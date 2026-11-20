"""Physics-guided local search loop (module C + S6 glue).

One iteration:

    measure sensitivities -> linear proposal (trust region, protected metrics) ->
    new candidate -> measure -> keep only if it dominates the previous best

The loop works on any :class:`CampaignRunner`, so the same code runs against a
fake bridge in tests and against real guarded HFSS runs in a campaign.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence
import math

from .campaign import CampaignRunner
from .core import SpecError
from .optimize import compare
from .physics.local_opt import propose_local_step
from .physics.sensitivity import BudgetedEvaluator, EvaluationBudgetExceeded, compute_sensitivity


@dataclass
class LocalSearchStep:
    iteration: int
    params: dict[str, Any]
    proposal_accepted: bool
    predicted_change: float
    measured_metrics: dict[str, float | None]
    feasible: bool
    improved: bool
    reason: str = ""
    trust_region: float = 0.0
    agreement_ratio: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "params": dict(self.params),
            "proposal_accepted": self.proposal_accepted,
            "predicted_change": self.predicted_change,
            "metrics": dict(self.measured_metrics),
            "feasible": self.feasible,
            "improved": self.improved,
            "reason": self.reason,
            "trust_region": self.trust_region, "agreement_ratio": self.agreement_ratio,
        }


@dataclass
class LocalSearchResult:
    base_params: dict[str, Any]
    target_kind: str
    steps: list[LocalSearchStep] = field(default_factory=list)
    best_params: dict[str, Any] = field(default_factory=dict)
    evaluations: int = 0
    stop_reason: str = ""
    final_trust_region: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_params": dict(self.base_params),
            "target_kind": self.target_kind,
            "best_params": dict(self.best_params),
            "evaluations": self.evaluations,
            "steps": [step.to_dict() for step in self.steps],
            "stop_reason": self.stop_reason, "final_trust_region": self.final_trust_region,
        }


def _clamp_params(runner: CampaignRunner, params: dict[str, Any]) -> dict[str, Any]:
    space = runner.case.family.param_space
    clamped = {}
    for name, value in params.items():
        spec = space[name]
        if spec.kind == "choice":
            clamped[name] = value if value in spec.choices else spec.default
        else:
            clamped[name] = min(max(float(value), spec.low), spec.high)
    try:
        return space.validate(clamped)
    except SpecError:
        return None


def run_local_search(
    runner: CampaignRunner,
    *,
    target_kind: str,
    desired_change: float,
    protected: dict[str, float] | None = None,
    budget: int = 3,
    step_fraction: float = 0.15,
    trust_region: float = 0.4,
    max_iterations: int | None = None,
    min_trust_region: float = 0.025,
) -> LocalSearchResult:
    """Local search with a hard limit on *new measurements* (including probes).

    ``budget`` formerly counted outer iterations and could launch 2*d+1 solves
    per iteration. It now means measurements; ``max_iterations`` is separate.
    Successful recorded designs are reused without a charge. Prediction/error
    agreement shrinks or expands the trust radius after each measured proposal.
    """
    if not runner.records:
        raise ValueError("run at least one candidate before a local search")
    protected = protected or {}
    space = runner.case.family.param_space
    measured = [record for record in runner.records if record.status == "COMPLETED" and record.metrics]
    if not measured:
        raise ValueError("local search requires completed measurement evidence")
    direction = next((objective.direction for objective in runner.case.objectives
                      if objective.kind == target_kind), "maximize")
    feasible = [record for record in measured if record.feasible and record.metrics.get(target_kind) is not None]
    best = ((max if direction == "maximize" else min)(feasible, key=lambda row: row.metrics[target_kind])
            if feasible else min(measured, key=lambda row: sum(row.violations.values())))
    base_metrics = dict(best.metrics)   # protection is measured against the start
    result = LocalSearchResult(base_params=dict(best.params), target_kind=target_kind,
                               best_params=dict(best.params))
    cache = {runner.case.make_candidate(record.params).candidate_id: record for record in measured}
    evaluate = BudgetedEvaluator(runner.case, runner.evaluate, max(0, int(budget)), cache)
    radius = float(trust_region)
    if not all(math.isfinite(value) and value > 0 for value in (radius, min_trust_region)):
        raise ValueError("trust radii must be positive")
    if not math.isfinite(desired_change) or desired_change == 0:
        raise ValueError("desired_change must be finite and nonzero")
    matrices = {}
    limit = max(1, int(max_iterations)) if max_iterations is not None else max(1, int(budget))
    desired = abs(float(desired_change)) * (-1 if direction == "minimize" else 1)
    for iteration in range(limit):
        if not evaluate.remaining:
            result.stop_reason = "measurement budget exhausted"
            break
        key = runner.case.make_candidate(best.params).candidate_id
        if key not in matrices:
            try:
                matrices[key] = compute_sensitivity(runner.case, evaluate, dict(best.params),
                    metric_kinds=[target_kind] + list(protected), step_fraction=step_fraction)
            except EvaluationBudgetExceeded:
                result.stop_reason = "measurement budget exhausted during sensitivity"
                break
        matrix = matrices[key]
        if not evaluate.remaining:
            result.stop_reason = "measurement budget exhausted during sensitivity"
            break
        proposal = propose_local_step(
            matrix,
            target_kind=target_kind,
            desired_change=desired * min(1.0, radius / float(trust_region)),
            protected=protected,
            trust_region=radius,
        )
        if not proposal.accepted:
            result.steps.append(LocalSearchStep(
                iteration=iteration, params=dict(best.params), proposal_accepted=False,
                predicted_change=proposal.predicted_target_change,
                measured_metrics=dict(best.metrics), feasible=best.feasible,
                improved=False, reason=proposal.reason, trust_region=radius,
            ))
            result.stop_reason = proposal.reason
            break
        shifted = dict(best.params)
        for name, delta in proposal.delta.items():
            shifted[name] = float(shifted.get(name, 0.0)) + float(delta)
        candidate_params = _clamp_params(runner, shifted)
        if candidate_params is None:
            result.steps.append(LocalSearchStep(
                iteration=iteration, params=dict(best.params), proposal_accepted=False,
                predicted_change=proposal.predicted_target_change,
                measured_metrics=dict(best.metrics), feasible=best.feasible,
                improved=False, reason="proposal left the parameter bounds",
            ))
            break
        try:
            record = evaluate(candidate_params)
        except EvaluationBudgetExceeded:
            result.stop_reason = "measurement budget exhausted before proposal"
            break
        comparison = compare(_view(record), _view(best), runner.case.objectives)
        target_before = best.metrics.get(target_kind)
        target_after = record.metrics.get(target_kind)
        gain = None
        if target_before is not None and target_after is not None:
            gain = float(target_after) - float(target_before)
            if direction == "minimize":
                gain = -gain
        target_improved = gain is not None and gain > 0.05 * abs(desired_change)
        protected_kept = protected_ok(record, protected, base_metrics)
        improved = bool(record.feasible and target_improved and protected_kept
                        and comparison <= 0)
        predicted_gain = abs(proposal.predicted_target_change)
        ratio = (gain / predicted_gain if gain is not None and predicted_gain > 1e-12 else None)
        result.steps.append(LocalSearchStep(
            iteration=iteration, params=dict(record.params),
            proposal_accepted=True, predicted_change=proposal.predicted_target_change,
            measured_metrics=dict(record.metrics), feasible=record.feasible,
            improved=improved, reason="kept" if improved else "rejected; shrink trust region",
            trust_region=radius, agreement_ratio=ratio,
        ))
        if improved:
            best = record
            result.best_params = dict(record.params)
        if not improved or ratio is None or ratio < 0.25:
            radius *= 0.5
        elif ratio > 0.75:
            radius = min(float(trust_region) * 2, radius * 1.5)
        if radius < min_trust_region:
            result.stop_reason = "trust region below minimum"
            break
    result.evaluations = evaluate.calls
    result.final_trust_region = radius
    if not result.stop_reason:
        result.stop_reason = "measurement budget exhausted" if not evaluate.remaining else "iteration limit"
    return result


def _view(record: Any) -> Any:
    return type("E", (), {
        "feasible": record.feasible,
        "total_violation": sum(record.violations.values()),
        "metrics": record.metrics,
    })()


def protected_ok(record: Any, protected: dict[str, float], base_metrics: dict[str, float]) -> bool:
    """True when every protected metric stayed within its allowed change."""
    for kind, limit in protected.items():
        before = base_metrics.get(kind)
        after = record.metrics.get(kind)
        if before is None or after is None:
            return False
        if abs(float(after) - float(before)) > float(limit):
            return False
    return True



