"""Experiment driver tests: the controller runs a real loop (offline)."""
from __future__ import annotations

import os
import unittest

from antopt.experiment import run_experiment
from antopt.executor import SyntheticExecutor
from antopt.synthetic import SyntheticProblem, build_case

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")

STRATEGIES = {"LOCAL_OPTIMIZATION", "GLOBAL_SEARCH", "SURROGATE_OPTIMIZATION",
              "PHYSICS_DIAGNOSIS", "TOPOLOGY_SEARCH", "STOP"}


class ExperimentDriverTests(unittest.TestCase):
    def setUp(self):
        self.case = build_case()
        self.executor = SyntheticExecutor(self.case, SyntheticProblem())

    def test_budget_is_respected_and_progress_is_made(self):
        result = run_experiment(self.case, self.executor, budget=16, seed=3,
                                reference=(0.0, -30.0))
        self.assertLessEqual(result["evaluations"], 16)
        self.assertGreaterEqual(result["evaluations"], 4)
        self.assertTrue(result["steps"])
        for step in result["steps"]:
            self.assertIn(step["strategy"], STRATEGIES)
            self.assertTrue(step["reasons"])

    def test_finds_feasible_and_tracks_best(self):
        result = run_experiment(self.case, self.executor, budget=20, seed=5,
                                reference=(0.0, -30.0))
        self.assertGreater(result["feasible"], 0)
        self.assertIsNotNone(result["best"])
        self.assertTrue(result["best"]["feasible"])
        self.assertGreater(result["best"]["metrics"]["realized_gain_h"], 0.0)

    def test_never_improves_by_violating_a_constraint(self):
        result = run_experiment(self.case, self.executor, budget=24, seed=7,
                                reference=(0.0, -30.0))
        archive = result["archive"]
        for entry in archive.feasible_front():
            self.assertTrue(entry.feasible)
            self.assertEqual(entry.total_violation, 0.0)

    def test_decisions_are_logged_with_context(self):
        result = run_experiment(self.case, self.executor, budget=8, seed=1)
        self.assertEqual(len(result["decisions"]), len(result["steps"]))
        first = result["decisions"][0]
        self.assertIn("context", first)
        self.assertIn("decision", first)
        self.assertIn("strategy", first["decision"])

    def test_zero_budget_returns_immediately(self):
        result = run_experiment(self.case, self.executor, budget=0, seed=0)
        self.assertEqual(result["evaluations"], 0)
        self.assertEqual(result["steps"], [])


if __name__ == "__main__":
    unittest.main()
