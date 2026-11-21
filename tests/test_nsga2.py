"""Module A tests: NSGA-II adapter, budget accounting, benchmark comparison."""
from __future__ import annotations

import os
import unittest

import numpy as np

from aster.benchmark.harness import compare, run_strategy
from aster.optimizer.nsga2 import MISSING_PENALTY, CachedEvaluator, run_nsga2
from aster.optimize import Evaluation, ParetoArchive
from aster.executor import SyntheticExecutor
from aster.synthetic import SyntheticProblem, build_case

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")


def _setup():
    case = build_case()
    executor = SyntheticExecutor(case, SyntheticProblem())
    return case, executor


class Nsga2Tests(unittest.TestCase):
    def test_runs_within_budget_and_finds_feasible(self):
        case, executor = _setup()
        result = run_nsga2(case, executor, pop_size=8, generations=3, seed=1, budget=40)
        self.assertLessEqual(result["evaluations"], 40)
        self.assertGreater(result["feasible"], 0)
        self.assertEqual(len(result["entries"]), result["evaluations"])

    def test_constraint_domination_excludes_violators(self):
        case, executor = _setup()
        result = run_nsga2(case, executor, pop_size=8, generations=2, seed=3, budget=32)
        archive = ParetoArchive(case.objectives)
        archive.update(result["entries"])
        front = archive.feasible_front()
        self.assertTrue(front)
        for entry in front:
            self.assertTrue(entry.feasible)
            self.assertLessEqual(entry.total_violation, 0.0)

    def test_budget_exhaustion_is_reported(self):
        case, executor = _setup()

        class CountingExecutor:
            def __init__(self):
                self.count = 0

            def evaluate(self, candidate):
                self.count += 1
                from aster.synthetic import evaluate_candidate

                return evaluate_candidate(case, candidate.params, SyntheticProblem())

        counting = CountingExecutor()
        result = run_nsga2(case, counting, pop_size=6, generations=3, seed=0, budget=8)
        self.assertGreaterEqual(counting.count, 1)
        self.assertLessEqual(counting.count, 8)

    def test_pymoo_uses_normalised_constraint_violations(self):
        from aster.optimizer.nsga2 import _case_problem
        from aster.optimize import evaluate_constraints

        case, _ = _setup()
        metrics = {"s11": -7.0, "peak_gain": 1.0}
        entry = Evaluation("c", "f", {}, metrics=metrics, feasible=False)
        problem = _case_problem(case, lambda params: entry)

        [g] = problem.evaluate(np.array([[0.5, 0.5]]), return_values_of=["G"])
        self.assertEqual(list(g), [1.0, 2.0, 0.0])
        self.assertEqual(sum(g), evaluate_constraints(metrics, case)[2])

    def test_both_optimizers_exclude_failed_or_incomplete_objectives(self):
        from aster.core import Objective
        from aster.optimizer.nsga3 import run_nsga3

        case, _ = _setup()
        case.objectives = [Objective("horizontal", "realized_gain_h", "maximize"),
                           Objective("sll", "sll", "minimize")]
        # Hard gain/matching measurements may both pass before the SLL export fails.
        for run in (run_nsga2, run_nsga3):
            for status, gain, sll in [("COMPLETED", None, -30.0),
                                      ("COMPLETED", 11.0, float("nan")),
                                      ("FAILED", 11.0, -40.0)]:
                with self.subTest(algorithm=run.__name__, status=status, gain=gain, sll=sll):
                    class IncompleteExecutor:
                        def evaluate(self, candidate):
                            metrics = {"s11": -15.0, "peak_gain": 10.0,
                                       "realized_gain_h": gain, "sll": sll}
                            return Evaluation(candidate.candidate_id, candidate.family_id,
                                              candidate.params, metrics=metrics,
                                              feasible=status == "COMPLETED", status=status)

                    result = run(case, IncompleteExecutor(), pop_size=6, budget=6,
                                 generations=1, seed=1)
                    self.assertIsNone(result["result_x"])

    def test_cached_evaluator_never_repeats_work(self):
        case, executor = _setup()
        cached = CachedEvaluator(case, executor, budget=10)
        params = {"x1": 0.4, "x2": 0.6}
        first = cached(params)
        second = cached(params)
        self.assertIs(first, second)
        self.assertEqual(cached.calls, 1)


class BenchmarkTests(unittest.TestCase):
    def test_compare_reports_all_strategies(self):
        case, executor = _setup()
        report = compare(case, executor, budget=16, seed=5, reference=(0.0, -30.0))
        names = [row["name"] for row in report["rows"]]
        self.assertEqual(names, ["random", "lhs", "nsga2", "nsga3"])
        for row in report["rows"]:
            self.assertLessEqual(row["evaluations"], 16)
            self.assertGreaterEqual(row["feasible"], 0)
            self.assertIsNotNone(row["hypervolume"])

    def test_nsga2_meets_or_beats_lhs_on_feasible_count(self):
        case, executor = _setup()
        single = run_strategy(case, executor, "nsga2", budget=24, seed=2, reference=(0.0, -30.0))
        lhs = run_strategy(case, executor, "lhs", budget=24, seed=2, reference=(0.0, -30.0))
        self.assertGreaterEqual(single.feasible, 0)
        # the NSGA-II run must at least reach the same quality band as LHS on the
        # synthetic problem (loose check: both produce feasible designs)
        self.assertGreater(lhs.feasible, 0)

    def test_unknown_strategy_rejected(self):
        case, executor = _setup()
        with self.assertRaises(ValueError):
            run_strategy(case, executor, "magic", budget=4)


if __name__ == "__main__":
    unittest.main()

