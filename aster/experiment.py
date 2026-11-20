"""Experiment driver: the controller actually runs the campaign.

The controller (S6) only *decides*; this module executes the decision against an
executor (synthetic in tests, guarded HFSS in a campaign) and feeds the outcome
back. Every step is logged, so an agent can explain what it did and why.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .controller import (
    GLOBAL,
    FINE_SCAN,
    Decision,
    LOCAL,
    PHYSICS,
    STOP,
    SURROGATE,
    TOPOLOGY,
    ControllerContext,
    StrategyController,
)
from .core import CaseSpec, latin_hypercube, sample_population
from .optimize import Evaluation, ParetoArchive, hypervolume_2d
from .physics.diagnosis import diagnose
from .physics.local_opt import propose_local_step
from .physics.sensitivity import BudgetedEvaluator, EvaluationBudgetExceeded, compute_sensitivity


@dataclass
class ExperimentStep:
    index: int
    strategy: str
    reasons: list[str]
    evaluations: int
    feasible: int
    best: dict[str, float | None] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"index": self.index, "strategy": self.strategy, "reasons": list(self.reasons),
                "evaluations": self.evaluations, "feasible": self.feasible,
                "best": dict(self.best)}


class _CountingEvaluator(BudgetedEvaluator):
    def __init__(self, case: CaseSpec, executor: Any, budget: int | None = None):
        super().__init__(case, lambda params: executor.evaluate(case.make_candidate(params)), budget)
        self.executor = executor
        self.on_evaluation = lambda entry: None

    def __call__(self, params):
        return self.evaluate_case(self.case, params)

    def evaluate_case(self, case: CaseSpec, params: dict[str, Any]) -> Evaluation:
        """Evaluate an explicitly supplied family through the same hard budget.

        Callbacks own their family/model setup; this does not change an HFSS
        project implicitly. Every returned evaluation is archived exactly here.
        """
        candidate = case.make_candidate(params)
        if candidate.candidate_id in self.cache:
            return self.cache[candidate.candidate_id]
        if self.remaining is not None and self.remaining <= 0:
            raise EvaluationBudgetExceeded("measurement budget exhausted before starting a new run")
        self.calls += 1
        entry = self.executor.evaluate(candidate)
        if entry.status == "COMPLETED":
            self.cache[candidate.candidate_id] = entry
        self.on_evaluation(entry)
        return entry


def _search_base(archive: ParetoArchive, case: CaseSpec) -> Evaluation | None:
    # A descendant callback does not implicitly activate a different model for
    # subsequent local steps. Its evidence stays in the archive for comparison.
    measured = [entry for entry in archive.entries.values() if entry.status == "COMPLETED"
                and entry.family_id == case.family.family_id]
    feasible = [entry for entry in measured if entry.feasible
                and entry.metrics.get(case.objectives[0].kind) is not None]
    if feasible:
        return (max if case.objectives[0].direction == "maximize" else min)(
            feasible, key=lambda entry: entry.metrics[case.objectives[0].kind])
    return min(measured, key=lambda entry: entry.total_violation) if measured else None


def run_experiment(
    case: CaseSpec,
    executor: Any,
    *,
    budget: int = 20,
    batch: int = 4,
    seed: int = 0,
    reference: tuple[float, float] | None = None,
    controller: StrategyController | None = None,
    strategy_callbacks: Mapping[str, Callable[..., Any]] | None = None,
) -> dict[str, Any]:
    """Run an explicitly supported strategy, with one hard measurement budget.

    SURROGATE/TOPOLOGY/FINE_SCAN require a callback. It receives case, archive,
    evaluator, budget_left, batch, seed, and must return parameter dictionaries
    or evaluate through that evaluator. No geometry is changed implicitly.
    """
    evaluator = _CountingEvaluator(case, executor, max(0, int(budget)))
    callbacks = dict(strategy_callbacks or {})
    archive = ParetoArchive(case.objectives)
    evaluator.on_evaluation = archive.add
    controller = controller or StrategyController()
    steps: list[ExperimentStep] = []
    hypervolumes: list[float] = []
    stalled = 0
    no_progress = 0
    max_steps = max(4, budget * 3)
    matrix = None
    diagnostics = []
    def measure(params):
        entry = evaluator(params)
        archive.add(entry)
        return entry
    while evaluator.calls < budget and len(steps) < max_steps:
        before_calls = evaluator.calls
        before_best = _search_base(archive, case)
        before_hv = hypervolumes[-1] if hypervolumes else None
        before_score = (before_best.total_violation if before_best is not None else float("inf"))
        best = before_best
        failing = ([c.id for c in case.constraints] if best is None else
                   [c.id for c in case.constraints if float(best.violations.get(c.id, 0)) > 0])
        context = ControllerContext(
            failing_constraints=failing,
            passing_constraints=[c.id for c in case.constraints if c.id not in failing],
            stalled_rounds=stalled,
            hypervolume_history=hypervolumes,
            has_sensitivity=matrix is not None,
            sensitivity_fixable=None,
            topology_worth_trying=stalled >= 3,
            budget_left=budget - evaluator.calls,
            has_measurements=best is not None,
            surrogate_usable=SURROGATE in callbacks,
        )
        decision = controller.select(context)
        if decision.strategy in {SURROGATE, TOPOLOGY, FINE_SCAN} and decision.strategy not in callbacks:
            requested = decision.strategy
            decision = Decision(STOP, [*decision.reasons, "strategy callback unavailable: " + requested],
                                budget - evaluator.calls, {"requested_strategy": requested})
            controller.log[-1]["decision"] = decision.to_dict()
        if decision.strategy == STOP:
            steps.append(_record(len(steps), decision, evaluator, archive, case))
            break
        try:
            if decision.strategy in callbacks:
                proposals = callbacks[decision.strategy](
                    case=case, archive=archive, evaluator=evaluator,
                    budget_left=evaluator.remaining, batch=batch, seed=seed + len(steps))
                for params in proposals or ():
                    measure(params)
            elif decision.strategy == GLOBAL:
                count = max(0, min(batch, budget - evaluator.calls))
                params_list = (latin_hypercube(case.family.param_space, count, seed=seed + len(steps))
                               if count > 0 else [])
                for params in params_list:
                    measure(params)
            elif decision.strategy in {PHYSICS, LOCAL}:
                base = _search_base(archive, case)
                measured = 0
                if base is not None:
                    kinds = [case.objectives[0].kind] + [c.kind for c in case.constraints]
                    matrix = compute_sensitivity(
                        case, evaluator, dict(base.params), metric_kinds=kinds,
                        step_fraction=0.15)
                    target = case.objectives[0].kind
                    target_direction = case.objectives[0].direction
                    proposal = propose_local_step(
                        matrix, target_kind=target,
                        desired_change=(-1.0 if target_direction == "minimize" else 1.0), trust_region=0.4)
                    if proposal.accepted:
                        params = {**base.params}
                        for name, delta in proposal.delta.items():
                            params[name] = float(params[name]) + float(delta)
                        try:
                            measure(case.family.param_space.complete(params))
                            measured += 1
                        except EvaluationBudgetExceeded:
                            raise
                        except (ValueError, TypeError):
                            pass
                    diagnostics.append(diagnose(case, dict(base.metrics), matrix,
                        budget_left=evaluator.remaining, stalled_rounds=stalled))
                if measured == 0 and evaluator.remaining:
                    # A rejected or cached proposal can buy knowledge only if
                    # the shared measurement gate still has budget.
                    count = max(0, min(batch, budget - evaluator.calls))
                    for params in latin_hypercube(case.family.param_space, count,
                                                  seed=seed + len(steps) + 17):
                        measure(params)
            else:
                raise ValueError("unsupported strategy: " + decision.strategy)
        except EvaluationBudgetExceeded:
            # Some Jacobian columns can be completed with the final budget slots;
            # no callback or fallback is allowed to start one more measurement.
            pass
        archive.update(evaluator.cache.values())
        if reference is not None and len(case.objectives) >= 2:
            hypervolumes.append(hypervolume_2d(archive.feasible(), case.objectives[0],
                                               case.objectives[1], reference))
        steps.append(_record(len(steps), decision, evaluator, archive, case))
        after_best = _search_base(archive, case)
        improved = bool(after_best is not None and after_best.total_violation < before_score - 1e-12)
        if reference is not None and hypervolumes:
            improved = improved or before_hv is None or hypervolumes[-1] > before_hv + 1e-12
        elif after_best is not None and before_best is not None and after_best.feasible:
            direction = 1 if case.objectives[0].direction == "maximize" else -1
            old, new = before_best.metrics.get(case.objectives[0].kind), after_best.metrics.get(case.objectives[0].kind)
            improved = improved or (old is not None and new is not None and direction * (new - old) > 1e-12)
        stalled = 0 if improved else stalled + 1
        # A step that measured nothing new must not spin forever: every
        # candidate in its probe set was already in the cache.
        no_progress = no_progress + 1 if evaluator.calls == before_calls else 0
        if no_progress >= 2:
            break
    best = archive.best_by(case.objectives[0].kind,
                           maximize=case.objectives[0].direction == "maximize")
    return {
        "case": case.case_id,
        "evaluations": evaluator.calls,
        "feasible": len(archive.feasible()),
        "steps": [step.to_dict() for step in steps],
        "decisions": list(controller.log),
        "hypervolumes": list(hypervolumes),
        "diagnostics": diagnostics,
        "stop_reason": ("budget exhausted" if evaluator.calls >= budget else
                        "no new measurements" if no_progress >= 2 else
                        "controller stopped" if steps and steps[-1].strategy == STOP else "step limit"),
        "archive": archive,
        "best": None if best is None else best.to_dict() if hasattr(best, "to_dict") else {
            "candidate_id": best.candidate_id,
            "metrics": dict(best.metrics),
            "params": dict(best.params),
            "feasible": best.feasible,
        },
    }


def _record(index: int, decision: Any, evaluator: _CountingEvaluator,
            archive: ParetoArchive, case: CaseSpec) -> ExperimentStep:
    target = case.objectives[0]
    best_entry = archive.best_by(target.kind, maximize=target.direction == "maximize")
    best = {} if best_entry is None else dict(best_entry.metrics)
    return ExperimentStep(index=index, strategy=decision.strategy,
                          reasons=list(decision.reasons),
                          evaluations=evaluator.calls,
                          feasible=len(archive.feasible()), best=best)





