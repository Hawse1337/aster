"""Structured physics diagnosis for the LLM (module C).

The output follows the task-book template so an agent can reason about *why*
a design is failing instead of blindly scanning parameters.
"""
from __future__ import annotations

from typing import Any, Sequence

from ..core import CaseSpec
from ..optimize import constraint_violation
from .sensitivity import SensitivityMatrix

STRATEGY_HINTS = {
    "local_constrained_optimization": "improve the failing metric while protecting passing ones",
    "sensitivity_scan": "measure sensitivities before committing to a direction",
    "global_search": "conflicting metrics; explore the design space broadly",
    "topology_search": "parameters cannot fix the bottleneck; change the structure family",
}


def diagnose(
    case: CaseSpec,
    metrics: dict[str, float | None],
    sensitivity: SensitivityMatrix | None,
    *,
    budget_left: int | None = None,
    stalled_rounds: int = 0,
) -> dict[str, Any]:
    violations = {
        constraint.id: constraint_violation(metrics.get(constraint.kind), constraint)
        for constraint in case.constraints
    }
    failing = [name for name, value in violations.items() if value > 0]
    passing = [name for name in violations if name not in failing]
    kind_by_id = {constraint.id: constraint.kind for constraint in case.constraints}

    sensitive: list[str] = []
    confidence = "low"
    if sensitivity is not None and failing:
        for metric_id in failing:
            kind = kind_by_id[metric_id]
            sensitive.extend(name for name, _ in sensitivity.top_params(kind, 2))
        sensitive = list(dict.fromkeys(sensitive))
        confidence = "medium" if len(sensitivity.metric_kinds) >= 2 else "low"

    if not failing:
        strategy = "local_constrained_optimization"
        problem = "all hard constraints satisfied"
    elif sensitivity is None:
        strategy = "sensitivity_scan"
        problem = f"metrics failing without measured sensitivity: {failing}"
    elif budget_left is not None and budget_left <= 2:
        strategy = "local_constrained_optimization"
        problem = f"metrics failing {failing} with a nearly exhausted budget"
    elif stalled_rounds >= 3 or len(failing) >= len(case.constraints):
        strategy = "topology_search"
        problem = f"multiple constraints persistently failing: {failing}"
        confidence = "medium" if sensitive else "low"
    else:
        strategy = "local_constrained_optimization"
        problem = f"hard constraint(s) failing: {failing}"

    return {
        "current_problem": problem,
        "failing_constraints": failing,
        "passing_constraints": passing,
        "sensitive_parameters": sensitive,
        "protected_metrics": passing,
        "recommended_strategy": strategy,
        "strategy_hint": STRATEGY_HINTS.get(strategy, ""),
        "confidence": confidence,
        "requires_hfss_validation": True,
        "measured_only": True,
        "budget_left": budget_left,
        "stalled_rounds": stalled_rounds,
        "violations": violations,
    }


def pairwise_interactions(
    case: CaseSpec,
    evaluator: Any,
    base_params: dict[str, float],
    pairs: Sequence[tuple[str, str]],
    *,
    metric_kind: str,
    step_fraction: float = 0.1,
) -> dict[str, float]:
    """Second-order interaction: f(+,+) - f(+,-) - f(-,+) + f(-,-)."""
    space = case.family.param_space

    def step_for(name: str) -> float:
        spec = space[name]
        return max((float(spec.high) - float(spec.low)) * float(step_fraction), 1e-6)

    def evaluate(mods: dict[str, float]) -> float | None:
        params = {**base_params}
        for name, delta in mods.items():
            params[name] = min(max(params[name] + delta, space[name].low), space[name].high)
        return evaluator(params).metrics.get(metric_kind)

    out: dict[str, float] = {}
    for left, right in pairs:
        dl, dr = step_for(left), step_for(right)
        f_pp = evaluate({left: dl, right: dr})
        f_pm = evaluate({left: dl, right: -dr})
        f_mp = evaluate({left: -dl, right: dr})
        f_mm = evaluate({left: -dl, right: -dr})
        if None in (f_pp, f_pm, f_mp, f_mm):
            continue
        value = (f_pp - f_pm - f_mp + f_mm) / (4.0 * dl * dr)
        out[f"{left}|{right}"] = float(value)
    return out
