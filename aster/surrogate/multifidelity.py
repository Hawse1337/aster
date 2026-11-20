"""Multi-fidelity surrogates (S3).

Low-fidelity measurements (coarse mesh, fewer adaptive passes, analytic
estimates) are cheap but biased. The additive multi-fidelity model (Forrester
et al.) learns the low-fidelity response and a *discrepancy* between high and
low, so cheap samples improve the prediction of the expensive metric without
being reported as high-fidelity measurements.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

LOW = "low"
HIGH = "high"


@dataclass
class MultiFidelitySample:
    x: list[float]
    y: float
    fidelity: str
    candidate_id: str = ""
    source: str = "measured"

    def __post_init__(self) -> None:
        if self.fidelity not in {LOW, HIGH}:
            raise ValueError(f"unknown fidelity: {self.fidelity}")


class MultiFidelityModel:
    """Additive discrepancy model: y_high(x) = rho * y_low(x) + delta(x)."""

    name = "mf_additive"

    def __init__(self, rho: float = 1.0):
        self.rho = rho
        self._low = None
        self._delta = None
        self.n_low = 0
        self.n_high = 0

    def fit(self, samples: Sequence[MultiFidelitySample]) -> "MultiFidelityModel":
        from sklearn.gaussian_process import GaussianProcessRegressor  # noqa: PLC0415
        from sklearn.gaussian_process.kernels import Matern, WhiteKernel  # noqa: PLC0415
        from sklearn.preprocessing import StandardScaler  # noqa: PLC0415

        # Every fit owns its components; a previous low-fidelity GP must not
        # survive when the new dataset contains high-fidelity observations only.
        self._low, self._delta = None, None
        self._paired_discrepancy = False
        low = [s for s in samples if s.fidelity == LOW]
        high = [s for s in samples if s.fidelity == HIGH]
        if not high:
            raise ValueError("the multi-fidelity model needs at least one high-fidelity sample")
        self.n_low, self.n_high = len(low), len(high)
        self._scaler = StandardScaler().fit(np.array([s.x for s in samples], dtype=float))
        kernel = Matern(length_scale=0.5, nu=2.5) + WhiteKernel(noise_level=1e-3)
        if low:
            self._low = GaussianProcessRegressor(kernel=kernel, normalize_y=True,
                                                 random_state=0)
            self._low.fit(self._scaler.transform(np.array([s.x for s in low])),
                          np.array([s.y for s in low]))
            predicted_low = self._low.predict(self._scaler.transform(
                np.array([s.x for s in high])
            ))
            # For truly paired data the discrepancy must use the observed coarse
            # result, rather than absorb the low GP's interpolation error.
            observed = {}
            for sample in low:
                observed.setdefault(tuple(sample.x), []).append(float(sample.y))
            self._paired_discrepancy = all(tuple(sample.x) in observed for sample in high)
            predicted_low = np.array([
                np.mean(observed[tuple(sample.x)]) if tuple(sample.x) in observed else prediction
                for sample, prediction in zip(high, predicted_low)
            ])
            residual = np.array([s.y for s in high]) - self.rho * predicted_low
        else:
            residual = np.array([s.y for s in high])
        self._delta = GaussianProcessRegressor(kernel=kernel, normalize_y=True,
                                               random_state=0)
        self._delta.fit(self._scaler.transform(np.array([s.x for s in high])), residual)
        return self

    def predict(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if self._delta is None:
            raise RuntimeError("multi-fidelity model is not fitted")
        x = np.atleast_2d(np.asarray(x, dtype=float))
        scaled = self._scaler.transform(x)
        if self._low is not None:
            low_mean, low_std = self._low.predict(scaled, return_std=True)
            base = self.rho * low_mean
            low_variance = (self.rho * low_std) ** 2
        else:
            base = np.zeros(len(x))
            low_variance = np.zeros(len(x))
        delta_mean, delta_std = self._delta.predict(scaled, return_std=True)
        # These are independently fitted additive components, not a correlated co-kriging posterior.
        return base + delta_mean, np.sqrt(low_variance + np.maximum(delta_std, 0.0) ** 2)

    def predict_from_low(self, x: np.ndarray, observed_low: np.ndarray
                         ) -> tuple[np.ndarray, np.ndarray]:
        """Correct measured coarse values with a discrepancy trained on pairs.

        The returned uncertainty describes the fitted discrepancy only. It is not
        a calibrated confidence bound and must not, by itself, authorize screening.
        """
        if self._delta is None or not self._paired_discrepancy:
            raise RuntimeError("observed-low correction requires paired training data")
        x = np.atleast_2d(np.asarray(x, dtype=float))
        low = np.asarray(observed_low, dtype=float).reshape(-1)
        if len(low) != len(x) or not np.all(np.isfinite(low)) or not np.all(np.isfinite(x)):
            raise ValueError("finite observed coarse values must match candidate rows")
        delta, std = self._delta.predict(self._scaler.transform(x), return_std=True)
        return self.rho * low + delta, std


@dataclass
class FidelityBudget:
    """Track how much of each fidelity tier is still affordable."""

    low_left: int
    high_left: int
    used_low: int = 0
    used_high: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)

    def take(self, fidelity: str, count: int = 1) -> bool:
        if fidelity == HIGH and self.high_left >= count:
            self.high_left -= count
            self.used_high += count
            self.history.append({"fidelity": HIGH, "count": count})
            return True
        if fidelity == LOW and self.low_left >= count:
            self.low_left -= count
            self.used_low += count
            self.history.append({"fidelity": LOW, "count": count})
            return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {"low_left": self.low_left, "high_left": self.high_left,
                "used_low": self.used_low, "used_high": self.used_high}


def high_fidelity_error(model: MultiFidelityModel, samples: Sequence[MultiFidelitySample]
                        ) -> float:
    high = [s for s in samples if s.fidelity == HIGH]
    if not high:
        return float("nan")
    mean, _ = model.predict(np.array([s.x for s in high], dtype=float))
    truth = np.array([s.y for s in high])
    return float(np.sqrt(np.mean((mean - truth) ** 2)))
