"""How long will this design take to solve, and will it converge at all?

The recommender optimised electromagnetic metrics and knew nothing about cost, which is why it once
proposed a design that solved for twenty-seven minutes and then failed the convergence gate. Cost is
not guesswork here: every finished run records ``total_seconds`` and its status, so a small
regression over the measured points gives an estimate good enough to keep a batch inside a budget.

Design choices, deliberately simple:

* **ridge regression on the measured points**, in normalised parameter space - a handful of points
  does not justify a Gaussian process for a side signal, and a linear fit is explainable;
* **the failure rate is reported separately**, because "it will take twenty minutes" is only half an
  answer if half the neighbouring runs failed to converge;
* **when there is not enough data the estimate is the median**, flagged as such, never a silent guess.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence
import math
import re

import numpy as np

from .risk import failure_kind

__all__ = ["CostEstimate", "fit_cost_model", "estimate", "measured_costs"]

MIN_FIT_POINTS = 4
REGULARISATION = 1e-2


def _failure_stage(row: dict[str, Any]) -> str:
    failure = row.get("failure") or {}
    explicit = row.get("failure_stage") or (failure.get("stage") if isinstance(failure, dict) else None)
    stage = str(explicit or row.get("stage") or "").upper()
    if stage and stage not in {"DONE", "FAILED", "STALLED", "TIMEOUT"}:
        return stage
    # Terminal rows often lost the active stage. Infer only unambiguous error
    # categories, preserving unknown rather than inventing a solver duration.
    code = str(row.get("error_code") or "").upper()
    if re.search(r"CONNECT|NO_AEDT|AMBIGUOUS_AEDT", code):
        return "CONNECTING"
    if re.search(r"EXPORT|REPORT|E_FF_", code):
        return "EXPORTING"
    if re.search(r"GEOMETRY|INVALID_PARAMS|MODEL_FAILED", code):
        return "MODEL"
    if "NOT_CONVERGED" in code:
        return "SOLVING"
    return "unknown"


@dataclass
class CostEstimate:
    seconds: float
    spread: float
    samples: int
    method: str
    failure_rate: float = 0.0
    conservative_seconds: float | None = None
    lower_bound_seconds: float = 0.0
    censored_samples: int = 0
    failure_groups: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"seconds": round(float(self.seconds), 1),
                "minutes": round(float(self.seconds) / 60.0, 1),
                "spread_seconds": round(float(self.spread), 1),
                "samples": int(self.samples),
                "method": self.method,
                "failure_rate": round(float(self.failure_rate), 3),
                "conservative_seconds": (round(self.conservative_seconds, 1)
                                         if self.conservative_seconds is not None else None),
                "upper_seconds": (round(self.conservative_seconds, 1)
                                  if self.conservative_seconds is not None else None),
                "lower_bound_seconds": round(self.lower_bound_seconds, 1),
                "censored_samples": self.censored_samples,
                "known": bool(self.samples), "failure_groups": self.failure_groups,
                "cost_basis": "completed workflow duration; timeouts are right-censored lower bounds",
                "uncertainty_basis": "LOO residual/fallback spread; conservative heuristic, not calibrated coverage"}


@dataclass
class CostModel:
    weights: np.ndarray | None = None
    low: np.ndarray | None = None
    high: np.ndarray | None = None
    spread: float = 0.0
    samples: int = 0
    median: float = 0.0
    failure_rate: float = 0.0
    names: tuple[str, ...] = field(default_factory=tuple)
    upper_baseline: float = 0.0
    censored: list[tuple[list[float], float]] = field(default_factory=list)
    failure_groups: dict[str, Any] = field(default_factory=dict)

    @property
    def fitted(self) -> bool:
        return self.weights is not None and self.low is not None and self.high is not None

    def predict(self, params: dict[str, float]) -> CostEstimate:
        try:
            vector = np.array([float(params[name]) for name in self.names], dtype=float)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("cost prediction requires all named finite parameters") from exc
        if not np.all(np.isfinite(vector)):
            raise ValueError("cost prediction requires finite parameters")
        lower = nearby_timeout = 0.0
        for point, elapsed in self.censored:
            exact = np.allclose(point, vector, rtol=0, atol=1e-9)
            if self.low is None or self.high is None:
                nearby = exact
            else:
                width = np.where(self.high - self.low > 1e-12, self.high - self.low, 1.0)
                nearby = np.linalg.norm((np.asarray(point) - vector) / width) <= 0.25
            if nearby:
                nearby_timeout = max(nearby_timeout, elapsed)
            if exact:
                lower = max(lower, elapsed)
        if not self.fitted:
            seconds, method, outside = self.median, "median", 0.0
        else:
            span = np.where(np.abs(self.high - self.low) < 1e-12, 1.0, self.high - self.low)
            normalised = (vector - self.low) / span
            design = np.concatenate([[1.0], normalised])
            seconds, method = max(1.0, float(design @ self.weights)), "ridge"
            outside = float(np.linalg.norm(np.maximum(0, np.maximum(-normalised, normalised - 1))))
        upper = (max(lower, nearby_timeout, self.upper_baseline, seconds + 1.645 * self.spread * (1 + outside))
                 if self.samples else None)
        return CostEstimate(seconds=seconds, spread=self.spread, samples=self.samples,
                            method=method, failure_rate=self.failure_rate,
                            conservative_seconds=upper, lower_bound_seconds=lower,
                            censored_samples=len(self.censored), failure_groups=self.failure_groups)


def measured_costs(runs: Iterable[dict[str, Any]],
                   names: Sequence[str]) -> tuple[np.ndarray, np.ndarray, list[str], float]:
    """Rows of parameters, their measured solve seconds, their run ids and the failure rate."""
    xs: list[list[float]] = []
    seconds: list[float] = []
    ids: list[str] = []
    failed = 0
    total = 0
    for run in runs:
        status = str(run.get("status") or "").upper()
        if status not in {"COMPLETED", "FAILED", "MODEL_FAILED", "TIMEOUT", "STALLED",
                          "BUDGET_EXCEEDED", "RECOVERY_REQUIRED"}:
            continue
        total += 1
        if status != "COMPLETED":
            failed += 1
        cost = run.get("total_seconds")
        params = run.get("params") or {}
        try:
            seconds_value = float(cost)
            row = [float(params[name]) for name in names]
        except (KeyError, TypeError, ValueError):
            continue
        if status != "COMPLETED" or seconds_value <= 0 or not math.isfinite(seconds_value) \
                or not all(math.isfinite(value) for value in row):
            continue
        xs.append(row)
        seconds.append(seconds_value)
        ids.append(str(run.get("run_id")))
    rate = (failed / total) if total else 0.0
    return (np.asarray(xs, dtype=float), np.asarray(seconds, dtype=float), ids, rate)


def fit_cost_model(runs: Iterable[dict[str, Any]], names: Sequence[str]) -> CostModel:
    """Fit the (small) cost regression; fall back to the median when data is thin."""
    rows = list(runs)
    x, y, _ids, failure_rate = measured_costs(rows, names)
    censored: list[tuple[list[float], float]] = []
    failure_groups: dict[str, Any] = {}
    for row in rows:
        if str(row.get("status") or "").upper() not in {
                "FAILED", "MODEL_FAILED", "TIMEOUT", "STALLED", "BUDGET_EXCEEDED", "RECOVERY_REQUIRED"}:
            continue
        try:
            elapsed = float(row.get("total_seconds"))
            point = [float((row.get("params") or {})[name]) for name in names]
        except (KeyError, TypeError, ValueError):
            continue
        if elapsed <= 0 or not math.isfinite(elapsed) or not all(map(math.isfinite, point)):
            continue
        kind = failure_kind(row)
        if kind == "censored":
            censored.append((point, elapsed))
        stage = _failure_stage(row)
        group = failure_groups.setdefault(kind + ":" + stage,
                                          {"samples": 0, "total_seconds": 0.0, "max_seconds": 0.0})
        group["samples"] += 1
        group["total_seconds"] += elapsed
        group["max_seconds"] = max(group["max_seconds"], elapsed)
    extras = {"censored": censored, "failure_groups": failure_groups}
    if len(y) == 0:
        return CostModel(median=0.0, spread=0.0, samples=0, failure_rate=failure_rate,
                         names=tuple(names), **extras)
    low = np.min(x, axis=0)
    high = np.max(x, axis=0)
    varying = high - low > 1e-12
    span = np.where(varying, high - low, 1.0)
    normalised = (x - low) / span
    design = np.hstack([np.ones((len(x), 1)), normalised])
    upper_baseline = float(np.quantile(y, 0.9))
    if len(y) >= max(MIN_FIT_POINTS, int(np.sum(varying)) + 2) and np.any(varying):
        penalty = REGULARISATION * np.eye(design.shape[1])
        penalty[0, 0] = 0.0
        weights = np.linalg.solve(design.T @ design + penalty, design.T @ y)
        # Leave-one-out residuals expose interpolation overconfidence. A small
        # floor reflects unmodelled run-to-run overhead even on an exact trend.
        residuals = []
        for index in range(len(y)):
            keep = np.arange(len(y)) != index
            fit = np.linalg.solve(design[keep].T @ design[keep] + penalty,
                                  design[keep].T @ y[keep])
            residuals.append(float(y[index] - design[index] @ fit))
        spread = max(float(np.sqrt(np.mean(np.square(residuals)))), 0.05 * float(np.median(y)))
        return CostModel(weights=weights, low=low, high=high, spread=spread, samples=len(y),
                         median=float(np.median(y)), failure_rate=failure_rate,
                         names=tuple(names), upper_baseline=upper_baseline, **extras)
    return CostModel(low=low, high=high, spread=max(float(np.std(y)), 0.1 * float(np.median(y))),
                     samples=len(y), median=float(np.median(y)), failure_rate=failure_rate,
                     names=tuple(names), upper_baseline=upper_baseline, **extras)


def estimate(runs: Iterable[dict[str, Any]], names: Sequence[str],
             params: dict[str, float]) -> CostEstimate:
    """Convenience wrapper: the estimated cost of one candidate."""
    return fit_cost_model(runs, names).predict(params)
