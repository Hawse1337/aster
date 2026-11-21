"""Action-specific budget and delivery-risk estimates for one measurement recipe."""
from __future__ import annotations

from dataclasses import dataclass
import math

from .cost import fit_cost_model
from .learning_contract import stored_contract
from .risk import fit_risk_model


_FULL = {"geometry", "interaction", "tolerance"}
_KINDS = _FULL | {"frequency", "coverage"}
_TERMINAL = {"COMPLETED", "FAILED", "MODEL_FAILED", "TIMEOUT", "STALLED",
             "BUDGET_EXCEEDED", "RECOVERY_REQUIRED"}


@dataclass
class _Fits:
    cost: object
    risk: object


def _compatible(row, case_id, recipe_id):
    contract = stored_contract(row)
    cases = {x for x in (row.get("case"), row.get("case_name"), contract.get("case_id"),
                         row.get("case_id")) if x is not None}
    recipes = {x for x in (row.get("recipe_id"), contract.get("identity")) if x is not None}
    return (cases == {case_id} and recipes == {recipe_id}
            and str(row.get("source", "")).upper() == "MEASURED"
            and not (row.get("is_prediction") or row.get("cache_hit") or row.get("cached")))


class ActionScorer:
    def __init__(self, space, floors, models, empty):
        self.space, self.floors, self.models, self.empty = space, floors, models, empty
        self.categories = [item.name for item in space if item.kind == "choice"]

    def estimate(self, kind, params):
        """Quote seconds and a scheduling multiplier for an explicitly named action."""
        if kind not in _KINDS:
            raise ValueError(f"unknown study action {kind!r}")
        point = self.space.validate(dict(params))
        if point != params:
            raise ValueError("action parameters must be representable in the design space")
        group = tuple(point[name] for name in self.categories)
        direct = self.models.get((kind, group))
        legacy = self.models.get(("full_solve", group)) if kind in _FULL else None
        risk_fit = direct or legacy or self.empty
        cost_fit = direct or legacy or self.empty
        if not cost_fit.cost.samples and legacy and legacy.cost.samples:
            cost_fit = legacy
        cost = cost_fit.cost.predict(point)
        risk = risk_fit.risk.predict(point)
        cost_group = kind if direct is not None and cost_fit is direct else "full_solve"
        risk_group = kind if direct is not None else "full_solve"
        conservative = cost.conservative_seconds
        lower = cost.lower_bound_seconds
        if direct and cost_fit is not direct:
            lower = max(lower, direct.cost.predict(point).lower_bound_seconds)
        floor = self.floors.get(kind)
        quotes = []
        sources = []
        if conservative is not None:
            quotes.append(conservative)
            sources.append(f"measured:{cost_group}:{cost.method}")
        if floor is not None:
            quotes.append(floor)
            sources.append("configured")
        # A timeout is a lower bound, never a stand-alone budget for completion.
        if lower:
            sources.append("censored_lower_bound")
            if quotes:
                quotes.append(lower)
        estimate = max(quotes) if quotes else None
        multiplier = 1.0 if not risk.local_known else 1.0 - risk.physical_failure_rate
        cost_detail = cost.to_dict()
        cost_detail["lower_bound_seconds"] = lower
        return {"estimated_seconds": estimate, "cost_basis": "+".join(sources) or "unknown",
                "success_factor": multiplier,
                "risk_basis": f"observed:{risk_group}:local_smoothed_physical" if risk.local_known else "unknown",
                "risk": risk.to_dict(), "cost": cost_detail}


def fit_action_scorer(runs, space, *, case_id, recipe_id, cost_seconds=None):
    """Fit once. `estimate(kind, params)` reuses completed-duration and nearest-run fits.

    Measured terminal history must identify the case and recipe. A labelled row's
    action_seconds is its action duration; otherwise total_seconds must pertain to
    that row's action. An unlabelled row is a full solve. A frequency or angular
    measurement cannot inherit whole-solve timings. Categorical strata are exact.
    Configured seconds provide a floor, and can be used without historical fits.
    The multiplier prices local physical failure risk only. Infrastructure and
    censored outcomes remain visible in the diagnostics.
    """
    if not all(isinstance(x, str) and x.strip() for x in (case_id, recipe_id)):
        raise ValueError("case_id and recipe_id are required")
    floors = dict(cost_seconds or {})
    if set(floors) - _KINDS or any(type(x) not in (int, float) or not math.isfinite(x) or x <= 0
                                   for x in floors.values()):
        raise ValueError("cost_seconds requires known action kinds and finite positive seconds")
    numeric = [item.name for item in space if item.kind != "choice"]
    categories = [item.name for item in space if item.kind == "choice"]
    groups, seen = {}, set()
    for row in runs:
        if str(row.get("status", "")).upper() not in _TERMINAL or not _compatible(row, case_id, recipe_id):
            continue
        kind = row.get("action_kind", "full_solve")
        if kind != "full_solve" and kind not in _KINDS:
            continue
        rid = row.get("run_id")
        if not isinstance(rid, str) or not rid:
            continue
        if rid in seen:
            raise ValueError(f"duplicate measured action run_id {rid!r}")
        try:
            params = space.validate(row["params"])
            if params != row["params"]:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        seen.add(rid)
        group = (kind, tuple(params[k] for k in categories))
        groups.setdefault(group, []).append({**row, "params": params,
            "total_seconds": row.get("action_seconds", row.get("total_seconds"))})
    def fit(rows):
        return _Fits(fit_cost_model(rows, numeric), fit_risk_model(rows, numeric))
    return ActionScorer(space, floors, {group: fit(rows) for group, rows in groups.items()}, fit([]))
