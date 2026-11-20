"""Tolerance and robustness: how much does a design move when manufacturing cannot hold the nominal?

The optimiser returns a nominal point; a workshop holds dimensions to a tolerance, so the number that
decides whether a design ships is the *worst case inside that tolerance window*, not the nominal. This
module is the planning and scoring skeleton: it lists the perturbed designs a campaign would have to
measure (deduplicated by identity, so ±(a,b) that collapse onto an existing point are dropped) and it
scores a set of measured neighbours into worst-case, mean, spread and yield.

No solver here: measuring the variants is a campaign, and this module stays testable without AEDT.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from typing import Any, Iterable, Mapping, Sequence
import math

from .distributed import config_hash

__all__ = ["ToleranceSpec", "perturbed_designs", "score_robustness"]

DEFAULT_DELTAS_MM = (0.05,)


@dataclass
class ToleranceSpec:
    """Which parameters move, by how much, and whether the corner or each parameter alone is measured."""

    parameters: tuple[str, ...]
    deltas: tuple[float, ...] = DEFAULT_DELTAS_MM
    corners_only: bool = True
    """True: only the 2^k corner combinations (cheap). False: each parameter alone (linearity check)."""

    def __post_init__(self) -> None:
        if not self.parameters:
            raise ValueError("a tolerance spec needs at least one parameter")
        if any(not math.isfinite(delta) or delta <= 0 for delta in self.deltas):
            raise ValueError("tolerance deltas must be positive")


def perturbed_designs(space: Any, nominal: dict[str, Any], spec: ToleranceSpec,
                      measured: Iterable[str] = ()) -> dict[str, Any]:
    """Perturbed parameter dictionaries that stay inside the box and are not already measured."""
    known = set(measured)
    low = {item.name: item.low for item in space}
    high = {item.name: item.high for item in space}
    points: list[dict[str, Any]] = []
    seen: set[str] = set()
    for delta in spec.deltas:
        if spec.corners_only:
            axes = [[(name, -delta), (name, delta)] for name in spec.parameters]
            combinations = product(*axes)
        else:
            combinations = ([(name, sign * delta)] for name in spec.parameters
                            for sign in (-1, 1))
        for combination in combinations:
            candidate = dict(nominal)
            for name, offset in combination:
                if name not in candidate:
                    raise KeyError("nominal has no parameter %r" % name)
                value = float(candidate[name]) + float(offset)
                if name in low:
                    value = min(max(value, float(low[name])), float(high[name]))
                candidate[name] = round(value, 6)
            identity = config_hash("", candidate)
            if identity in seen or identity in known:
                continue
            seen.add(identity)
            points.append(candidate)
    return {"nominal": dict(nominal), "variants": points,
            "corners_only": bool(spec.corners_only), "deltas": list(spec.deltas)}


def score_robustness(measurements: Iterable[dict[str, float]],
                     keys: tuple[str, ...] = ("s11_bandwidth", "peak_gain"),
                     constraints: dict[str, tuple[str, float]] | None = None, *,
                     directions: Mapping[str, str] | None = None,
                     objectives: Sequence[Any] | None = None,
                     planned_count: int | None = None,
                     ) -> dict[str, Any]:
    """Worst case, mean, spread and yield over a set of measured neighbours.

    ``constraints`` maps a metric to (operator, target), e.g. ``{"peak_gain": (">=", 6.0)}``; yield is
    the fraction of neighbours that satisfy every one. Missing values count as failures - an
    unmeasured neighbour is not a passing one.
    """
    constraints = constraints or {}
    direction_by_key = {"ripple": "minimize", "s11": "minimize", "sll": "minimize",
                        **dict(directions or {})}
    for objective in objectives or ():
        kind = objective.get("kind") if isinstance(objective, dict) else objective.kind
        direction = objective.get("direction") if isinstance(objective, dict) else objective.direction
        direction_by_key[kind] = direction
    if any(value not in {"maximize", "minimize"} for value in direction_by_key.values()):
        raise ValueError("objective directions must be maximize or minimize")
    original = [dict(item) for item in measurements]
    rows = [dict(row.get("metrics") or {}) if "metrics" in row else row for row in original]
    valid_status = [str(row.get("status", "COMPLETED")).upper() == "COMPLETED" for row in original]
    count = len(rows) if planned_count is None else int(planned_count)
    if count < len(rows) or count < 0:
        raise ValueError("planned_count cannot be smaller than the returned measurements")
    missing = count - len(rows)
    if not rows and not count:
        return {"count": 0, "worst": {}, "mean": {}, "spread": {}, "yield": 0.0,
                "yield_basis": "no measurements"}
    worst: dict[str, float | None] = {}
    mean: dict[str, float | None] = {}
    spread: dict[str, float | None] = {}
    def value_for(row, key):
        try:
            value = float(row[key])
        except (KeyError, TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None
    for key in keys:
        values = [value_for(row, key) for row, completed in zip(rows, valid_status) if completed]
        values = [value for value in values if value is not None]
        if not values:
            worst[key] = None
            mean[key] = None
            spread[key] = None
            continue
        values = [float(value) for value in values]
        worst[key] = float(max(values) if direction_by_key.get(key, "maximize") == "minimize" else min(values))
        mean[key] = float(sum(values) / len(values))
        spread[key] = float(max(values) - min(values))

    def passes(row: dict[str, Any]) -> bool:
        for key, (operator, target) in constraints.items():
            value = value_for(row, key)
            if value is None:
                return False
            if operator == ">=" and not float(value) >= float(target):
                return False
            if operator == "<=" and not float(value) <= float(target):
                return False
            if operator == "==" and not float(value) == float(target):
                return False
            if operator not in {">=", "<=", "=="}:
                raise ValueError("unsupported constraint operator: " + operator)
        # Missing requested objective data cannot manufacture a passing sample
        # even when there are no explicit hard constraints.
        if any(value_for(row, key) is None for key in keys):
            return False
        return True

    passed = sum(1 for row, completed in zip(rows, valid_status) if completed and passes(row))
    complete = not missing and all(valid_status) and all(
        all(value_for(row, key) is not None for key in keys) for row in rows)
    return {"count": len(rows), "planned_count": count, "missing_count": missing,
            "failed_count": sum(not status for status in valid_status),
            "worst": worst, "mean": mean, "spread": spread,
            "directions": {key: direction_by_key.get(key, "maximize") for key in keys},
            "worst_verified": complete, "worst_basis": "observed completed neighbours only",
            "yield": passed / count if count else 0.0,
            "yield_basis": "%d passing of %d planned neighbours (%d missing)" % (passed, count, missing)}
