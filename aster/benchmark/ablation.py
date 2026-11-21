"""Ablation harness: surrogate-assisted search and multi-seed comparison.

Implements the fifth baseline (surrogate-assisted Bayesian loop) and aggregates
every strategy over several random seeds so conclusions do not rest on a single
run. Physics-guided and topology strategies are exercised through the
controller tests; here we measure the numerical search strategies.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from ..core import CaseSpec, latin_hypercube, sample_population
from ..optimize import Evaluation, ParetoArchive, hypervolume_2d
from ..surrogate.dataset import Dataset, Sample
from ..surrogate.models import GaussianProcessModel, cross_validate, model_is_usable
from ..surrogate.selection import select_candidates
from .harness import StrategyResult, run_strategy
from .measured_surface import MeasuredSurfaceExecutor

SEARCH_STRATEGIES = ("random", "lhs", "nsga2", "nsga3", "surrogate")


def run_surrogate_assisted(
    case: CaseSpec,
    executor: Any,
    *,
    budget: int,
    initial: int = 8,
    batch: int = 4,
    pool: int = 256,
    seed: int = 0,
    reference: tuple[float, float] | None = None,
) -> StrategyResult:
    """LHS seeding, then GPR + EI to choose the next measured candidates."""
    space = case.family.param_space
    target = case.objectives[0]
    archive = ParetoArchive(case.objectives)
    # This dataset is private to one benchmark run. A fitted surface is an
    # offline oracle whose observations retain their prediction provenance.
    surface_run = isinstance(executor, MeasuredSurfaceExecutor)
    sample_status = "PREDICTED" if surface_run else "COMPLETED"
    sample_source = "predicted" if surface_run else "measured"
    dataset = Dataset()
    history: list[dict[str, Any]] = []
    evaluated = 0
    first_feasible: int | None = None
    rng = np.random.RandomState(seed)
    param_names = [spec.name for spec in space]

    seen: set[tuple[float, ...]] = set()

    def key_of(params: dict[str, Any]) -> tuple[float, ...]:
        return tuple(round(float(params[spec.name]), 6) for spec in space)

    def measure(params: dict[str, Any]) -> Evaluation:
        nonlocal evaluated, first_feasible
        seen.add(key_of(params))
        candidate = case.make_candidate(params, algorithm="surrogate", round=len(history))
        entry = executor.evaluate(candidate)
        if first_feasible is None and entry.feasible:
            first_feasible = evaluated + 1
        archive.add(entry)
        evaluated += 1
        value = entry.metrics.get(target.kind)
        if (entry.status != sample_status or value is None or not np.isfinite(value)
                or not np.isfinite(entry.total_violation)):
            return entry
        targets = {target.kind: float(value)}
        targets["violation"] = max(0.0, float(entry.total_violation))
        dataset.add(Sample(candidate_id=candidate.candidate_id,
                           params={k: float(v) for k, v in entry.params.items()},
                           targets=targets,
                           source=sample_source, run_id=entry.run_id,
                           status=entry.status))
        return entry

    for params in latin_hypercube(space, min(initial, budget), seed=seed):
        measure(params)
    history.append({"stage": "seed", "evaluated": evaluated,
                    "feasible": len(archive.feasible())})

    model = GaussianProcessModel()
    violation_model = GaussianProcessModel()
    have_violation_model = False
    while evaluated < budget:
        if len(dataset) >= 4:
            x, y = dataset.matrix(target.kind, param_names, measured_only=not surface_run)
            report = cross_validate(model, x, y, folds=3)
            model.fit(x, y)
            vx, vy = dataset.matrix("violation", param_names, measured_only=not surface_run)
            if len(vx) >= 4:
                violation_model.fit(vx, vy)
                have_violation_model = True
        else:
            report = None
        pool_params = []
        attempt = 0
        while len(pool_params) < min(pool, 8 * max(1, budget)) and attempt < 8:
            draw = sample_population(space, min(pool, 8 * max(1, budget)),
                                     seed=seed + evaluated + 1000 * attempt)
            pool_params.extend(p for p in draw if key_of(p) not in seen)
            attempt += 1
        if not pool_params:
            break
        pool_x = np.array([[float(p[name]) for name in param_names]
                           for p in pool_params], dtype=float)
        remaining = min(batch, budget - evaluated)
        if report is not None and dataset:
            mean, std = model.predict(pool_x)
            best = float(np.max(y))
            if have_violation_model:
                predicted, _ = violation_model.predict(pool_x)
                feasible_mask = predicted <= 1e-6
                chosen_params: list[dict[str, Any]] = []
                if feasible_mask.any():
                    idx = np.flatnonzero(feasible_mask)
                    picks = select_candidates(mean[idx], std[idx], best=best,
                                              strategy="ei", count=remaining)
                    chosen_params = [pool_params[int(idx[pick.index])] for pick in picks]
                else:
                    # feasibility first: chase the smallest predicted violation,
                    # breaking ties by the predicted objective
                    order = np.lexsort((-mean, predicted))[:remaining]
                    chosen_params = [pool_params[int(i)] for i in order]
                chosen = chosen_params
            else:
                picks = select_candidates(mean, std, best=best, strategy="ei",
                                          count=remaining)
                chosen = [pool_params[pick.index] for pick in picks]
        else:
            order = rng.permutation(len(pool_params))
            chosen = [pool_params[i] for i in order[:remaining]]
        for params in chosen:
            if evaluated >= budget:
                break
            measure(params)
        history.append({"stage": "loop", "evaluated": evaluated,
                        "feasible": len(archive.feasible()),
                        "model_rmse": None if report is None else round(report.rmse, 4),
                        "model_usable": False if report is None else model_is_usable(
                            report, float(np.ptp(y)))})
    front = archive.feasible_front()
    best_entry = archive.best_by(target.kind, maximize=target.direction == "maximize")
    volume = None
    if reference is not None and len(case.objectives) >= 2:
        volume = hypervolume_2d(archive.feasible(), case.objectives[0], case.objectives[1],
                               reference)
    return StrategyResult(
        name="surrogate",
        evaluations=evaluated,
        feasible=len(archive.feasible()),
        front_size=len(front),
        best_by_objective={target.kind: None if best_entry is None else best_entry.metrics.get(target.kind)},
        hypervolume=volume,
        first_feasible_at=first_feasible,
        history=history,
    )


def run_ablation(
    case: CaseSpec,
    executor_factory: Any,
    *,
    budget: int,
    seeds: Sequence[int] = (0, 1, 2),
    reference: tuple[float, float] | None = None,
    strategies: Sequence[str] = SEARCH_STRATEGIES,
) -> dict[str, Any]:
    """Aggregate strategy results over seeds (mean / worst / best)."""
    rows: dict[str, list[StrategyResult]] = {name: [] for name in strategies}
    surface_only = True
    for seed in seeds:
        for name in strategies:
            executor = executor_factory()
            surface_only = surface_only and isinstance(executor, MeasuredSurfaceExecutor)
            if name == "surrogate":
                result = run_surrogate_assisted(case, executor, budget=budget,
                                                seed=seed, reference=reference)
            else:
                result = run_strategy(case, executor, name, budget=budget, seed=seed,
                                      reference=reference)
            rows[name].append(result)
    summary: dict[str, Any] = {}
    for name, results in rows.items():
        feasible = [r.feasible for r in results]
        evaluations = [r.evaluations for r in results]
        volumes = [r.hypervolume for r in results if r.hypervolume is not None]
        firsts = [r.first_feasible_at for r in results if r.first_feasible_at is not None]
        summary[name] = {
            "runs": len(results),
            "first_feasible_mean": (sum(firsts) / len(firsts)) if firsts else None,
            "first_feasible_never": len(results) - len(firsts),
            "feasible_mean": round(float(np.mean(feasible)), 3),
            "feasible_best": int(max(feasible)),
            "evaluations_mean": round(float(np.mean(evaluations)), 2),
            "hypervolume_mean": None if not volumes else round(float(np.mean(volumes)), 4),
        }
    report = {"case": case.case_id, "budget": budget, "seeds": list(seeds), "summary": summary}
    if summary and surface_only:
        report["evaluation_source"] = "fitted_surface_predictions"
    return report
