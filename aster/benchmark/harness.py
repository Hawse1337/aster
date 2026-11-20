"""Benchmark harness: compare search strategies on equal evaluation budgets."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from ..core import CaseSpec, latin_hypercube, sample_population
from ..optimize import (
    Evaluation,
    ParetoArchive,
    evaluate_constraints,
    hypervolume_2d,
)


@dataclass
class StrategyResult:
    name: str
    evaluations: int
    feasible: int
    front_size: int
    best_by_objective: dict[str, float | None] = field(default_factory=dict)
    hypervolume: float | None = None
    first_feasible_at: int | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "evaluations": self.evaluations,
            "feasible": self.feasible,
            "front_size": self.front_size,
            "best_by_objective": self.best_by_objective,
            "hypervolume": self.hypervolume,
            "first_feasible_at": self.first_feasible_at,
        }


def _summarize(
    name: str,
    archive: ParetoArchive,
    case: CaseSpec,
    evaluations: int,
    history: list[dict[str, Any]],
    reference: tuple[float, float] | None = None,
    first_feasible_at: int | None = None,
) -> StrategyResult:
    front = archive.feasible_front()
    best: dict[str, float | None] = {}
    for objective in case.objectives:
        entry = archive.best_by(objective.kind, maximize=objective.direction == "maximize")
        best[objective.kind] = None if entry is None else entry.metrics.get(objective.kind)
    volume = None
    if reference is not None and len(case.objectives) >= 2:
        volume = hypervolume_2d(archive.feasible(), case.objectives[0], case.objectives[1],
                               reference)
    return StrategyResult(
        name=name,
        evaluations=evaluations,
        feasible=len(archive.feasible()),
        front_size=len(front),
        best_by_objective=best,
        hypervolume=volume,
        first_feasible_at=first_feasible_at,
        history=history,
    )


def run_strategy(
    case: CaseSpec,
    executor: Any,
    strategy: str,
    *,
    budget: int,
    batch: int = 4,
    seed: int = 0,
    reference: tuple[float, float] | None = None,
) -> StrategyResult:
    if strategy in {"random", "lhs"}:
        archive = ParetoArchive(case.objectives)
        history: list[dict[str, Any]] = []
        evaluated = 0
        first_feasible: int | None = None
        round_index = 0
        while evaluated < budget:
            count = min(batch, budget - evaluated)
            if strategy == "random":
                samples = sample_population(case.family.param_space, count, seed=seed + round_index)
            else:
                samples = latin_hypercube(case.family.param_space, count, seed=seed + round_index)
            for params in samples:
                candidate = case.make_candidate(params, algorithm=strategy, round=round_index)
                entry = executor.evaluate(candidate)
                if first_feasible is None and entry.feasible:
                    first_feasible = evaluated + 1
                archive.add(entry)
                evaluated += 1
            history.append({"round": round_index, "evaluated": evaluated,
                            "feasible": len(archive.feasible())})
            round_index += 1
        return _summarize(strategy, archive, case, evaluated, history, reference,
                          first_feasible_at=first_feasible)

    if strategy in ("nsga2", "nsga3"):
        if strategy == "nsga2":
            from ..optimizer.nsga2 import run_nsga2 as runner  # noqa: PLC0415
        else:
            from ..optimizer.nsga3 import run_nsga3 as runner  # noqa: PLC0415

        batch_size = max(2, min(batch * 2, 32))
        result = runner(
            case,
            executor,
            pop_size=batch_size,
            generations=max(1, budget // batch_size),
            seed=seed,
            budget=budget,
        )
        archive = ParetoArchive(case.objectives)
        archive.update(result["entries"])
        first_feasible = next(
            (index + 1 for index, entry in enumerate(result["entries"]) if entry.feasible),
            None)
        return _summarize(strategy, archive, case, result["evaluations"],
                          result["history"], reference,
                          first_feasible_at=first_feasible)

    raise ValueError(f"unknown strategy: {strategy}")


def compare(
    case: CaseSpec,
    executor: Any,
    *,
    budget: int,
    strategies: Sequence[str] = ("random", "lhs", "nsga2", "nsga3"),
    seed: int = 0,
    reference: tuple[float, float] | None = None,
) -> dict[str, Any]:
    rows = [
        run_strategy(case, executor, name, budget=budget, seed=seed, reference=reference)
        for name in strategies
    ]
    return {
        "case": case.case_id,
        "budget": budget,
        "seed": seed,
        "rows": [row.to_dict() for row in rows],
    }
