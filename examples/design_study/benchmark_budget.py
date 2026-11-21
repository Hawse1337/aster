"""Compare four ASTER strategies on its analytic synthetic antenna problem.

Example: python benchmark_budget.py --source-tree ./aster-source --output benchmark-budget.json
Install the optimizer and surrogate dependencies of the selected source tree first.
"""
from __future__ import annotations

import argparse
import importlib
import json
import math
import statistics
import sys
from importlib.metadata import version
from pathlib import Path


STRATEGIES = ("random", "lhs", "nsga2", "surrogate")
# The harness takes its reference in maximization coordinates (gain, -S11).
REFERENCE = (4.0, 9.0)


class CountedProblem:
    def __init__(self, problem, budget):
        self.problem = problem
        self.budget = budget
        self.calls = 0

    def metrics(self, params):
        if self.calls >= self.budget:
            raise RuntimeError("strategy exceeded its analytic function-call budget")
        self.calls += 1
        return self.problem.metrics(params)


class RecordingExecutor:
    def __init__(self, executor):
        self.executor = executor
        self.entries = []

    def evaluate(self, candidate):
        result = self.executor.evaluate(candidate)
        self.entries.append(result)
        return result


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def verify(result, executor, oracle, case, optimize):
    """Independently check direction, domination, area, thresholds and call order."""
    entries = executor.entries
    unique = {entry.candidate_id: entry for entry in entries}
    for entry in entries:
        valid = entry.metrics["s11"] <= -10 and entry.metrics["peak_gain"] >= 5
        require(valid == entry.feasible, "hard-constraint direction mismatch")
    first = next((i for i, e in enumerate(entries, start=1) if e.feasible), None)
    require(first == result.first_feasible_at, "first feasible call mismatch")
    require(oracle.calls == len(entries) == result.evaluations, "function-call mismatch")
    feasible = {cid: (e.metrics["peak_gain"], e.metrics["s11"])
                for cid, e in unique.items() if e.feasible}
    require(len(feasible) == result.feasible, "feasible archive count mismatch")
    front = {cid for cid, (g, s) in feasible.items()
             if not any(h >= g and t <= s and (h > g or t < s)
                        for h, t in feasible.values())}
    archive = optimize.ParetoArchive(case.objectives)
    archive.update(entries)
    require({e.candidate_id for e in archive.feasible_front()} == front
            and result.front_size == len(front), "non-dominated front mismatch")

    # Independently integrate union height over disjoint vertical gain strips.
    previous = REFERENCE[0]
    area = 0.0
    for gain in sorted({g for g, _ in feasible.values() if g > previous}):
        height = max(-s for g, s in feasible.values() if g >= gain) - REFERENCE[1]
        area += (gain - previous) * max(0.0, height)
        previous = gain
    require(math.isclose(area, result.hypervolume, rel_tol=1e-10, abs_tol=1e-10),
            "hypervolume or objective direction mismatch")
    return {"actual_function_calls": oracle.calls, "unused_budget": oracle.budget - oracle.calls,
            "unique_candidates": len(unique), "feasible_candidates": len(feasible),
            "first_feasible_call": first, "feasible_front_size": len(front),
            "hypervolume": result.hypervolume}


def summarize(rows):
    summary = {}
    for strategy in STRATEGIES:
        group = [row for row in rows if row["strategy"] == strategy]
        firsts = [row["first_feasible_call"] for row in group if row["first_feasible_call"]]
        summary[strategy] = {
            "mean_feasible_candidates": statistics.mean(r["feasible_candidates"] for r in group),
            "mean_hypervolume": statistics.mean(r["hypervolume"] for r in group),
            "mean_first_feasible_among_successes": statistics.mean(firsts) if firsts else None,
            "seeds_without_feasible": len(group) - len(firsts),
            "total_analytic_calls": sum(r["actual_function_calls"] for r in group),
        }
    return summary


