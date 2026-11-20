"""Strategy controller: rule-based state machine (module S6).

The controller reads a compact context (constraints, stall/hyper-volume
history, sensitivity availability, budget, family count) and returns a
strategy plus machine-readable reasons. Every decision is logged so an agent
can explain *why* a method was chosen. Rules first; LLM/learning later.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

LOCAL = "LOCAL_OPTIMIZATION"
GLOBAL = "GLOBAL_SEARCH"
SURROGATE = "SURROGATE_OPTIMIZATION"
PHYSICS = "PHYSICS_DIAGNOSIS"
TOPOLOGY = "TOPOLOGY_SEARCH"
FINE_SCAN = "DETERMINISTIC_FINE_SCAN"
STOP = "STOP"

STRATEGIES = {LOCAL, GLOBAL, SURROGATE, PHYSICS, TOPOLOGY, FINE_SCAN, STOP}


@dataclass
class Decision:
    strategy: str
    reasons: list[str]
    budget_left: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"strategy": self.strategy, "reasons": list(self.reasons),
                "budget_left": self.budget_left, "details": dict(self.details)}


@dataclass
class ControllerContext:
    failing_constraints: Sequence[str] = ()
    passing_constraints: Sequence[str] = ()
    stalled_rounds: int = 0
    hypervolume_history: Sequence[float] = ()
    has_sensitivity: bool = False
    sensitivity_fixable: bool | None = None
    topology_worth_trying: bool = False
    budget_left: int | None = None
    surrogate_usable: bool = False
    families: int = 1
    objective_conflict: bool = False
    # spiky-landscape evidence: how many of the probes around the current best
    # point stayed feasible (None = no neighbourhood probes yet)
    neighbourhood_probes: int = 0
    neighbourhood_feasible: int = 0
    refinement_step: float | None = None
    has_measurements: bool = True


class StrategyController:
    def __init__(self) -> None:
        self.log: list[dict[str, Any]] = []

    def select(self, context: ControllerContext) -> Decision:
        decision = self._decide(context)
        self.log.append({"context": _context_dict(context), "decision": decision.to_dict()})
        return decision

    def _decide(self, context: ControllerContext) -> Decision:
        budget = context.budget_left
        if budget is not None and budget <= 0:
            return Decision(STOP, ["evaluation budget exhausted"], budget)
        if not context.has_measurements:
            return Decision(GLOBAL, ["no measured design yet; initialise with a space-filling design"], budget)

        failing = list(context.failing_constraints)
        spiky = (
            context.neighbourhood_probes >= 3
            and context.neighbourhood_feasible / max(1, context.neighbourhood_probes) < 0.5
        )
        if not failing:
            if spiky:
                return Decision(
                    FINE_SCAN,
                    [
                        "all hard constraints satisfied",
                        "only %d of %d neighbourhood probes stayed feasible"
                        % (context.neighbourhood_feasible, context.neighbourhood_probes),
                        "a sharp optimum makes surrogate extrapolation unreliable; "
                        "refine deterministically around the best point",
                    ],
                    budget,
                    {"refinement_step": context.refinement_step},
                )
            if context.surrogate_usable:
                return Decision(
                    SURROGATE,
                    ["all hard constraints satisfied", "surrogate is usable for refinement"],
                    budget,
                )
            return Decision(
                LOCAL,
                ["all hard constraints satisfied", "refine secondary objectives locally"],
                budget,
            )

        if context.topology_worth_trying:
            return Decision(
                TOPOLOGY,
                ["numeric triggers say the parameter space is exhausted"] + (
                    ["more than one family already explored"] if context.families > 1 else []
                ),
                budget,
                {"families": context.families},
            )

        if not context.has_sensitivity:
            return Decision(
                PHYSICS,
                [f"constraint(s) failing: {failing}", "no sensitivity matrix yet"],
                budget,
            )

        if context.sensitivity_fixable is False:
            return Decision(
                TOPOLOGY,
                ["sensitivity cannot fix the failing metric inside the trust region"],
                budget,
            )

        if context.objective_conflict or len(failing) > 1:
            if context.hypervolume_history and len(context.hypervolume_history) >= 2:
                return Decision(
                    GLOBAL,
                    ["multiple constraints conflict", "hyper-volume history available"],
                    budget,
                )
            return Decision(
                GLOBAL,
                ["multiple constraints conflict; explore broadly"],
                budget,
            )

        if context.stalled_rounds >= 3:
            return Decision(
                PHYSICS,
                [f"no progress for {context.stalled_rounds} rounds", "re-measure sensitivity"],
                budget,
            )

        return Decision(
            LOCAL,
            [f"constraint {failing[0]} failing; protect {list(context.passing_constraints)}"],
            budget,
        )


def _context_dict(context: ControllerContext) -> dict[str, Any]:
    return {
        "failing": list(context.failing_constraints),
        "passing": list(context.passing_constraints),
        "stalled_rounds": context.stalled_rounds,
        "hypervolume_history": list(context.hypervolume_history),
        "has_sensitivity": context.has_sensitivity,
        "sensitivity_fixable": context.sensitivity_fixable,
        "topology_worth_trying": context.topology_worth_trying,
        "budget_left": context.budget_left,
        "surrogate_usable": context.surrogate_usable,
        "families": context.families,
        "objective_conflict": context.objective_conflict,
        "neighbourhood_probes": context.neighbourhood_probes,
        "neighbourhood_feasible": context.neighbourhood_feasible,
        "refinement_step": context.refinement_step,
        "has_measurements": context.has_measurements,
    }
