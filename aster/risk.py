# -*- coding: utf-8 -*-
"""Candidate-level failure risk: the failure rate of the runs nearest to a proposal.

A global failure rate says "one in five runs fails"; it does not say *which* one. The measured runs
do carry that information locally, because a proposal sits somewhere in parameter space and the runs
around it are the best available evidence for what happens there.

Operational failures retain the legacy empirical rate. Separately, labelled
physical failures use a smoothed rate and uncertainty interval; infrastructure
failures, time limits and unlabelled legacy failures are never bad-geometry
labels. Missing physical evidence is explicitly unknown.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence
import math
import re

import numpy as np

__all__ = ["RiskEstimate", "NeighbourRisk", "fit_risk_model", "measured_outcomes", "failure_kind"]


def failure_kind(run: dict[str, Any]) -> str:
    """Classify evidence without treating an infrastructure fault as a bad geometry.

    Time limits are censored observations, not a convergence failure. Unlabelled
    legacy FAILED rows remain unknown and are retained in the operational rate.
    """
    status = str(run.get("status") or "").upper()
    if status == "COMPLETED":
        return "success"
    failure = run.get("failure") or {}
    detail = " ".join(str(run.get(key) or "") for key in
                      ("error_code", "error_detail", "failure_stage", "stage"))
    if isinstance(failure, dict):
        detail += " " + str(failure.get("code") or "")
    detail = (status + " " + detail).upper()
    if re.search(r"BUDGET|TIMEOUT|TIMED.OUT|STALLED|TIME.LIMIT", detail):
        return "censored"
    if re.search(r"E_NOT_CONVERGED|GEOMETRY_REJECTED|E_GEOMETRY|INVALID_PARAMS", detail):
        return "physical"
    if re.search(r"LICENSE|LICENCE|SESSION|CONNECT|NO_AEDT|GRPC|SWEEP_EXPORT|REPORT|"
                 r"DISK|SQLITE|TRANSPORT|WORKER|RECOVERY_REQUIRED", detail):
        return "environment"
    return "unknown"


def _interval(failures: int, count: int) -> tuple[float, float]:
    """Wilson interval; wide intervals deliberately expose thin evidence."""
    if not count:
        return 0.0, 1.0
    z = 1.96
    p = failures / count
    denominator = 1 + z * z / count
    center = (p + z * z / (2 * count)) / denominator
    half = z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / denominator
    return max(0.0, center - half), min(1.0, center + half)

DEFAULT_NEIGHBOURS = 5
NEARBY_FRACTION = 0.25
"""A neighbour counts as "near" when it is within a quarter of the diagonal of the measured box."""


@dataclass
class RiskEstimate:
    failure_rate: float
    neighbours: int
    method: str
    global_rate: float = 0.0
    known: bool = False
    physical_failure_rate: float | None = None
    environment_failure_rate: float | None = None
    risk_lower: float = 0.0
    risk_upper: float = 1.0
    physical_samples: int = 0
    environment_samples: int = 0
    censored_samples: int = 0
    unknown_samples: int = 0
    local_known: bool = False
    global_prior: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"failure_rate": round(float(self.failure_rate), 3),
                "neighbours": int(self.neighbours), "method": self.method,
                "global_rate": round(float(self.global_rate), 3),
                "known": self.known, "physical_failure_rate": self.physical_failure_rate,
                "environment_failure_rate": self.environment_failure_rate,
                "risk_lower": self.risk_lower, "risk_upper": self.risk_upper,
                "physical_samples": self.physical_samples,
                "environment_samples": self.environment_samples,
                "censored_samples": self.censored_samples, "unknown_samples": self.unknown_samples,
                "local_known": self.local_known, "global_prior": self.global_prior,
                "failure_rate_basis": "observed operational failures (all causes)",
                "physical_rate_basis": "Laplace smoothed; interval is 95% Wilson"}


def measured_outcomes(runs: Iterable[dict[str, Any]],
                      names: Sequence[str]) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Parameters of every run that has them, and whether it failed."""
    xs: list[list[float]] = []
    failed: list[float] = []
    ids: list[str] = []
    for run in runs:
        status = str(run.get("status") or "").upper()
        if status not in {"COMPLETED", "FAILED", "MODEL_FAILED", "TIMEOUT", "STALLED",
                          "BUDGET_EXCEEDED", "RECOVERY_REQUIRED"}:
            continue
        params = run.get("params") or {}
        try:
            row = [float(params[name]) for name in names]
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in row):
            continue
        xs.append(row)
        failed.append(0.0 if status == "COMPLETED" else 1.0)
        ids.append(str(run.get("run_id")))
    return (np.asarray(xs, dtype=float), np.asarray(failed, dtype=float), ids)


