"""Executor over a surface fitted to measured candidates (S2/S3 bridge).

The ablation harness normally runs on the analytic problem or on real HFSS. This
module sits in between: it fits a Gaussian process to the measured candidates of a
family and lets the search strategies run on that fitted landscape, so algorithms
can be compared on the *shape* of the real problem without paying for every solve.

Predictions from this executor are labelled ``predicted`` — they must never be
reported as measurements.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

from ..core import CaseSpec
from ..optimize import Evaluation, constraint_violation, evaluate_constraints
from ..surrogate.models import GaussianProcessModel

try:  # exact interpolant keeps the measured values at the measured points
    from scipy.interpolate import RBFInterpolator as _RBF
except Exception:  # noqa: BLE001
    _RBF = None


PHYSICAL_FLOORS = {"s11_bandwidth": 0.0, "ripple": 0.0, "peak_gain": -20.0}


class _RBFModel:
    def __init__(self, x, y):
        self._interp = _RBF(x, y, kernel="thin_plate_spline", smoothing=0.0)

    def predict(self, x):
        values = self._interp(np.asarray(x, dtype=float))
        return values.reshape(-1, 1), np.zeros_like(values).reshape(-1, 1)


@dataclass
class MeasuredPoint:
    params: dict[str, float]
    metrics: dict[str, float]


def load_measured_points(path, base: dict[str, float] | None = None) -> list[MeasuredPoint]:
    """Load measured points from a JSON file written by the campaign scripts.

    The file stores per-point parameter overrides plus the measured metrics; the
    full parameter vector is rebuilt by merging ``base`` with each override.
    """
    import json
    from pathlib import Path

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    defaults = dict(base or {})
    points: list[MeasuredPoint] = []
    for entry in payload.get("points", []):
        params = dict(defaults)
        params.update({str(k): float(v) for k, v in (entry.get("params") or {}).items()})
        metrics = {str(k): float(v) for k, v in (entry.get("metrics") or {}).items()}
        points.append(MeasuredPoint(params=params, metrics=metrics))
    return points


@dataclass
class MeasuredSurfaceExecutor:
    """Evaluate candidates by interpolating measured points with a GP."""

    case: CaseSpec
    points: Sequence[MeasuredPoint]
    feature_names: Sequence[str] | None = None
    noise: float = 1e-6
    _models: dict[str, GaussianProcessModel] = field(default_factory=dict, init=False)
    calls: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.param_names = (list(self.feature_names) if self.feature_names
                            else [spec.name for spec in self.case.family.param_space])
        self._x = np.array([[float(point.params[name]) for name in self.param_names]
                            for point in self.points], dtype=float)
        kinds = {constraint.kind for constraint in self.case.constraints}
        kinds |= {objective.kind for objective in self.case.objectives}
        self._kinds = sorted(kinds)
        self._bounds: dict[str, tuple[float, float]] = {}
        for kind in self._kinds:
            values = [point.metrics.get(kind) for point in self.points]
            numbers = [float(v) for v in values if v is not None]
            if numbers:
                self._bounds[kind] = (min(numbers), max(numbers))
        # A GP smooths a handful of points, which erased the measured feasible
        # spike (bandwidth 3.0 GHz) and made every strategy look infeasible. The
        # executor therefore uses an exact RBF interpolant so the measured values
        # are reproduced at the measured points, with a GP as the fallback.
        for kind in self._kinds:
            values = [point.metrics.get(kind) for point in self.points]
            if any(value is None for value in values):
                continue
            y = np.array(values, dtype=float)
            if _RBF is not None and len(self._x) >= len(self._x[0]) + 1:
                self._models[kind] = _RBFModel(self._x, y)
            else:
                model = GaussianProcessModel()
                model.fit(self._x, y)
                self._models[kind] = model

    def predict(self, params: dict[str, Any]) -> dict[str, float]:
        vector = np.array([[float(params[name]) for name in self.param_names]])
        out: dict[str, float] = {}
        for kind, model in self._models.items():
            mean, _std = model.predict(vector)
            value = float(np.asarray(mean).reshape(-1)[0])
            # the RBF interpolant is unconstrained outside the measured box; clip
            # to the measured range widened by 50% so a prediction can never
            # claim a physically impossible bandwidth or gain
            low, high = self._bounds.get(kind, (None, None))
            if low is not None:
                span = high - low
                floor = low - 0.5 * span
                # physical floors: a negative bandwidth or ripple is meaningless and
                # would let an extrapolated point look feasible
                if kind in PHYSICAL_FLOORS:
                    floor = max(floor, PHYSICAL_FLOORS[kind])
                value = min(max(value, floor), high + 0.5 * span)
            out[kind] = value
        return out

    def evaluate(self, candidate: Any) -> Evaluation:
        self.calls += 1
        metrics = self.predict(candidate.params)
        feasible, violations, total = evaluate_constraints(metrics, self.case)
        return Evaluation(
            candidate_id=getattr(candidate, "candidate_id", "pred_%d" % self.calls),
            family_id=getattr(candidate, "family_id", self.case.family.family_id),
            params=dict(candidate.params),
            metrics=metrics,
            feasible=feasible,
            violations=violations,
            total_violation=total,
            run_id=None,
            status="PREDICTED",
            origin={"algorithm": "measured_surface"},
        )
