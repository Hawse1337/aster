"""Module A: constrained multi-objective optimisation with pymoo (NSGA-II).

The problem adapter is generic: it only uses the case's parameter space,
constraints (converted to the standard ``g(x) <= 0`` convention with normalised
violation) and objectives. Feasibility-first selection comes from pymoo's
constraint domination, which matches the acceptance criterion that a candidate
improving one metric while violating a hard constraint must not win.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..core import CaseSpec
from ..optimize import Evaluation, constraint_violation

MISSING_PENALTY = 1e3


@dataclass
class CachedEvaluator:
    """Evaluates candidates at most once (the archive is the cache)."""

    case: CaseSpec
    executor: Any
    entries: dict[str, Evaluation] = field(default_factory=dict)
    calls: int = 0
    budget: int | None = None
    exhausted: bool = False

    def __call__(self, params: dict[str, Any]) -> Evaluation:
        candidate = self.case.make_candidate(params)
        cached = self.entries.get(candidate.candidate_id)
        if cached is not None:
            return cached
        if self.budget is not None and self.calls >= self.budget:
            self.exhausted = True
            return Evaluation(
                candidate_id=candidate.candidate_id,
                family_id=candidate.family_id,
                params=dict(params),
                metrics={},
                feasible=False,
                total_violation=MISSING_PENALTY,
                status="BUDGET_EXHAUSTED",
            )
        entry = self.executor.evaluate(candidate)
        self.calls += 1
        self.entries[candidate.candidate_id] = entry
        return entry


def _case_problem(case: CaseSpec, evaluator: CachedEvaluator):
    """Map measured outcomes to the same minimisation problem for both NSGA variants."""
    from pymoo.core.problem import Problem  # noqa: PLC0415

    space = case.family.param_space

    class CaseProblem(Problem):
        def _evaluate(self, X, out, *args, **kwargs):  # noqa: N802
            f_values = []
            g_values = []
            for row in X:
                entry = evaluator(space.from_vector(row))
                values = entry.objective_values(case.objectives)
                f_row = []
                for objective, value in zip(case.objectives, values):
                    if value is None:
                        f_row.append(MISSING_PENALTY)
                    else:
                        f_row.append(-value if objective.direction == "maximize" else value)
                g_row = [constraint_violation(entry.metrics.get(c.kind), c)
                         for c in case.constraints]
                # Failed/budget-limited runs can carry partial metrics. Even when
                # all hard limits pass, rank only complete, eligible evaluations.
                invalid = (None in values or entry.status not in {"COMPLETED", "PREDICTED"}
                           or (not entry.feasible and not any(g_row)))
                g_row.append(MISSING_PENALTY if invalid else 0.0)
                f_values.append(f_row)
                g_values.append(g_row)
            out["F"] = np.asarray(f_values, dtype=float)
            out["G"] = np.asarray(g_values, dtype=float)

    return CaseProblem(n_var=len(space), n_obj=len(case.objectives),
                       n_ieq_constr=len(case.constraints) + 1,
                       xl=np.zeros(len(space)), xu=np.ones(len(space)))


def run_nsga2(
    case: CaseSpec,
    executor: Any,
    *,
    pop_size: int = 8,
    generations: int = 4,
    seed: int = 0,
    budget: int | None = None,
) -> dict[str, Any]:
    """Run NSGA-II and return the evaluated archive plus generation history."""
    try:
        from pymoo.algorithms.moo.nsga2 import NSGA2  # noqa: PLC0415
        from pymoo.optimize import minimize  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("pymoo is required for module A (pip install pymoo)") from exc

    evaluator = CachedEvaluator(case, executor, budget=budget)
    history: list[dict[str, Any]] = []

    problem = _case_problem(case, evaluator)
    algorithm = NSGA2(pop_size=int(pop_size))
    result = minimize(
        problem,
        algorithm,
        ("n_gen", int(generations)),
        seed=int(seed),
        verbose=False,
        save_history=False,
    )
    entries = list(evaluator.entries.values())
    feasible = [e for e in entries if e.feasible]
    history.append(
        {
            "evaluations": evaluator.calls,
            "feasible": len(feasible),
            "budget_exhausted": evaluator.exhausted,
            "generations": int(generations),
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
