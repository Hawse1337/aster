"""Design-of-experiments baseline: cover the parameter box before asking the optimiser to refine.

The recommendation engine can only be as good as the region it has seen; most of our campaigns started
from one or two hand-picked points, so a large part of the seven-dimensional box was never measured.
This module plans a stratified baseline (Latin hypercube), removes duplicates by the same config hash
the distributed scheduler uses, and skips designs the campaign already evaluated - so it is safe to
resume after a crash or to run several machines against one plan.

It is a planner only: execution goes through the normal queue, which keeps every discipline (attempts,
budgets, recovery) in one place.
"""
from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .core import ParamSpace, latin_hypercube
from .distributed import config_hash

__all__ = ["DoePlan", "plan_doe", "already_measured", "load_points"]


@dataclass
class DoePlan:
    case: str
    count: int
    seed: int
    points: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    measurements: dict[str, dict[str, Any]] = field(default_factory=dict)
    learning_contract: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"case": self.case, "count": self.count, "seed": self.seed,
                "points": list(self.points),
                "measured": len(self.measurements),
                "planned": len(self.points),
                "skipped": list(self.skipped),
                "learning_contract": self.learning_contract,
                "estimated_hours": round(len(self.points) * 0.42, 2),
                "estimate_basis": "planning hint: 25.2 minutes/point; not a measured budget guarantee"}

    @property
    def hashes(self) -> list[str]:
        return [_identity(self.case, point, self.learning_contract) for point in self.points]


def _identity(case, params, contract=None):
    if not contract:
        return config_hash(case, params)
    return hashlib.sha256(json.dumps({"case": case, "params": params, "contract": contract},
                                    sort_keys=True, default=str).encode()).hexdigest()[:32]


def already_measured(case: str, params_iter: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Existing measurements keyed by config hash (the distributed scheduler's identity)."""
    measured: dict[str, dict[str, Any]] = {}
    for entry in params_iter:
        params = entry.get("params") if "params" in entry else entry
        if not isinstance(params, dict):
            continue
        measured.setdefault(config_hash(case, params), entry)
    return measured


def mean_nearest_neighbour(points: list[dict[str, Any]], ignored: Iterable[str] = ()) -> float:
    """A crude spread check: larger is better; useful to compare two DOEs before spending hours."""
    if len(points) < 2:
        return 0.0
    keys = [key for key in points[0] if key not in set(ignored)]
    values = [[float(point.get(key, 0.0)) for key in keys] for point in points]
    spans = [max(row[index] for row in values) - min(row[index] for row in values) for index in
             range(len(keys))]
    normalised = [[(row[index] - min(r[index] for r in values)) / (span or 1.0)
                   for index, span in enumerate(spans)] for row in values]
    total = 0.0
    for index, row in enumerate(normalised):
        distances = [sum((row[j] - other[j]) ** 2 for j in range(len(keys))) ** 0.5
                     for position, other in enumerate(normalised) if position != index]
        total += min(distances)
    return total / len(normalised)


def plan_doe(space: ParamSpace, case: str, count: int = 10, seed: int = 0,
             measured: dict[str, dict[str, Any]] | None = None,
             attempts: int = 4, *, validator=None, learning_contract=None) -> DoePlan:
    """Stratified points that are new for this case; deterministic for a given seed."""
    count = int(count)
    if count < 1:
        raise ValueError("count must be positive")
    measured = measured or {}
    plan = DoePlan(case=case, count=count, seed=seed, measurements=dict(measured))
    plan.learning_contract = dict(learning_contract or {})
    seen: set[str] = set(measured)
    candidate_seed = seed
    for _attempt in range(int(attempts)):
        candidates = latin_hypercube(space, count, seed=candidate_seed)
        for point in candidates:
            point = space.validate({spec.name: spec.denormalize(spec.normalize(point[spec.name]))
                                    for spec in space})
            if validator is not None:
                try:
                    valid = bool(validator(point))
                except Exception:
                    valid = False
                if not valid:
                    plan.skipped.append({"params": point, "reason": "invalid_geometry"})
                    continue
            identity = _identity(case, point, plan.learning_contract)
            if identity in seen:
                plan.skipped.append({"hash": identity, "params": point, "reason": "measured"})
                continue
            seen.add(identity)
            plan.points.append(point)
            if len(plan.points) >= count:
                return plan
        candidate_seed += 1
    return plan


def enqueue_plan(store: Any, plan_or_points: Any, case: str, batch_id: str, *, learning_contract=None) -> dict[str, Any]:
    """Push the still-unmeasured points of a plan into a campaign queue; returns a summary.

    Identity is the same config hash the distributed scheduler uses, so re-running this after a crash
    or from another machine cannot double-queue a design.
    """
    points = list(getattr(plan_or_points, "points", plan_or_points) or [])
    contract = dict(learning_contract or getattr(plan_or_points, "learning_contract", {}) or {})
    existing: dict[str, Any] = {}
    for row in store.jobs():
        if (row.get("case") or row.get("case_name")) != case:
            continue
        if contract:
            from .learning_contract import compatibility
            if not compatibility(row, contract)[0]:
                continue
        params = row.get("params")
        # a FAILED row was not measured: it must be retryable, otherwise an environment hiccup
        # permanently removes a design from the baseline
        if isinstance(params, dict) and points and str(row.get("status")) != "FAILED":
            existing.setdefault(config_hash(case, params), row)
    fresh = [point for point in points if config_hash(case, point) not in existing]
    if fresh:
        run_ids = store.enqueue(fresh, case, batch_id, learning_contract=contract) if contract else store.enqueue(fresh, case, batch_id)
    else:
        run_ids = []
    return {"case": case, "batch_id": batch_id, "planned": len(points),
            "enqueued": len(fresh), "skipped_existing": len(points) - len(fresh),
            "run_ids": list(run_ids)}


def load_points(path: str | Path, *, expected_contract=None) -> list[dict[str, Any]]:
    """Read a plan/ledger JSON and return its parameter dicts (for resuming)."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if expected_contract is not None and payload.get("learning_contract") != expected_contract:
        raise ValueError("DoE plan belongs to a different or unversioned learning contract")
    entries = payload.get("points") or payload.get("records") or payload.get("runs") or []
    return [entry.get("params") if "params" in entry else entry for entry in entries]
