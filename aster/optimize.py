"""Feasibility-first multi-objective utilities (module A foundation).

No external dependency: fast non-dominated sorting, feasibility-first
comparison with normalised constraint violation, and a Pareto archive that
refuses to replace a feasible best with an infeasible candidate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .core import CaseSpec, Constraint, Objective


@dataclass
class Evaluation:
    """One measured candidate (metrics come from aedt_guard, never from a model)."""

    candidate_id: str
    family_id: str
    params: dict[str, Any]
    metrics: dict[str, float | None] = field(default_factory=dict)
    feasible: bool = False
    violations: dict[str, float] = field(default_factory=dict)
    total_violation: float = 0.0
    run_id: str | None = None
    status: str = "COMPLETED"
    origin: dict[str, Any] = field(default_factory=dict)

    def objective_values(self, objectives: Sequence[Objective]) -> list[float | None]:
        return [self.metrics.get(o.kind) for o in objectives]


def metric_value(metrics: dict[str, float | None], kind: str) -> float | None:
    return metrics.get(kind)


def constraint_violation(value: float | None, constraint: Constraint) -> float:
    """Normalised violation: 0 when satisfied, positive when not."""
    if value is None:
        return 1000.0  # missing measurement must never look feasible
    target = float(constraint.target)
    if constraint.operator == "<=":
        excess = float(value) - target
    elif constraint.operator == ">=":
        excess = target - float(value)
    else:
        excess = abs(float(value) - target)
    if excess <= 0:
        return 0.0
    return excess / float(constraint.violation_scale)


def evaluate_constraints(
    metrics: dict[str, float | None], case: CaseSpec
) -> tuple[bool, dict[str, float], float]:
    violations: dict[str, float] = {}
    total = 0.0
    for constraint in case.constraints:
        violation = constraint_violation(metrics.get(constraint.kind), constraint)
        if violation > 0:
            violations[constraint.id] = violation
            total += violation
    return (not violations), violations, total


def dominates(left: Evaluation, right: Evaluation, objectives: Sequence[Objective]) -> bool:
    """Pareto dominance on objectives (feasible designs only, by convention)."""
    if not (left.feasible and right.feasible):
        raise ValueError("dominance is only defined for feasible candidates")
    better = False
    for objective in objectives:
        a = left.metrics.get(objective.kind)
        b = right.metrics.get(objective.kind)
        if a is None or b is None:
            raise ValueError("objectives require measured values")
        if objective.direction == "maximize":
            if a < b:
                return False
            better = better or a > b
        else:
            if a > b:
                return False
            better = better or a < b
    return better


def feasibility_first_key(entry: Evaluation) -> tuple[int, float]:
    """Sort key: feasible first, then the normalised total violation."""
    return (0 if entry.feasible else 1, float(entry.total_violation))


def compare(left: Evaluation, right: Evaluation, objectives: Sequence[Objective]) -> int:
    """Return -1 if left is preferable, 0 if incomparable, 1 if right is preferable."""
    if left.feasible != right.feasible:
        return -1 if left.feasible else 1
    if not left.feasible:
        if left.total_violation < right.total_violation:
            return -1
        if right.total_violation < left.total_violation:
            return 1
        return 0
    if dominates(left, right, objectives):
        return -1
    if dominates(right, left, objectives):
        return 1
    return 0


def fast_non_dominated_sort(
    population: Sequence[Evaluation], objectives: Sequence[Objective]
) -> list[list[Evaluation]]:
    """NSGA-II style layering over the feasible part of the population."""
    feasible = [entry for entry in population if entry.feasible]
    if not feasible:
        return []
    dominates_map: dict[str, list[Evaluation]] = {e.candidate_id: [] for e in feasible}
    dominated_count: dict[str, int] = {e.candidate_id: 0 for e in feasible}
    fronts: list[list[Evaluation]] = [[]]
    by_id = {e.candidate_id: e for e in feasible}
    for left in feasible:
        for right in feasible:
            if left is right:
                continue
            if dominates(left, right, objectives):
                dominates_map[left.candidate_id].append(right)
            elif dominates(right, left, objectives):
                dominated_count[left.candidate_id] += 1
        if dominated_count[left.candidate_id] == 0:
            fronts[0].append(left)
    current = 0
    while fronts[current]:
        next_front: list[Evaluation] = []
        for entry in fronts[current]:
            for dominated in dominates_map[entry.candidate_id]:
                dominated_count[dominated.candidate_id] -= 1
                if dominated_count[dominated.candidate_id] == 0:
                    next_front.append(dominated)
        current += 1
        fronts.append(next_front)
        if current > len(feasible) + 1:  # defensive guard against cycles
            break
    return [front for front in fronts if front]


def hypervolume_2d(
    population: Sequence[Evaluation],
    x_objective: Objective,
    y_objective: Objective,
    reference: tuple[float, float],
) -> float:
    """2-D hypervolume of the feasible front (maximisation form, reference point)."""
    points = []
    for entry in population:
        if not entry.feasible:
            continue
        x = entry.metrics.get(x_objective.kind)
        y = entry.metrics.get(y_objective.kind)
        if x is None or y is None:
            continue
        if x_objective.direction == "minimize":
            x = -x
        if y_objective.direction == "minimize":
            y = -y
        ref_x, ref_y = reference
        if x > ref_x and y > ref_y:
            points.append((x, y))
    if not points:
        return 0.0
    points.sort(key=lambda item: item[0], reverse=True)
    area = 0.0
    best_y = reference[1]
    for x, y in points:
        if y > best_y:
            area += (x - reference[0]) * (y - best_y)
            best_y = y
    return float(area)


def pareto_archive(
    population: Iterable[Evaluation], objectives: Sequence[Objective]
) -> list[Evaluation]:
    """Flatten the first non-dominated fronts into one archive list."""
    fronts = fast_non_dominated_sort(list(population), objectives)
    return [entry for front in fronts for entry in front] if fronts else []


class ParetoArchive:
    """Keeps every evaluated candidate and exposes the current front.

    Infeasible candidates never enter ``feasible_front``; a previously feasible
    best is therefore never replaced by an infeasible proposal.
    """

    def __init__(self, objectives: Sequence[Objective]):
        self.objectives = list(objectives)
        self.entries: dict[str, Evaluation] = {}

    def __len__(self) -> int:
        return len(self.entries)

    def add(self, entry: Evaluation) -> None:
        self.entries[entry.candidate_id] = entry

    def update(self, entries: Iterable[Evaluation]) -> None:
        for entry in entries:
            self.add(entry)

    def feasible(self) -> list[Evaluation]:
        return [e for e in self.entries.values() if e.feasible]

    def feasible_front(self) -> list[Evaluation]:
        return pareto_archive(self.feasible(), self.objectives)

    def first_front(self) -> list[Evaluation]:
        """Only the non-dominated front itself; ``feasible_front`` flattens every front.

        The export needs the true Pareto front, so candidates from the second and later
        fronts must not appear in a "Pareto front" file.
        """
        fronts = fast_non_dominated_sort(self.feasible(), self.objectives)
        return list(fronts[0]) if fronts else []

    def best_by(self, kind: str, maximize: bool = True) -> Evaluation | None:
        pool = [e for e in self.feasible() if e.metrics.get(kind) is not None]
        if not pool:
            return None
        return (max if maximize else min)(pool, key=lambda e: e.metrics[kind])

    def improvement_summary(self) -> dict[str, Any]:
        fronts = fast_non_dominated_sort(list(self.entries.values()), self.objectives)
        return {
            "evaluated": len(self.entries),
            "feasible": len(self.feasible()),
            "front_sizes": [len(front) for front in fronts],
            "infeasible": len(self.entries) - len(self.feasible()),
        }
