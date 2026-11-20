"""Numeric rules that decide when parameter search is no longer enough (module D).

Ported from the task book's trigger list, but expressed as measurable rules so
the decision is auditable instead of a feeling:

* stalled improvement over the last N rounds,
* hyper-volume improvement below a relative threshold,
* one hard constraint violated by essentially every candidate,
* local sensitivity cannot fix the failing metric inside the trust region.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass
class TriggerConfig:
    stall_rounds: int = 3
    hypervolume_improvement: float = 0.02
    violation_ratio: float = 0.9
    min_budget_left: int = 4


def should_search_topology(
    *,
    history: Sequence[dict[str, Any]],
    hypervolumes: Sequence[float],
    constraint_violation_ratios: dict[str, float],
    sensitivity_fixable: bool | None,
    budget_left: int | None,
    config: TriggerConfig | None = None,
) -> dict[str, Any]:
    """Return a decision with explicit reasons; never a bare boolean."""
    config = config or TriggerConfig()
    reasons: list[str] = []

    if budget_left is not None and budget_left < config.min_budget_left:
        return {"decision": False, "reasons": ["budget too small for a structure search"],
                "budget_left": budget_left}

    recent = list(history)[-config.stall_rounds:]
    if len(recent) >= config.stall_rounds:
        feasible_counts = [entry.get("feasible", 0) for entry in recent]
        if len(set(feasible_counts)) == 1:
            reasons.append(
                f"no improvement in feasible count over the last {config.stall_rounds} rounds"
            )

    if len(hypervolumes) >= 2:
        first, last = float(hypervolumes[0]), float(hypervolumes[-1])
        reference = max(abs(first), 1e-9)
        if (last - first) / reference < config.hypervolume_improvement:
            reasons.append(
                "hyper-volume improvement below threshold "
                f"({(last - first) / reference:.3f} < {config.hypervolume_improvement})"
            )

    persistent = [
        name for name, ratio in constraint_violation_ratios.items()
        if ratio >= config.violation_ratio
    ]
    if persistent:
        reasons.append(f"constraint(s) violated by almost every candidate: {persistent}")

    if sensitivity_fixable is False:
        reasons.append("local sensitivity cannot fix the failing metric inside the trust region")

    decision = len(reasons) >= 2
    return {
        "decision": decision,
        "reasons": reasons,
        "persistent_constraints": persistent,
        "checked": {
            "stall_rounds": config.stall_rounds,
            "hypervolume_improvement": config.hypervolume_improvement,
            "violation_ratio": config.violation_ratio,
        },
    }