def benchmark(package, budget, seeds):
    from threadpoolctl import threadpool_limits

    def module(name):
        return importlib.import_module(f"{package}.{name}")

    synthetic = module("synthetic")
    executor_class = module("executor").SyntheticExecutor
    optimize = module("optimize")
    run_strategy = module("benchmark.harness").run_strategy
    run_surrogate = module("benchmark.ablation").run_surrogate_assisted
    rows = []
    traces = []
    with threadpool_limits(limits=1):
        for seed in seeds:
            for name in STRATEGIES:
                problem = synthetic.SyntheticProblem()
                case = synthetic.build_case(problem)
                oracle = CountedProblem(problem, budget)
                executor = RecordingExecutor(executor_class(case, oracle))
                knobs = dict(budget=budget, batch=4, seed=seed, reference=REFERENCE)
                if name == "surrogate":
                    result = run_surrogate(case, executor, initial=8, pool=128, **knobs)
                else:
                    result = run_strategy(case, executor, name, **knobs)
                rows.append({"strategy": name, "seed": seed,
                             **verify(result, executor, oracle, case, optimize)})
                traces.append((name, seed, executor.entries, case.objectives))

    # Cached NSGA candidates can consume generations without new function calls.
    # Report a second comparison at the same realized call count for all runs.
    common_calls = min(len(entries) for _, _, entries, _ in traces)
    prefix_rows = []
    for name, seed, entries, objectives in traces:
        prefix = entries[:common_calls]
        archive = optimize.ParetoArchive(objectives)
        archive.update(prefix)
        prefix_rows.append({
            "strategy": name, "seed": seed, "actual_function_calls": common_calls,
            "feasible_candidates": len(archive.feasible()),
            "first_feasible_call": next((i for i, e in enumerate(prefix, 1) if e.feasible), None),
            "feasible_front_size": len(archive.feasible_front()),
            "hypervolume": optimize.hypervolume_2d(archive.feasible(), *objectives, REFERENCE),
        })
    return {"evidence": "synthetic_analytic_function_evaluations", "case": "synthetic",
            "hfss_solver_calls": 0, "package": package, "budget_per_seed_per_strategy": budget,
            "seeds": seeds, "batch": 4, "surrogate_initial": 8, "surrogate_pool": 128,
            "objectives": {"peak_gain": "maximize", "s11": "minimize"},
            "thresholds": {"minimum_gain_dbi": 5.0, "maximum_s11_db": -10.0},
            "hypervolume_reference_raw": {"gain_dbi": REFERENCE[0], "s11_db": -REFERENCE[1]},
            "hypervolume_units": "unscaled dBi * dB",
            "checks": ["hard thresholds", "counted oracle calls", "first feasible call",
                       "unique feasible count", "mixed-direction Pareto front",
                       "hypervolume against independent strip integration"],
            "versions": {p: version(p) for p in ("numpy", "scipy", "scikit-learn", "pymoo")},
            "rows": rows, "summary": summarize(rows),
            "equal_actual_calls": {"common_prefix_calls": common_calls, "rows": prefix_rows,
                                   "summary": summarize(prefix_rows)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-tree", type=Path, help="directory containing the patched package")
    parser.add_argument("--package", choices=("aster", "antopt"), default="aster")
    parser.add_argument("--budget", type=int, default=24)
    parser.add_argument("--seeds", nargs="+", type=int, default=[0, 1, 2])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.budget < 1 or len(args.seeds) != len(set(args.seeds)) or min(args.seeds) < 0:
        parser.error("budget must be positive and seeds must be distinct nonnegative integers")
    if args.source_tree:
        if not (args.source_tree / args.package / "synthetic.py").is_file():
            parser.error("--source-tree must contain the chosen package")
        sys.path.insert(0, str(args.source_tree.resolve()))
    report = benchmark(args.package, args.budget, args.seeds)
    payload = json.dumps(report, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()
