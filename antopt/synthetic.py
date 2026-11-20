"""Synthetic benchmark: validates the optimisation stack before touching HFSS.

The problem is deliberately analytic so the Pareto front is known, which is
what the acceptance criteria for module A ask for (a new candidate that only
improves bandwidth while violating a gain constraint must not be judged
better). It uses the same CaseSpec/Constraint/Objective objects as a real
antenna case, so the pipeline under test is the production one.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

from .core import (
    CaseSpec,
    Constraint,
    FamilySpec,
    Objective,
    ParamSpace,
    ParamSpec,
    latin_hypercube,
    sample_population,
)
from .optimize import (
    Evaluation,
    ParetoArchive,
    evaluate_constraints,
    hypervolume_2d,
)


@dataclass
class SyntheticProblem:
    """Two-parameter, two-objective problem with one hard constraint.

    params:  x1, x2 in [0, 1] (normalised design variables)
    metrics: s11_db (<= -10 required), gain_h (>= target), sll_db (soft)
    """

    gain_target: float = 5.0
    s11_target: float = -10.0

    def metrics(self, params: dict[str, float]) -> dict[str, float]:
        x1 = float(params["x1"])
        x2 = float(params["x2"])
        # A trade-off surface: gain rises with x1, matching degrades with x1,
        # so the feasible region is a band (mirrors "bandwidth vs pattern").
        gain_h = 4.0 + 5.0 * x1 - 4.0 * (x2 - 0.5) ** 2
        s11_db = -18.0 + 10.0 * x1 + 2.0 * math.sin(6.0 * x2)
        sll_db = -16.0 + 3.0 * x1 - 2.0 * x2
        return {"realized_gain_h": gain_h, "peak_gain": gain_h, "s11": s11_db, "sll": sll_db}


def build_case(problem: SyntheticProblem | None = None) -> CaseSpec:
    problem = problem or SyntheticProblem()
    space = ParamSpace(
        [
            ParamSpec(name="x1", unit="", low=0.0, high=1.0, default=0.5,
                      physics="trade-off axis: gain up, matching down"),
            ParamSpec(name="x2", unit="", low=0.0, high=1.0, default=0.5,
                      physics="secondary axis: ripple / sidelobe shaping"),
        ]
    )
    family = FamilySpec(family_id="synthetic_v1", antenna_type="synthetic", param_space=space)
    return CaseSpec(
        case_id="synthetic",
        project="synthetic",
        design_type="synthetic",
        frequency_ghz=(24.0, 30.0),
        family=family,
        constraints=[
            Constraint(id="s11", kind="s11", operator="<=", target=problem.s11_target,
                       unit="dB", violation_scale=3.0),
            Constraint(id="peak_gain", kind="peak_gain", operator=">=",
                       target=problem.gain_target, unit="dBi", violation_scale=2.0),
        ],
        objectives=[
            Objective(id="gain_obj", kind="peak_gain", direction="maximize"),
            Objective(id="s11_obj", kind="s11", direction="minimize"),
        ],
    )


def evaluate_candidate(case: CaseSpec, params: dict[str, float],
                       problem: SyntheticProblem) -> Evaluation:
    metrics = problem.metrics(params)
    feasible, violations, total = evaluate_constraints(metrics, case)
    candidate = case.make_candidate(params, algorithm="synthetic")
    return Evaluation(
        candidate_id=candidate.candidate_id,
        family_id=case.family.family_id,
        params=dict(params),
        metrics=metrics,
        feasible=feasible,
        violations=violations,
        total_violation=total,
        run_id=None,
        status="COMPLETED",
    )


def run_sampling_round(
    case: CaseSpec,
    problem: SyntheticProblem,
    *,
    budget: int,
    batch: int = 8,
    seed: int = 0,
    sampler: str = "latin_hypercube",
) -> tuple[ParetoArchive, dict[str, Any]]:
    """Evaluate ``budget`` candidates in batches, keeping a Pareto archive."""
    archive = ParetoArchive(case.objectives)
    history: list[dict[str, Any]] = []
    evaluated = 0
    round_index = 0
    while evaluated < budget:
        count = min(batch, budget - evaluated)
        if sampler == "random":
            params_list = sample_population(case.family.param_space, count, seed=seed + round_index)
        else:
            params_list = latin_hypercube(case.family.param_space, count, seed=seed + round_index)
        for params in params_list:
            entry = evaluate_candidate(case, params, problem)
            archive.add(entry)
            evaluated += 1
        history.append(
            {
                "round": round_index,
                "evaluated": evaluated,
                "feasible": len(archive.feasible()),
                "front": len(archive.feasible_front()),
            }
        )
        round_index += 1
    summary = archive.improvement_summary()
    summary["history"] = history
    return archive, summary


def compare_strategies(
    *,
    budgets: Iterable[int] = (8, 16, 32, 64),
    seed: int = 7,
    problem: SyntheticProblem | None = None,
) -> dict[str, Any]:
    """Random vs stratified sampling at equal budgets (ablation baseline)."""
    problem = problem or SyntheticProblem()
    case = build_case(problem)
    rows = []
    for budget in budgets:
        random_archive, random_summary = run_sampling_round(
            case, problem, budget=budget, seed=seed, sampler="random"
        )
        lhs_archive, lhs_summary = run_sampling_round(
            case, problem, budget=budget, seed=seed, sampler="latin_hypercube"
        )
        rows.append(
            {
                "budget": budget,
                "random_feasible": random_summary["feasible"],
                "lhs_feasible": lhs_summary["feasible"],
                "random_best_gain": _best(random_archive, "realized_gain_h"),
                "lhs_best_gain": _best(lhs_archive, "realized_gain_h"),
                "random_front": len(random_archive.feasible_front()),
                "lhs_front": len(lhs_archive.feasible_front()),
            }
        )
    return {"seed": seed, "rows": rows}


def _best(archive: ParetoArchive, kind: str) -> float | None:
    entry = archive.best_by(kind, maximize=True)
    return None if entry is None else entry.metrics.get(kind)





