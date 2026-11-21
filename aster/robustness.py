"""Tolerance and robustness: how much does a design move when manufacturing cannot hold the nominal?

The optimiser returns a nominal point; a workshop holds dimensions to a tolerance, so the number that
decides whether a design ships is the *worst case inside that tolerance window*, not the nominal. This
module is the planning and scoring skeleton: it lists the perturbed designs a campaign would have to
measure (deduplicated by identity, so ±(a,b) that collapse onto an existing point are dropped) and it
scores a set of measured neighbours into worst-case, mean, spread and yield.

No solver here: measuring the variants is a campaign, and this module stays testable without AEDT.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from statistics import NormalDist
from itertools import product
from typing import Any, Iterable, Mapping, Sequence
import math

from .distributed import config_hash

__all__ = ["ToleranceSpec", "perturbed_designs", "score_robustness",
           "plan_tolerance_study", "score_process_yield"]

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
                "yield_basis": "no measurements", "scenario_pass_fraction": 0.0,
                "manufacturing_yield": None}
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
        all(value_for(row, key) is not None for key in set(keys) | set(constraints)) for row in rows)
    return {"count": len(rows), "planned_count": count, "missing_count": missing,
            "failed_count": sum(not status for status in valid_status),
            "worst": worst, "mean": mean, "spread": spread,
            "directions": {key: direction_by_key.get(key, "maximize") for key in keys},
            "worst_verified": complete, "worst_basis": "observed completed neighbours only",
            "scenario_pass_fraction": passed / count if count else 0.0, "manufacturing_yield": None,
            "yield": passed / count if count else 0.0,
            "yield_basis": "%d passing of %d planned neighbours (%d missing)" % (passed, count, missing)}


# Scale factors are relative to mm, degrees and GHz within each dimension.
_UNITS = {
    "nm": ("length", 1e-6), "um": ("length", 1e-3), "mm": ("length", 1.0),
    "cm": ("length", 10.0), "m": ("length", 1000.0), "in": ("length", 25.4),
    "mil": ("length", 0.0254), "deg": ("angle", 1.0), "rad": ("angle", 180.0 / math.pi),
    "Hz": ("frequency", 1e-9), "kHz": ("frequency", 1e-6), "MHz": ("frequency", 1e-3),
    "GHz": ("frequency", 1.0), "1": ("ratio", 1.0), "%": ("ratio", 0.01),
}


def _number(value: Any) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"expected a finite number, received {value!r}")
    return float(value)


def _unit_scale(source: str, model: str) -> float:
    if not isinstance(source, str) or not isinstance(model, str):
        raise ValueError("tolerance units must be strings")
    aliases = {"": "1", "°": "deg"}
    source, model = (aliases.get(u, u).replace("µ", "u").replace("μ", "u") for u in (source, model))
    if source == model:
        return 1.0
    if source not in _UNITS or model not in _UNITS or _UNITS[source][0] != _UNITS[model][0]:
        raise ValueError(f"incompatible or unsupported tolerance units {source!r} and {model!r}")
    return _UNITS[source][1] / _UNITS[model][1]


def _hard_constraints(constraints):
    parsed = {}
    for name, (op, target) in (constraints or {}).items():
        if op not in {">=", "<=", "=="}:
            raise ValueError(f"unsupported constraint operator {op!r}")
        parsed[name] = (op, _number(target))
    return parsed


def _completed_metrics(row, required):
    if str(row.get("status", "")).upper() != "COMPLETED" or row.get("invalid"):
        return None
    metrics = row.get("metrics")
    if not isinstance(metrics, Mapping) or not metrics:
        return None
    if not required:
        numeric = [v for v in metrics.values() if type(v) in (float, int) and math.isfinite(v)]
        return {} if numeric else None
    try:
        return {key: _number(metrics[key]) for key in required}
    except (KeyError, TypeError, ValueError):
        return None


def _margin(value, constraint):
    op, target = constraint
    if op == ">=":
        return value - target
    if op == "<=":
        return target - value
    return -abs(value - target)


def plan_tolerance_study(space: Any, nominal: Mapping[str, Any],
                         tolerances: Mapping[str, Any], *, scenarios=None,
                         measurements=(), constraints=None, keys=(), corners=False,
                         required_repeats: int = 1, objective_directions=None,
                         max_scenarios: int = 4096) -> dict[str, Any]:
    """Plan deterministic verification in the model's physical units.

    A tolerance is a positive native half-width or {delta, unit, resolution?}.
    Resolution is the build/fixture increment in the specified unit, relative
    to the nominal. Floating dimensions otherwise remain continuous; the
    optimiser's search increment is not a fabrication limit. Named scenarios
    replace the independent axes/corners with simultaneous signed offsets in
    the corresponding tolerance units. The nominal is always included.

    Supply measurements from the same campaign, fidelity, setup and metric
    definition. A repeated test uses distinct non-cached run IDs. Margins use
    the native units of each KPI and the least favorable completed replicate.
    """
    if type(required_repeats) is not int or required_repeats < 1:
        raise ValueError("required_repeats must be a positive integer")
    if not tolerances:
        raise ValueError("at least one tolerance is required")
    base = space.validate(dict(nominal))
    for item in space:
        if item.kind != "choice" and base[item.name] != nominal[item.name]:
            raise ValueError(f"nominal {item.name} is not representable")
    hard = _hard_constraints(constraints)
    directions = dict(objective_directions or {})
    if any(direction not in {"minimize", "maximize"} for direction in directions.values()):
        raise ValueError("objective directions must be minimize or maximize")
    required = set(keys) | set(hard) | set(directions)
    axes = {}
    for name, raw in tolerances.items():
        item = space[name]
        if item.kind == "choice":
            raise ValueError(f"numeric tolerance cannot perturb choice {name!r}")
        settings = raw if isinstance(raw, Mapping) else {"delta": raw}
        unit = settings.get("unit", item.unit)
        scale = _unit_scale(unit, item.unit)
        delta = _number(settings["delta"])
        resolution = _number(settings.get("resolution", 0.0))
        if delta <= 0 or resolution < 0:
            raise ValueError("delta must be positive and resolution must be nonnegative")
        axes[name] = (delta, scale, resolution)

    if type(max_scenarios) is not int or max_scenarios < 1:
        raise ValueError("max_scenarios must be a positive integer")
    size = len(scenarios) if scenarios is not None else (2 ** len(axes) if corners else 2 * len(axes))
    if size + 1 > max_scenarios:
        raise ValueError(f"{size + 1} scenarios including nominal exceed max_scenarios={max_scenarios}")
    if scenarios is None:
        if corners:
            scenarios = {",".join(signs): {name: axes[name][0] * (1 if sign == "+" else -1)
                                          for name, sign in zip(axes, signs)}
                         for signs in product(("-", "+"), repeat=len(axes))}
        else:
            scenarios = {name + sign: {name: delta * factor}
                         for name, (delta, _, _) in axes.items()
                         for sign, factor in (("-", -1), ("+", 1))}
    if not isinstance(scenarios, Mapping) or not scenarios or "nominal" in scenarios:
        raise ValueError("provide named, nonempty scenarios; 'nominal' is reserved")

    observed = {}
    for row in measurements:
        metrics = _completed_metrics(row, required)
        if metrics is None:
            continue
        try:
            params = space.validate(dict(row["params"]))
            if params != row["params"]:
                continue  # a fractional integer is not an observation of its rounded neighbour
        except (KeyError, ValueError, TypeError):
            continue
        run_id = row.get("run_id")
        if required_repeats > 1 and (not run_id or row.get("cache_hit") or row.get("cached")):
            continue
        runs = observed.setdefault(config_hash("", params), {})
        # Unidentified rows may provide one observation, but never prove replication.
        evidence = runs.setdefault(run_id, [])
        evidence.append(metrics)

    records, pending = [], {}
    worst = {key: None for key in hard}
    observed_worst = {}
    passed = verified = 0
    for label, offsets in {"nominal": {}, **scenarios}.items():
        if not isinstance(label, str) or not label or not isinstance(offsets, Mapping):
            raise ValueError("each scenario needs a name and an offset mapping")
        candidate = dict(base)
        requested, errors = {}, {}
        outside = []
        for name, offset in offsets.items():
            if name not in axes:
                raise ValueError(f"no tolerance declared for {name!r}")
            _, scale, resolution = axes[name]
            delta = _number(offset)
            origin = Decimal(str(base[name]))
            displacement = Decimal(str(delta))
            factor = Decimal(str(scale))
            requested[name] = float(origin + displacement * factor)
            if resolution:
                step = Decimal(str(resolution))
                displacement = round(displacement / step) * step
            actual = float(origin + displacement * factor)
            item = space[name]
            if not (item.low <= requested[name] <= item.high and item.low <= actual <= item.high):
                outside.append(name)
                continue
            candidate[name] = item.validate(actual)
            errors[name] = candidate[name] - requested[name]
        record = {"name": label, "requested_params": {**base, **requested},
                  "quantization_error": errors, "outside": outside, "repeats": 0,
                  "margin": {}, "passed": None}
        records.append(record)
        if outside:
            record.update(status="outside_space", params=None)
            continue
        identity = config_hash("", candidate)
        runs = observed.get(identity, {})
        record.update(params=candidate, repeats=len(runs))
        remaining = max(0, required_repeats - len(runs))
        exact = all(error == 0.0 for error in errors.values())
        if remaining:
            record["status"] = "missing"
            todo = pending.setdefault(identity, {"params": candidate, "scenarios": [],
                                                   "remaining_repeats": remaining})
            todo["scenarios"].append(label)
        elif not exact:
            record["status"] = "quantized"
        else:
            verified += 1
            record["status"] = "verified"
            for key, direction in directions.items():
                reduce_worst = min if direction == "maximize" else max
                values = [metrics[key] for group in runs.values() for metrics in group]
                if key in observed_worst:
                    values.append(observed_worst[key])
                observed_worst[key] = reduce_worst(values)

        margins = {}
        for key, constraint in hard.items():
            evidence = [_margin(metrics[key], constraint) for group in runs.values() for metrics in group]
            if evidence:
                margins[key] = min(evidence)
                if exact and (worst[key] is None or margins[key] < worst[key]):
                    worst[key] = margins[key]
        record["margin"] = margins
        record["passed"] = (all(m >= 0 for m in margins.values())
                            if hard and record["status"] == "verified" else None)
        passed += record["passed"] is True
    return {"scenarios": records, "pending": list(pending.values()),
            "complete": verified == len(records), "verified_count": verified,
            "planned_count": len(records), "worst_margin": worst,
            "observed_worst_objectives": observed_worst,
            "verified_worst_objectives": dict(observed_worst) if verified == len(records) else {},
            "scenario_pass_fraction": passed / len(records) if hard else None,
            "scenario_pass_basis": "verified passing scenarios / all planned scenarios",
            "manufacturing_yield": None, "units": {n: space[n].unit for n in axes}}


def score_process_yield(measurements, *, process_id: str, sample_ids: Sequence[str],
                        constraints, independent_random_sample: bool, sample_source: str) -> dict[str, Any]:
    """Estimate pass probability for declared independent random process draws.

    Use actual manufacturing sample IDs and a fixed process definition. If
    inputs are simulated process draws, the estimate only describes that model.
    Complete repeated observations of a sample must agree on pass/fail. Missing
    or conflicting classifications leave the interval undefined. The 95%
    interval uses the Wilson score method (NIST handbook, section 7.2.4.1).
    """
    if sample_source not in {"manufactured", "simulated"}:
        raise ValueError("sample_source must be manufactured or simulated")
    hard = _hard_constraints(constraints)
    ids = list(sample_ids)
    if (not process_id or not ids or not hard or len(set(ids)) != len(ids)
            or any(not isinstance(key, str) or not key for key in ids)):
        raise ValueError("a named process, unique sample IDs and hard constraints are required")
    outcomes = {key: set() for key in ids}
    for row in measurements:
        sid = row.get("sample_id")
        if (row.get("process_id") != process_id or sid not in outcomes
                or row.get("sample_source", sample_source) != sample_source):
            continue
        metrics = _completed_metrics(row, hard)
        if metrics is not None:
            outcomes[sid].add(all(_margin(metrics[key], rule) >= 0 for key, rule in hard.items()))
    unresolved = [key for key, values in outcomes.items() if len(values) != 1]
    passed = sum(values == {True} for values in outcomes.values())
    n = len(ids)
    result = {"process_id": process_id, "sample_source": sample_source, "planned": n, "passed": passed,
              "unresolved": unresolved, "modeled_yield": None,
              "sample_pass_fraction": None if unresolved else passed / n, "manufacturing_yield": None,
              "wilson_95": None}
    if unresolved or independent_random_sample is not True:
        return result
    fraction = passed / n
    z = NormalDist().inv_cdf(0.975)
    denom = 1 + z*z/n
    center = (fraction + z*z/(2*n)) / denom
    half = z * math.sqrt(fraction*(1-fraction)/n + z*z/(4*n*n)) / denom
    result["manufacturing_yield" if sample_source == "manufactured" else "modeled_yield"] = fraction
    result["wilson_95"] = [0.0 if passed == 0 else max(0.0, center-half),
                           1.0 if passed == n else min(1.0, center+half)]
    return result
