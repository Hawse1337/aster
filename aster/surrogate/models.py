"""Surrogate models with a single interface (module B).

``predict`` returns the mean *and* the standard deviation so acquisition
functions can trade exploitation against exploration. Predictions are always
tagged ``source="predicted"`` by the caller; they must never be written into
measured metrics (the guard only accepts real HFSS evidence).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence

import numpy as np


class SurrogateModel(Protocol):
    def fit(self, x: np.ndarray, y: np.ndarray) -> "SurrogateModel": ...

    def predict(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]: ...


@dataclass
class ModelReport:
    model: str
    samples: int
    rmse: float
    mae: float
    coverage: float  # fraction of validation points inside 2 sigma
    validation: str = "unvalidated"
    validated: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        finite = lambda value: float(value) if np.isfinite(value) else None
        return {"model": self.model, "samples": self.samples, "rmse": finite(self.rmse),
                "mae": finite(self.mae), "coverage": finite(self.coverage),
                "validation": self.validation, "validated": self.validated, "reason": self.reason,
                "coverage_basis": "held-out residual within mean +/- 2 predictive standard deviations",
                "nominal_gaussian_coverage": 0.9545}


class GaussianProcessModel:
    """GPR with a Matern kernel; exposes predictive std for acquisition."""

    name = "gpr"

    def __init__(self, length_scale: float = 0.3, noise: float = 1e-3,
                 *, restarts: int = 1):
        self.length_scale = length_scale
        self.noise = noise
        self.restarts = restarts
        self._model = None

    def fit(self, x: np.ndarray, y: np.ndarray) -> "GaussianProcessModel":
        from sklearn.gaussian_process import GaussianProcessRegressor  # noqa: PLC0415
        from sklearn.gaussian_process.kernels import Matern, WhiteKernel  # noqa: PLC0415
        from sklearn.preprocessing import StandardScaler  # noqa: PLC0415

        x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
        if x.ndim != 2 or y.ndim != 1 or len(x) != len(y) or not len(y):
            raise ValueError("fit requires nonempty X[N,D] and y[N]")
        if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
            raise ValueError("training data must be finite")
        self._x_scaler = StandardScaler().fit(x)
        y_mean, y_std = float(y.mean()), float(y.std(ddof=0)) or 1.0
        self._y_mean, self._y_std = y_mean, y_std
        kernel = Matern(length_scale=self.length_scale, nu=2.5) + WhiteKernel(noise_level=self.noise)
        self._model = GaussianProcessRegressor(kernel=kernel, normalize_y=True,
                                               n_restarts_optimizer=self.restarts, random_state=0)
        self._model.fit(self._x_scaler.transform(x), y)
        return self

    def predict(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if self._model is None:
            raise RuntimeError("model is not fitted")
        mean, std = self._model.predict(self._x_scaler.transform(np.atleast_2d(x)),
                                        return_std=True)
        return np.asarray(mean, dtype=float), np.asarray(std, dtype=float)


class RBFModel:
    """Cheap RBF interpolation with a distance-based uncertainty proxy."""

    name = "rbf"

    def fit(self, x: np.ndarray, y: np.ndarray) -> "RBFModel":
        from scipy.interpolate import RBFInterpolator  # noqa: PLC0415

        self._x = np.asarray(x, dtype=float)
        self._y = np.asarray(y, dtype=float)
        self._model = RBFInterpolator(self._x, self._y, kernel="thin_plate_spline")
        self._scale = float(np.median(np.linalg.norm(self._x - self._x.mean(axis=0), axis=1))) or 1.0
        return self

    def predict(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        x = np.atleast_2d(np.asarray(x, dtype=float))
        mean = np.asarray(self._model(x), dtype=float)
        distances = np.min(np.linalg.norm(x[:, None, :] - self._x[None, :, :], axis=2), axis=1)
        return mean, distances / self._scale


def cross_validate(model: SurrogateModel, x: np.ndarray, y: np.ndarray,
                   folds: int = 4, *, groups: Sequence[Any] | None = None,
                   times: Sequence[Any] | None = None) -> ModelReport:
    """Out-of-sample group validation, or forward validation when times are supplied.

    Repeated geometries stay together. Too little independent evidence is reported
    as unvalidated; training residuals are never presented as validation error.
    """
    import copy

    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    n = len(y)
    if x.ndim != 2 or y.ndim != 1 or len(x) != n or not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
        raise ValueError("validation requires finite X[N,D] and y[N]")
    if groups is not None and len(groups) != n:
        raise ValueError("groups must have one value per observation")
    if times is not None and len(times) != n:
        raise ValueError("times must have one value per observation")
    # Even caller groups may not split exact duplicate designs across folds.
    labels = [repr(group) for group in groups] if groups is not None else [repr(tuple(row)) for row in x]
    parents = {label: label for label in labels}
    def root(label):
        while parents[label] != label:
            parents[label] = parents[parents[label]]
            label = parents[label]
        return label
    by_x: dict[tuple, str] = {}
    for row, label in zip(x, labels):
        previous = by_x.setdefault(tuple(row), label)
        parents[root(label)] = root(previous)
    labels = [root(label) for label in labels]
    unique = list(dict.fromkeys(labels))
    if len(unique) < 2:
        return ModelReport(getattr(model, "name", type(model).__name__), n,
                           float("nan"), float("nan"), float("nan"),
                           reason="not enough independent groups for held-out validation")
    splits = []
    if times is not None:
        ordered = sorted(unique, key=lambda label: min(times[i] for i, value in enumerate(labels) if value == label))
        for block in np.array_split(np.asarray(ordered, dtype=object), min(max(2, int(folds)), len(ordered))):
            test_labels = set(block.tolist())
            if not test_labels:
                continue
            cutoff = min(times[i] for i, label in enumerate(labels) if label in test_labels)
            train = np.array([i for i, label in enumerate(labels) if label not in test_labels and times[i] < cutoff], dtype=int)
            test = np.array([i for i, label in enumerate(labels) if label in test_labels], dtype=int)
            if len(train) >= 2:
                splits.append((train, test))
        mode = "forward_group"
    else:
        order = np.asarray(unique, dtype=object)
        np.random.RandomState(0).shuffle(order)
        for block in np.array_split(order, min(max(2, int(folds)), max(1, len(order)))):
            test_labels = set(block.tolist())
            train = np.array([i for i, label in enumerate(labels) if label not in test_labels], dtype=int)
            test = np.array([i for i, label in enumerate(labels) if label in test_labels], dtype=int)
            if len(train) >= 2 and len(test):
                splits.append((train, test))
        mode = "group_holdout"
    errors: list[float] = []
    abs_errors: list[float] = []
    inside: list[bool] = []
    for train, test in splits:
        fitted = copy.deepcopy(model).fit(x[train], y[train])
        mean, std = fitted.predict(x[test])
        errors.extend((mean - y[test]).tolist())
        abs_errors.extend(np.abs(mean - y[test]).tolist())
        inside.extend((np.abs(mean - y[test]) <= 2 * np.maximum(std, 1e-9)).tolist())
    return ModelReport(
        model=getattr(model, "name", type(model).__name__),
        samples=n,
        rmse=float(np.sqrt(np.mean(np.square(errors)))) if errors else float("nan"),
        mae=float(np.mean(abs_errors)) if errors else float("nan"),
        coverage=float(np.mean(inside)) if inside else float("nan"),
        validation=mode if errors else "unvalidated", validated=len(errors),
        reason="" if errors else "not enough independent groups for held-out validation",
    )


def model_is_usable(report: ModelReport, y_range: float, max_relative_rmse: float = 0.2,
                    *, minimum_coverage: float = 0.6) -> bool:
    """Check held-out error and empirical interval coverage; calibration is not assumed."""
    if not np.isfinite(minimum_coverage) or not 0 <= minimum_coverage <= 1:
        raise ValueError("minimum_coverage must be finite and between zero and one")
    scale = abs(float(y_range)) or 1.0
    return (np.isfinite(report.rmse) and report.rmse / scale <= max_relative_rmse
            and np.isfinite(report.coverage) and report.coverage >= minimum_coverage
            and report.samples >= 8 and report.validated >= 2)
