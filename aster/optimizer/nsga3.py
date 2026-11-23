"""Module A: constrained multi-objective optimisation with pymoo (NSGA-III).

NSGA-III replaces NSGA-II's crowding distance with a set of reference directions, which keeps
the population spread over a structured sample of the Pareto front instead of collapsing
toward a few well-populated regions. On our problems the objectives are few (two or three)
but the front is narrow and skewed, so the reference-direction mechanism is exactly what the
third-generation algorithm adds over the second.

The problem adapter, feasibility-first constraint handling, evaluation cache and budget
accounting are shared with the NSGA-II module so the two algorithms stay directly comparable.

Reproducibility note: the initial population is seeded, but pymoo's niching breaks exact ties
with its own randomness, so two runs with the same seed can differ by a couple of evaluations.
Budgets and comparisons therefore use several seeds instead of assuming bitwise repeats.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from ..core import CaseSpec
from .nsga2 import CachedEvaluator, _case_problem, _seeded_sampling


def reference_directions(n_obj: int, pop_size: int) -> np.ndarray:
    """Pick a Das-Dennis partition whose direction count is at least the population size."""
    from pymoo.util.ref_dirs import get_reference_directions  # noqa: PLC0415

    if n_obj < 1:
        raise ValueError("at least one objective is required")
    if pop_size <= 0:
        raise ValueError("population size must be positive")
    partitions = 1
    while partitions < 40 and _comb(partitions + n_obj - 1, n_obj - 1) < pop_size:
        partitions += 1
    # pymoo caches the generator and NSGA-III mutates the array it is given, so hand out a
    # fresh copy or a second run in the same process sees already-normalised directions
    return np.array(get_reference_directions("das-dennis", n_obj,
                                             n_partitions=partitions), copy=True)


def _comb(n: int, k: int) -> int:
    result = 1
    for index in range(k):
        result = result * (n - index) // (index + 1)
    return result


def run_nsga3(
    case: CaseSpec,
    executor: Any,
    *,
    pop_size: int = 8,
    generations: int = 4,
    seed: int = 0,
    budget: int | None = None,
    initial_params=None,
) -> dict[str, Any]:
    """Run NSGA-III and return the evaluated archive plus generation history."""
    try:
        from pymoo.algorithms.moo.nsga3 import NSGA3  # noqa: PLC0415
        from pymoo.optimize import minimize  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("pymoo is required for module A (pip install pymoo)") from exc

    objectives = case.objectives
    evaluator = CachedEvaluator(case, executor, budget=budget)
    history: list[dict[str, Any]] = []

    problem = _case_problem(case, evaluator)
    directions = reference_directions(len(objectives), int(pop_size))
    algorithm = NSGA3(pop_size=int(pop_size), ref_dirs=directions,
                      **_seeded_sampling(case, initial_params, int(pop_size), seed))
    result = minimize(
        problem,
        algorithm,
        ("n_gen", int(generations)),
        seed=int(seed),
        verbose=False,
        save_history=False,
    )
    entries = list(evaluator.entries.values())
    feasible = [entry for entry in entries if entry.feasible]
    history.append(
        {
            "evaluations": evaluator.calls,
            "feasible": len(feasible),
            "budget_exhausted": evaluator.exhausted,
            "generations": int(generations),
            "reference_directions": int(len(directions)),
        }
    )
    return {
        "case": case.case_id,
        "entries": entries,
        "evaluations": evaluator.calls,
        "feasible": len(feasible),
        "budget_exhausted": evaluator.exhausted,
        "history": history,
        "result_x": None if result is None else getattr(result, "X", None),
    }