@dataclass
class NeighbourRisk:
    x: np.ndarray | None = None
    failed: np.ndarray | None = None
    low: np.ndarray | None = None
    high: np.ndarray | None = None
    names: tuple[str, ...] = ()
    neighbours: int = DEFAULT_NEIGHBOURS
    global_rate: float = 0.0
    kinds: tuple[str, ...] = ()

    def _estimate(self, indices: Sequence[int], method: str, neighbours: int) -> RiskEstimate:
        selected = [self.kinds[index] for index in indices]
        count = sum(kind in {"success", "physical"} for kind in selected)
        failures = selected.count("physical")
        low, high = _interval(failures, count)
        local_known = method == "neighbours" and count > 0
        global_count = sum(kind in {"success", "physical"} for kind in self.kinds)
        global_failures = self.kinds.count("physical")
        global_low, global_high = _interval(global_failures, global_count)
        prior = ({"physical_failure_rate": (global_failures + 1) / (global_count + 2),
                  "physical_samples": global_count,
                  "risk_lower": global_low, "risk_upper": global_high}
                 if global_count else None)
        operational = (float(np.mean(self.failed[list(indices)])) if len(indices)
                       else self.global_rate)
        return RiskEstimate(
            failure_rate=operational, neighbours=neighbours, method=method,
            global_rate=self.global_rate, known=local_known, local_known=local_known,
            global_prior=prior,
            physical_failure_rate=(failures + 1) / (count + 2) if local_known else None,
            environment_failure_rate=selected.count("environment") / len(selected) if selected else None,
            risk_lower=low if local_known else 0.0, risk_upper=high if local_known else 1.0,
            physical_samples=count if method == "neighbours" else 0,
            environment_samples=selected.count("environment"),
            censored_samples=selected.count("censored"), unknown_samples=selected.count("unknown"))

    @property
    def fitted(self) -> bool:
        return self.x is not None and self.low is not None

    def predict(self, params: dict[str, float]) -> RiskEstimate:
        try:
            vector = np.array([float(params[name]) for name in self.names], dtype=float)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("risk prediction requires all named finite parameters") from exc
        if not np.all(np.isfinite(vector)):
            raise ValueError("risk prediction requires finite parameters")
        if not self.fitted:
            return self._estimate([], "global", 0)
        span = np.where(np.abs(self.high - self.low) < 1e-12, 1.0, self.high - self.low)
        point = (vector - self.low) / span
        points = (self.x - self.low) / span
        distances = np.linalg.norm(points - point, axis=1)
        order = np.argsort(distances)[: max(1, int(self.neighbours))]
        near = order[distances[order] <= NEARBY_FRACTION] if len(order) else []
        if len(near) == 0:
            return self._estimate(list(range(len(self.kinds))), "global", 0)
        return self._estimate(list(near), "neighbours", len(near))


def fit_risk_model(runs: Iterable[dict[str, Any]], names: Sequence[str],
                   neighbours: int = DEFAULT_NEIGHBOURS) -> NeighbourRisk:
    rows = list(runs)
    usable = [row for row in rows if len(measured_outcomes([row], names)[0])]
    x, failed, _ids = measured_outcomes(usable, names)
    kinds = tuple(failure_kind(row) for row in usable)
    if len(x) == 0:
        return NeighbourRisk(names=tuple(names), neighbours=neighbours, global_rate=0.0)
    return NeighbourRisk(x=x, failed=failed, low=np.min(x, axis=0), high=np.max(x, axis=0),
                         names=tuple(names), neighbours=neighbours,
                         global_rate=float(np.mean(failed)), kinds=kinds)
