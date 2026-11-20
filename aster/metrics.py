"""Normalised hypervolume progress, following the convention yadof documents.

yadof's cost view plots a hypervolume band on every campaign: objectives are normalised so a fixed
reference point ``(1, ..., 1)`` is meaningful across tasks and generations, the upper edge is the
cumulative hypervolume of all recorded generations and the lower edge the current generation's own
hypervolume (see ``user_doc/config_and_run.md``, "History and tools" - the reference point is stated
as the fixed normalised-cost reference ``(1, ..., 1)``).

This module implements the same idea for our two objectives, without copying their code:

* normalise each objective between an ideal and a worst value, both derived from measured data
  (min and max of what has been measured), so ``(1, 1)`` is always "as bad as anything measured";
* compute exact 2-D hypervolume for minimisation by sweeping the sorted front;
* report a per-run curve: cumulative hypervolume after each measured run, plus that run's own
  contribution, which is what makes "progress stalled" visible in a campaign.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import numpy as np

__all__ = ["Normalisation", "hypervolume_2d", "normalisation_from_runs",
           "normalisation_from_case", "hypervolume_progress",
           "REFERENCE"]

REFERENCE = (1.0, 1.0)
"""Fixed reference point in normalised objective space (yadof's convention)."""


@dataclass
class Normalisation:
    ideal: tuple[float, ...]
    worst: tuple[float, ...]
    bounded: bool = True

    def scale(self, values: np.ndarray) -> np.ndarray:
        values = np.asarray(values, dtype=float)
        ideal = np.asarray(self.ideal, dtype=float)
        span = np.asarray(self.worst, dtype=float) - ideal
        span = np.where(np.abs(span) < 1e-12, 1.0, span)
        # below zero means "better than the task target": keep it, so hypervolume keeps rewarding
        # further improvement instead of saturating the moment a target is met
        scaled = (values - ideal) / span
        return np.clip(scaled, -1.0, 1.0) if self.bounded else scaled

    def to_dict(self) -> dict[str, list[float]]:
        return {"ideal": list(self.ideal), "worst": list(self.worst)}


def normalisation_from_runs(runs: Iterable[dict[str, Any]],
                            keys: Sequence[str] = ("s11_bandwidth", "peak_gain"),
                            margin: float = 0.0) -> Normalisation:
    """Ideal = best measured value, worst = worst measured value (plus margin when asked).

    All objectives are maximised in the campaign's own vocabulary (bandwidth, gain); they are
    negated here so that smaller is better, matching the minimisation the rest of the toolbox uses.
    """
    rows = []
    for run in runs:
        metrics = run.get("metrics") or {}
        try:
            values = [float(metrics[key]) for key in keys]
        except (KeyError, TypeError, ValueError):
            continue
        rows.append([-value for value in values])
    if not rows:
        return Normalisation(ideal=tuple(0.0 for _ in keys), worst=tuple(1.0 for _ in keys))
    data = np.asarray(rows, dtype=float)
    ideal = np.min(data, axis=0)
    worst = np.max(data, axis=0)
    if margin > 0:
        span = np.maximum(np.ptp(data, axis=0), 1e-9)
        worst = worst + float(margin) * span
    return Normalisation(ideal=tuple(float(value) for value in ideal),
                         worst=tuple(float(value) for value in worst))


def normalisation_from_case(case: Any,
                             keys: Sequence[str] = ("s11_bandwidth", "peak_gain")
                             ) -> Normalisation:
    """Ideal from the case's own targets, worst at zero - yadof normalises cost, not measurements.

    Measuring the extremes instead (``normalisation_from_runs``) lets a single bad design stretch the
    scale: one -5.4 dBi measurement made every other run saturate at hypervolume 1.0. The task's own
    targets are the stable reference the campaign is actually judged against.
    """
    ideal: list[float] = []
    worst: list[float] = []
    constraints = list(getattr(case, "constraints", ()) or ())
    objectives = list(getattr(case, "objectives", ()) or ())
    for key in keys:
        target = None
        for constraint in constraints:
            if str(getattr(constraint, "kind", "")) == key:
                target = float(getattr(constraint, "target", 0.0))
                break
        if target is None:
            for objective in objectives:
                if str(getattr(objective, "kind", "")) == key:
                    target = float(getattr(objective, "target", 0.0) or 0.0)
                    break
        objective = next((o for o in objectives if o.kind == key), None)
        if objective is not None and objective.direction == "minimize":
            # Fixed physical anchors, independent of which runs happen to be present.
            # Signed sidelobes are in dBc; lower (more negative) is better.
            lower, upper = (-30.0, 0.0) if key == "sll" else (0.0, max(1.0, 2 * abs(target or 1.0)))
            ideal.append(lower)
            worst.append(upper)
            continue
        if target is None or target <= 0:
            # no task target: fall back to the defaults the reference case uses
            target = {"s11_bandwidth": 3.0, "peak_gain": 6.0,
                      "directivity_horiz_db": 7.0}.get(key, 1.0)
        ideal.append(-abs(target))   # negated: the toolbox minimises
        worst.append(0.0)
    return Normalisation(ideal=tuple(ideal), worst=tuple(worst))


def hypervolume_2d(points: np.ndarray, reference: Sequence[float] = REFERENCE) -> float:
    """Exact 2-D hypervolume of minimisation points, clipped to the reference box."""
    values = np.asarray(points, dtype=float).reshape(-1, 2)
    ref_x, ref_y = float(reference[0]), float(reference[1])
    inside = values[(values[:, 0] < ref_x) & (values[:, 1] < ref_y)]
    if len(inside) == 0:
        return 0.0
    order = np.argsort(inside[:, 0])
    area = 0.0
    previous_y = ref_y
    for index in order:
        x, y = float(inside[index, 0]), float(inside[index, 1])
        if y >= previous_y:
            continue
        area += (ref_x - x) * (previous_y - y)
        previous_y = y
    return float(area)


def _volume(values: np.ndarray, reference: Sequence[float]) -> float:
    values = np.asarray(values, dtype=float)
    ref = np.asarray(reference, dtype=float)
    if not len(values):
        return 0.0
    values = values[np.all(np.isfinite(values), axis=1) & np.all(values < ref, axis=1)]
    if not len(values):
        return 0.0
    if len(ref) == 1:
        return float(ref[0] - np.min(values[:, 0]))
    if len(ref) == 2:
        return hypervolume_2d(values, ref)
    from pymoo.indicators.hv import HV
    return float(HV(ref_point=ref)(values))


def _case_feasible(metrics: dict[str, Any], case: Any) -> bool:
    for constraint in case.constraints:
        try:
            value = float(metrics[constraint.kind])
        except (KeyError, TypeError, ValueError):
            return False
        if not np.isfinite(value):
            return False
        target = float(constraint.target)
        if not {">=": value >= target, "<=": value <= target, "==": value == target}[constraint.operator]:
            return False
    return True


def hypervolume_progress(runs: Iterable[dict[str, Any]],
                         keys: Sequence[str] = ("s11_bandwidth", "peak_gain"),
                         normalisation: Normalisation | None = None,
                         reference: Sequence[float] = REFERENCE, *,
                         case: Any | None = None, contract: dict | None = None) -> dict[str, Any]:
    """Cumulative and per-run hypervolume after each measured run, oldest first.

    ``cumulative`` is the hypervolume of everything measured up to and including that run - a
    non-decreasing curve that makes a stalled campaign obvious. ``generation`` is the hypervolume
    that run alone adds (0 when it is dominated), comparable to yadof's lower band.
    """
    runs = list(runs)
    directions = ["maximize"] * len(keys)
    if case is not None:
        keys = [objective.kind for objective in case.objectives]
        directions = [objective.direction for objective in case.objectives]
        reference = [1.0] * len(keys)
        if normalisation is None:
            normalisation = normalisation_from_case(case, keys)
        normalisation = Normalisation(tuple(normalisation.ideal), tuple(normalisation.worst), bounded=False)
        if len(normalisation.ideal) != len(keys):
            raise ValueError("normalisation must match every case objective")
    rows: list[dict[str, Any]] = []
    excluded = {"case": 0, "contract": 0, "constraints": 0, "missing_objectives": 0}
    for run in runs:
        if str(run.get("status") or "").upper() != "COMPLETED":
            continue
        if str(run.get("source") or "").upper() == "PREDICTED":
            excluded["contract"] += 1
            continue
        if contract is not None:
            from .learning_contract import compatibility
            if not compatibility(run, contract)[0]:
                excluded["contract"] += 1
                continue
        if case is not None:
            # An unlabeled legacy run has no proof that its geometry matches this case.
            row_case = run.get("case") or run.get("case_id") or run.get("case_name")
            if row_case != case.case_id:
                excluded["case"] += 1
                continue
        metrics = run.get("metrics") or {}
        if case is not None and not _case_feasible(metrics, case):
            excluded["constraints"] += 1
            continue
        try:
            values = [(-1 if direction == "maximize" else 1) * float(metrics[key])
                      for key, direction in zip(keys, directions)]
            if not np.all(np.isfinite(values)):
                raise ValueError("nonfinite objective")
        except (KeyError, TypeError, ValueError):
            excluded["missing_objectives"] += 1
            continue
        rows.append({
            "run_id": str(run.get("run_id")),
            "at": str(run.get("started_utc") or run.get("created_utc") or ""),
            "values": values,
            "params": dict(run.get("params") or {}),
        })
    rows.sort(key=lambda row: row["at"])
    normalisation = normalisation or normalisation_from_runs(runs, keys)
    if not rows:
        return {"normalisation": normalisation.to_dict(), "reference": list(reference),
                "points": [], "cumulative": [], "generation": [], "best": 0.0,
                "keys": list(keys), "directions": directions, "excluded": excluded,
                "available": True, "status": "no_feasible_data" if case is not None else "empty"}
    scaled = [normalisation.scale(np.asarray(row["values"], dtype=float)) for row in rows]
    cumulative: list[float] = []
    generation: list[float] = []
    for index in range(len(scaled)):
        previous = cumulative[-1] if cumulative else 0.0
        try:
            total = _volume(np.asarray(scaled[: index + 1]), reference)
        except ImportError:
            return {"available": False, "status": "backend_unavailable",
                    "detail": "More than two objectives require the optimizer extra (pymoo)",
                    "keys": list(keys), "directions": directions, "excluded": excluded,
                    "normalisation": normalisation.to_dict(), "reference": list(reference),
                    "points": [], "cumulative": [], "generation": [], "best": None}
        cumulative.append(round(total, 6))
        generation.append(round(max(0.0, total - previous), 6))
    return {
        "normalisation": normalisation.to_dict(),
        "reference": list(reference),
        "points": [{"run_id": row["run_id"], "at": row["at"],
                    "scaled": [round(float(value), 6) for value in point],
                    "best_single": round(float(np.min(point)), 6)} for row, point in zip(rows, scaled)],
        "cumulative": cumulative,
        "generation": generation,
        "best": round(cumulative[-1], 6) if cumulative else 0.0,
        "previous": previous if cumulative else 0.0,
        "keys": list(keys), "directions": directions, "excluded": excluded,
        "available": True, "status": "available", "generation_basis": "marginal_feasible_hypervolume",
    }


