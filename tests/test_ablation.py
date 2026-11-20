"""Ablation tests: surrogate-assisted loop and multi-seed aggregation."""
from __future__ import annotations

import os
import unittest

from aster.benchmark.ablation import run_ablation, run_surrogate_assisted
from aster.benchmark.harness import run_strategy
from aster.executor import SyntheticExecutor
from aster.synthetic import SyntheticProblem, build_case

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")


class SurrogateAssistedTests(unittest.TestCase):
    def setUp(self):
        self.case = build_case()
        self.problem = SyntheticProblem()

    def test_loop_respects_budget_and_finds_feasible(self):
        executor = SyntheticExecutor(self.case, self.problem)
        result = run_surrogate_assisted(self.case, executor, budget=16, initial=6,
                                        batch=3, seed=1, reference=(0.0, -30.0))
        self.assertLessEqual(result.evaluations, 16)
        self.assertGreater(result.feasible, 0)
        self.assertIsNotNone(result.best_by_objective["peak_gain"])
        stages = [entry["stage"] for entry in result.history]
        self.assertEqual(stages[0], "seed")
        self.assertIn("loop", stages)

    def test_loop_records_model_quality(self):
        executor = SyntheticExecutor(self.case, self.problem)
        result = run_surrogate_assisted(self.case, executor, budget=20, initial=8,
                                        batch=4, seed=2)
        loop_entries = [entry for entry in result.history if entry["stage"] == "loop"]
        self.assertTrue(loop_entries)
        self.assertIn("model_usable", loop_entries[-1])

    def test_tiny_budget_uses_only_seeding(self):
        executor = SyntheticExecutor(self.case, self.problem)
        result = run_surrogate_assisted(self.case, executor, budget=4, initial=8, seed=0)
        self.assertLessEqual(result.evaluations, 4)

    def test_ablation_aggregates_seeds(self):
        report = run_ablation(
            self.case,
            lambda: SyntheticExecutor(self.case, self.problem),
            budget=12,
            seeds=(0, 1),
            reference=(0.0, -30.0),
        )
        self.assertEqual(set(report["summary"]), {"random", "lhs", "nsga2", "nsga3", "surrogate"})
        for name, row in report["summary"].items():
            self.assertEqual(row["runs"], 2)
            self.assertGreaterEqual(row["feasible_mean"], 0.0)
            self.assertGreaterEqual(row["evaluations_mean"], 1.0)
            self.assertLessEqual(row["evaluations_mean"], 12)

    def test_ablation_can_run_subset(self):
        report = run_ablation(
            self.case,
            lambda: SyntheticExecutor(self.case, self.problem),
            budget=8,
            seeds=(0,),
            strategies=("random", "surrogate"),
        )
        self.assertEqual(set(report["summary"]), {"random", "surrogate"})


if __name__ == "__main__":
    unittest.main()


class SurrogateLoopHygieneTests(unittest.TestCase):
    """Regression guards for the feasibility-aware, deduplicated loop."""

    def setUp(self):
        self.case = build_case()
        self.problem = SyntheticProblem()

    def _run(self, budget: int, seed: int = 0):
        executor = SyntheticExecutor(self.case, self.problem)
        return run_surrogate_assisted(self.case, executor, budget=budget, initial=8,
                                      batch=4, seed=seed, reference=(0.0, -30.0))

    def test_loop_keeps_improving_instead_of_re_measuring(self):
        result = self._run(40, seed=0)
        rmse_values = {entry.get("model_rmse") for entry in result.history
                       if entry.get("model_rmse") is not None}
        # a frozen model means the dataset stopped growing (the bug this guards)
        self.assertGreater(len(rmse_values), 2)

    def test_feasibility_grows_beyond_the_lhs_seed(self):
        result = self._run(40, seed=0)
        seed_feasible = result.history[0]["feasible"]
        self.assertGreaterEqual(result.feasible, seed_feasible + 5)

    def test_loop_does_not_lose_to_random_on_feasible_count(self):
        result = self._run(40, seed=0)
        random_case = build_case()
        executor = SyntheticExecutor(random_case, self.problem)
        from aster.benchmark.harness import run_strategy
        baseline = run_strategy(random_case, executor, "random", budget=40, seed=0,
                                reference=(0.0, -30.0))
        self.assertGreaterEqual(result.feasible, baseline.feasible)

class FirstFeasibleMetricTests(unittest.TestCase):
    def test_strategies_report_when_the_first_feasible_appeared(self):
        case = build_case()
        problem = SyntheticProblem()
        for strategy in ("random", "lhs", "nsga2"):
            executor = SyntheticExecutor(case, problem)
            result = run_strategy(case, executor, strategy, budget=16, seed=0)
            self.assertIn("first_feasible_at", result.to_dict())
            if result.feasible:
                self.assertIsNotNone(result.first_feasible_at)
                self.assertGreaterEqual(result.first_feasible_at, 1)
                self.assertLessEqual(result.first_feasible_at, result.evaluations)

    def test_ablation_summary_reports_first_feasible_mean(self):
        case = build_case()
        problem = SyntheticProblem()
        report = run_ablation(case, lambda: SyntheticExecutor(case, problem),
                             budget=12, seeds=(0, 1), reference=(0.0, -30.0))
        for row in report["summary"].values():
            self.assertIn("first_feasible_mean", row)
            self.assertIn("first_feasible_never", row)
