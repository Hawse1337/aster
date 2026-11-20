"""Recommend the next candidates from what a campaign has actually measured.

Up to here the GUI could measure a list of candidates a person typed. This closes the loop: read the
finished runs of a campaign, fit a surrogate, ask the acquisition dispatcher for ``q`` new designs,
and explain the choice - which backend answered, which runs it learned from, and what the nearest
measurement suggests about the constraints.

Two decisions worth stating:

* **only measured runs count.** A predicted metric must never be written as evidence (the guard
  refuses that anyway), so the training set is the COMPLETED jobs in the queue database.
* **objectives are minimised inside the surrogate.** Bandwidth and gain are what the engineer wants
  *more* of, so they enter negated; that convention already cost one debugging session and is
  applied in exactly one place here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

from .surrogate.backends import select_batch

__all__ = ["Recommendation", "recommend_from_runs", "training_set", "tighten_bounds", "measured_envelope", "OBJECTIVES", "CONSTRAINTS"]

def tighten_bounds(bounds: Sequence[tuple[float, float]],
                   accepts: Callable[[dict[str, float]], bool],
                   defaults: dict[str, float],
                   names: Sequence[str],
                   samples: int = 9) -> list[tuple[float, float]]:
    """Shrink each parameter's range to the part the model actually accepts.

    Real runs exposed the need: the optimiser proposed ``ring_outer = 1.8`` because the parameter
    range allows it, but the geometry rule requires at least the coax wall radius, so the solve died
    in its first second. Probing a small grid per parameter - the others held at the default - finds
    the usable interval without encoding any individual rule here.
    """
    result: list[tuple[float, float]] = []
    for name, (low, high) in zip(names, bounds):
        usable: list[float] = []
        for index in range(samples):
            value = float(low + (high - low) * index / max(1, samples - 1))
            probe = dict(defaults)
            probe[name] = value
            try:
                if accepts(probe):
                    usable.append(value)
            except Exception:  # noqa: BLE001 - a raising check means "not acceptable"
                continue
        if not usable:
            result.append((float(low), float(high)))
            continue
        first, last = min(usable), max(usable)
        # the grid only brackets the edge; bisect between the neighbouring rejected sample and the
        # accepted one so the bound is the edge and not one grid step away from it
        step = (high - low) / max(1, samples - 1)
        if first > low:
            first = _edge(lambda value: _accepts(name, value, accepts, defaults),
                          first - step, first, increasing=True)
        if last < high:
            last = _edge(lambda value: _accepts(name, value, accepts, defaults),
                         last, last + step, increasing=False)
        result.append((float(first), float(last)))
    return result


def _accepts(name: str, value: float, accepts: Callable[[dict[str, float]], bool],
             defaults: dict[str, float]) -> bool:
    probe = dict(defaults)
    probe[name] = float(value)
    try:
        return bool(accepts(probe))
    except Exception:  # noqa: BLE001 - a raising check means "not acceptable"
        return False


def _edge(check: Callable[[float], bool], bad: float, good: float, *,
          increasing: bool, rounds: int = 8) -> float:
    """Find where ``check`` flips between ``bad`` (rejected) and ``good`` (accepted)."""
    for _ in range(rounds):
        middle = (bad + good) / 2.0
        if check(middle):
            good = middle
        else:
            bad = middle
    return good


def measured_envelope(x: np.ndarray, names: Sequence[str],
                      bounds: Sequence[tuple[float, float]],
                      margin: float = 0.0) -> list[tuple[float, float]]:
    """Per-parameter range actually covered by the measurements.

    The closed-loop acceptance made this necessary: with four points that all shared the same ring
    height, disc factor and so on, the optimiser set every one of them to an extreme corner and the
    measured design radiated -5.4 dBi. A parameter nobody varied carries no information, so it is
    held at its measured value (low == high) instead of being explored.
    """
    low_case = np.array([pair[0] for pair in bounds], dtype=float)
    high_case = np.array([pair[1] for pair in bounds], dtype=float)
    result: list[tuple[float, float]] = []
    for index in range(len(names)):
        column = x[:, index]
        low = float(np.min(column))
        high = float(np.max(column))
        if margin > 0 and high > low:
            pad = (high - low) * float(margin)
            low, high = low - pad, high + pad
        measured_low, measured_high = low, high
        low = max(low, float(low_case[index]))
        high = min(high, float(high_case[index]))
        if high < low:
            # the measured points and the feasible bounds do not overlap (a coarse probe can be
            # stricter than reality): trust the measurements and let the per-point validator decide
            low, high = measured_low, measured_high
        result.append((low, high))
    return result


OBJECTIVES = (("s11_bandwidth", "max"), ("peak_gain", "max"))
"""Legacy standalone defaults; GUI recommendations always pass a CaseSpec."""

CONSTRAINTS = (("ripple", "<=", 2.0),)


@dataclass
class Recommendation:
    params: dict[str, Any]
    feasible: bool | None = None
    feasibility_status: str = "unknown"
    violations: dict[str, float] = field(default_factory=dict)
    nearest_run: str = ""
    note: str = ""
    predicted_cost: dict[str, Any] = field(default_factory=dict)
    risk: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


def completed_runs(runs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [run for run in runs if str(run.get("status") or "").upper() == "COMPLETED"
            and str(run.get("source") or "").upper() != "PREDICTED"
            and not run.get("is_prediction")]


def _objectives(case: Any = None) -> list[tuple[str, str]]:
    return ([(item.kind, item.direction) for item in case.objectives] if case is not None
            else list(OBJECTIVES))


def _sign(direction: str) -> float:
    return -1.0 if direction in ("max", "maximize") else 1.0


def _vector(params: dict[str, Any], names: Sequence[str], specs: Sequence[Any] | None
            ) -> list[float]:
    by_name = {spec.name: spec for spec in (specs or [])}
    result = []
    for name in names:
        value = params[name]  # Missing dimensions require an explicit migration, never defaults.
        spec = by_name.get(name)
        if spec is not None:
            validated = spec.validate(value)
            if spec.kind == "int" and float(validated) != float(value):
                raise ValueError("historical integer value is not discrete")
        result.append(float(spec.choices.index(value)) if spec and spec.kind == "choice"
                      else float(value))
    if not np.all(np.isfinite(result)):
        raise ValueError("non-finite parameters")
    return result


def training_set(runs: Iterable[dict[str, Any]], names: Sequence[str],
                 defaults: Mapping[str, float] | None = None, *, case: Any = None,
                 contract: Mapping[str, Any] | None = None,
                 parameter_specs: Sequence[Any] | None = None
                 ) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]]]:
    """Complete, finite measured rows; defaults never manufacture historical dimensions."""
    from .learning_contract import compatibility, constraint_values, finite_metrics
    xs, ys, used = [], [], []
    objectives = _objectives(case)
    for run in completed_runs(runs):
        if contract is not None and not compatibility(run, contract)[0]:
            continue
        if case is not None:
            row_case = run.get("case") or run.get("case_name")
            if row_case and row_case != case.case_id:
                continue
        params, metrics = dict(run.get("params") or {}), finite_metrics(run.get("metrics") or {})
        try:
            row = _vector(params, names, parameter_specs)
            values = [float(metrics[key]) * _sign(direction) for key, direction in objectives]
        except (KeyError, TypeError, ValueError):
            continue
        if not np.all(np.isfinite(values)):
            continue
        if case is not None and constraint_values(metrics, case) is None:
            continue
        xs.append(row)
        ys.append(values)
        used.append(run)
    return (np.asarray(xs, dtype=float).reshape((-1, len(names))),
            np.asarray(ys, dtype=float).reshape((-1, len(objectives))), used)


def _nearest(x_train: np.ndarray, point: np.ndarray) -> int:
    span = np.maximum(np.ptp(x_train, axis=0), 1e-9)
    return int(np.argmin(np.linalg.norm((x_train - point) / span, axis=1)))


def _canonical(point: Sequence[float], names: Sequence[str],
               specs: Sequence[Any] | None) -> dict[str, Any]:
    by_name = {spec.name: spec for spec in (specs or [])}
    result = {}
    for name, value in zip(names, point):
        spec = by_name.get(name)
        if spec is None:
            result[name] = float(value)
        elif spec.kind == "choice":
            result[name] = spec.choices[min(len(spec.choices) - 1, max(0, int(round(value))))]
        else:
            normalized = (float(value) - spec.low) / (spec.high - spec.low)
            result[name] = spec.validate(spec.denormalize(normalized))
    return result


def _key(params: dict[str, Any], names: Sequence[str], specs: Sequence[Any] | None) -> tuple:
    point = _vector(params, names, specs)
    clean = _canonical(point, names, specs)
    return tuple((name, round(clean[name], 9) if isinstance(clean[name], (int, float))
                  else clean[name]) for name in names)


def recommend_from_runs(runs: Sequence[dict[str, Any]], *, count: int = 2,
                        method: str = "auto", seed: int = 0,
                        names: Sequence[str] | None = None,
                        bounds: Sequence[tuple[float, float]] | None = None,
                        weights: Sequence[float] | None = None,
                        minimum_runs: int = 4,
                        defaults: Mapping[str, float] | None = None,
                        validator: Callable[[dict[str, float]], bool] | None = None,
                        extrapolate: bool = False, margin: float = 0.0,
                        budget_seconds: float | None = None,
                        max_failure_risk: float | None = None,
                        reference_point: Sequence[float] | None = None,
                        case: Any = None, contract: Mapping[str, Any] | None = None,
                        parameter_specs: Sequence[Any] | None = None,
                        cold_start: bool | None = None, exploration_radius: float = 0.1,
                        fallback_cost_seconds: float | None = None
                        ) -> dict[str, Any]:
    """Propose unmeasured designs; feasibility remains unknown until actual acceptance.

    Case-backed production calls use controlled local exploration until there is enough
    dimensional coverage for a surrogate. Legacy standalone callers can opt into that
    explicitly with cold_start=True.
    """
    from types import SimpleNamespace
    from .learning_contract import compatibility, constraint_values
    from .cost import fit_cost_model
    from .risk import fit_risk_model
    from .surrogate.backends import METHODS

    if not names or not bounds or len(names) != len(bounds):
        raise ValueError("names and matching bounds are required to vectorise the parameters")
    if budget_seconds is not None and (not np.isfinite(budget_seconds) or budget_seconds <= 0):
        raise ValueError("budget_seconds must be finite and positive")
    if max_failure_risk is not None and (not np.isfinite(max_failure_risk)
                                       or not 0 <= max_failure_risk <= 1):
        raise ValueError("max_failure_risk must be a finite 0..1 limit")
    if method not in tuple(METHODS) + ("diverse_ei",):
        raise ValueError("unknown acquisition method: " + str(method))
    if int(count) < 1 or int(count) > 64:
        raise ValueError("count must be between 1 and 64")
    objectives = _objectives(case)
    if weights is not None and len(weights) != len(objectives):
        raise ValueError("weights must have one entry per current objective")
    if reference_point is not None and len(reference_point) != len(objectives):
        raise ValueError("reference point must have one entry per current objective")
    compatible, excluded = [], []
    for row in runs:
        if str(row.get("source") or "").upper() == "PREDICTED" or row.get("is_prediction"):
            excluded.append({"run_id": row.get("run_id"), "reason": "predicted_not_evidence"})
            continue
        ok, reason = compatibility(row, contract) if contract is not None else (True, "")
        if ok:
            compatible.append(row)
        else:
            excluded.append({"run_id": row.get("run_id"), "reason": reason})
    x, y, used = training_set(compatible, names, case=case, parameter_specs=parameter_specs)
    trained_on = len(x)
    current_bounds = [tuple(map(float, pair)) for pair in bounds]
    dimension = sum(high > low for low, high in current_bounds)
    # A handful of points in a many-dimensional model is a pilot, not a mature GP.
    needed = max(2, int(minimum_runs), 2 * dimension + 1 if case is not None else 0)
    coverage = int(np.sum(np.ptp(x, axis=0) > 1e-9)) if trained_on else 0
    uncovered = trained_on and any(np.max(x[:, i]) < low or np.min(x[:, i]) > high
                                  for i, (low, high) in enumerate(current_bounds))
    explore = (trained_on < needed or (case is not None and coverage < dimension)
               or (case is not None and uncovered))
    cold_start = case is not None if cold_start is None else bool(cold_start)
    base = {"trained_on": trained_on, "source_runs": [str(row.get("run_id")) for row in used],
            "excluded_runs": excluded, "minimum_runs": needed,
            "objectives": [{"kind": kind, "direction": direction} for kind, direction in objectives],
            "learning_contract": dict(contract or {})}
    if explore and not cold_start:
        return {**base, "recommendations": [], "method": "none",
                "reason": "实测数据不足（%d 条，至少需要 %d 条）" % (trained_on, needed)}

    observed, pending = [], []
    occupied = set()
    for row in compatible:
        if str(row.get("status") or "").upper() not in (
                "COMPLETED", "QUEUED", "RUNNING", "SOLVING", "PREPARING", "RECOVERY_REQUIRED"):
            continue
        try:
            point = _vector(row.get("params") or {}, names, parameter_specs)
            occupied.add(_key(row.get("params") or {}, names, parameter_specs))
        except (KeyError, ValueError, TypeError):
            continue
        observed.append(point)
        if str(row.get("status") or "").upper() != "COMPLETED":
            pending.append(point)

    if explore:
        radius = min(0.25, max(0.01, float(exploration_radius)))
        centre = (_vector(dict(defaults or case.family.param_space.default()), names, parameter_specs)
                  if defaults or case is not None else [(low + high) / 2 for low, high in bounds])
        centre = np.clip(centre, np.array(current_bounds)[:, 0], np.array(current_bounds)[:, 1])
        bounds = [(max(low, value - radius * (high - low)),
                   min(high, value + radius * (high - low)))
                  for value, (low, high) in zip(centre, current_bounds)]
        if parameter_specs:
            # A 10% interval can contain only one rounded category/integer. Explore
            # at most one discrete neighbour, while retaining the geometry validator.
            bounds = [(max(low, centre[i] - 1), min(high, centre[i] + 1))
                      if parameter_specs[i].kind in ("int", "choice") else bounds[i]
                      for i, (low, high) in enumerate(current_bounds)]
        rng = np.random.RandomState(seed)
        # A stratified pilot pool, ranked by distance from work already measured/pending.
        pool = np.empty((max(128, int(count) * 32), len(names)))
        for j, (low, high) in enumerate(bounds):
            bins = rng.permutation(len(pool))
            pool[:, j] = low + (bins + rng.rand(len(pool))) / len(pool) * (high - low)
        if observed:
            scale = np.maximum(np.ptp(np.asarray(current_bounds), axis=1), 1e-9)
            distance = np.min(np.linalg.norm((pool[:, None, :] -
                np.asarray(observed)[None, :, :]) / scale, axis=2), axis=1)
            pool = pool[np.argsort(-distance)]
        selection = SimpleNamespace(points=pool, method="controlled_exploration",
            note="实测覆盖不足：仅在默认设计附近有限探测，不拟合成熟代理模型",
            diagnostics={"stage": "pilot", "quality": "insufficient_coverage"})
        limit = min(int(count), 2)
    else:
        bounds = current_bounds if extrapolate else measured_envelope(x, names, current_bounds, margin)
        active = [i for i, (low, high) in enumerate(bounds) if high > low]
        if not active:
            return {**base, "recommendations": [], "method": "none",
                    "reason": "所有参数在实测中都没有变化，无法推荐新点"}
        transformed_reference = ([float(value) * _sign(direction)
            for value, (_kind, direction) in zip(reference_point, objectives)]
            if reference_point is not None else None)
        kwargs = {}
        if case is not None:
            kwargs["constraint_obs"] = np.asarray([
                constraint_values(row.get("metrics") or {}, case) for row in used], dtype=float)
        if pending:
            kwargs["pending_X"] = np.asarray(pending, dtype=float)[:, active]
        kwargs["groups"] = [str(_key(row["params"], names, parameter_specs)) for row in used]
        if all(row.get("started_utc") or row.get("created_utc") for row in used):
            kwargs["times"] = [row.get("started_utc") or row["created_utc"] for row in used]
        selection = select_batch(x[:, active], y, [bounds[i] for i in active],
            count=min(64, max(int(count) * 3, int(count) + 4)), method=method, seed=seed,
            weights=weights, reference_point=transformed_reference, **kwargs)
        points = np.tile(np.array([low for low, high in bounds]), (len(selection.points), 1))
        points[:, active] = np.asarray(selection.points)
        selection.points = points
        limit = int(count)

    # Risk/cost see only the same physical recipe, while retaining failed runs as evidence.
    cost_model = fit_cost_model(compatible, names) if compatible else None
    risk_model = fit_risk_model(compatible, names) if compatible else None
    candidates, rejected, over_budget, over_risk = [], [], [], []
    dropped, conservative_total = 0, 0.0
    warning = []
    for point in np.asarray(selection.points, dtype=float):
        try:
            params = _canonical(point, names, parameter_specs)
            key = _key(params, names, parameter_specs)
            vector = _vector(params, names, parameter_specs)
            if any(value < low - 1e-9 or value > high + 1e-9
                   for value, (low, high) in zip(vector, current_bounds)):
                continue
        except (TypeError, ValueError, KeyError):
            continue
        if key in occupied:
            dropped += 1
            continue
        if validator is not None:
            try:
                valid = bool(validator(params))
            except Exception:
                valid = False
            if not valid:
                rejected.append(params)
                continue
        predicted_cost = cost_model.predict(params).to_dict() if cost_model else {}
        conservative = predicted_cost.get("conservative_seconds",
            predicted_cost.get("upper_seconds", (float(predicted_cost.get("seconds") or 0) +
                                      2 * float(predicted_cost.get("spread") or 0))))
        if not conservative or not np.isfinite(conservative):
            conservative = fallback_cost_seconds
        if budget_seconds is not None:
            if conservative is None:
                over_budget.append(params)
                warning.append("耗时证据不足，无法证明整批预算足够")
                continue
            if conservative_total + float(conservative) > float(budget_seconds):
                over_budget.append(params)
                continue
        risk = risk_model.predict(params).to_dict() if risk_model else {"known": False}
        risk_known = bool(risk.get("known", risk.get("neighbours", 0) > 0))
        risk_bound = risk.get("risk_upper", risk.get("failure_rate"))
        if max_failure_risk is not None and risk_known and risk_bound is not None:
            if float(risk_bound) > max_failure_risk:
                over_risk.append({**params, "risk_upper": risk_bound})
                continue
        if not risk_known:
            warning.append("物理失败风险证据不足，仅作限量探索")
            if max_failure_risk is not None and max_failure_risk < 1.0:
                over_risk.append({**params, "risk_upper": 1.0, "known": False})
                warning.append("未知风险不能满足指定风险硬上限；允许探测需不设上限或显式设为1")
                continue
            if len(candidates) >= 2:
                continue
        nearest = str(used[_nearest(x, np.asarray(_vector(params, names, parameter_specs)))].get(
            "run_id")) if trained_on else ""
        note = "尚未仿真，可行性未知；最近实测仅供参考"
        if any(abs(float(value) - edge) < 1e-9 for value, pair in
               zip(_vector(params, names, parameter_specs), bounds) for edge in pair):
            note += "；参数位于搜索边界"
        if conservative is not None:
            predicted_cost["conservative_seconds"] = float(conservative)
            conservative_total += float(conservative)
        candidates.append(Recommendation(params=params, nearest_run=nearest, note=note,
            predicted_cost=predicted_cost, risk=risk))
        occupied.add(key)
        if len(candidates) >= limit:
            break
    notes = []
    if rejected:
        notes.append("%d 个候选未通过几何校验已剔除" % len(rejected))
    if over_budget:
        notes.append("%d 个候选超过剩余整批预算或耗时未知" % len(over_budget))
    if over_risk:
        notes.append("%d 个候选物理失败风险上界过高" % len(over_risk))
    if len(candidates) < int(count):
        notes.append("受实测覆盖限制及去重/探测配额，只给出 %d 个候选" % len(candidates))
    reason = (selection.note if explore else
              "以 %d 条实测数据拟合，使用 %s 后端推荐 %d 个候选" %
              (trained_on, selection.method, len(candidates)))
    if notes:
        reason += "；" + "；".join(notes)
    return {**base, "recommendations": [item.to_dict() for item in candidates],
            "method": selection.method, "note": selection.note, "reason": reason,
            "diagnostics": getattr(selection, "diagnostics", {}),
            "rejected": rejected, "over_budget": over_budget, "over_risk": over_risk,
            "budget_seconds": budget_seconds, "batch_conservative_seconds": conservative_total,
            "reference_point": list(reference_point) if reference_point is not None else None,
            "dropped_duplicates": dropped, "warnings": sorted(set(warning)),
            "bounds_used": [list(pair) for pair in bounds]}
