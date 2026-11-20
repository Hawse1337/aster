"""Module A tests: the NSGA-III adapter must match NSGA-II's contract."""
from __future__ import annotations

import unittest

import numpy as np

from aster.cases import lens28
from aster.optimizer.nsga3 import reference_directions, run_nsga3


class Recorder:
    """Deterministic stand-in for the guard: a cheap analytic antenna model."""

    def __init__(self):
        self.calls = 0
        self.seen = []

    def evaluate(self, candidate):
        from aster.optimize import Evaluation

        self.calls += 1
        self.seen.append(dict(candidate.params))
        bandwidth = 4.0 - 2.0 * abs(candidate.params.get("ring_outer", 2.2) - 2.2)
        gain = 6.0 + 0.4 * (candidate.params.get("tip_offset", 1.0) - 1.0)
        metrics = {"s11_bandwidth": max(0.0, bandwidth), "peak_gain": gain,
                   "ripple": 1.2}
        feasible = metrics["s11_bandwidth"] >= 3.0 and metrics["peak_gain"] >= 6.0
        violation = 0.0
        violation += max(0.0, 3.0 - metrics["s11_bandwidth"])
        violation += max(0.0, 6.0 - metrics["peak_gain"])
        return Evaluation(candidate_id=candidate.candidate_id,
                          family_id=candidate.family_id, params=dict(candidate.params),
                          metrics=metrics, feasible=feasible, total_violation=violation)


class ReferenceDirectionTests(unittest.TestCase):
    def test_two_objectives_hit_the_population_size(self):
        directions = reference_directions(2, 8)
        self.assertEqual(directions.shape[0], 8)
        self.assertTrue(np.allclose(directions.sum(axis=1), 1.0))

    def test_three_objectives_are_supported(self):
        directions = reference_directions(3, 8)
        self.assertEqual(directions.shape[1], 3)
        self.assertGreaterEqual(directions.shape[0], 8)

    def test_invalid_arguments_are_rejected(self):
        with self.assertRaises(ValueError):
            reference_directions(0, 8)
        with self.assertRaises(ValueError):
            reference_directions(2, 0)


class RunNsga3Tests(unittest.TestCase):
    def setUp(self):
        self.case = lens28.build_case()
        self.executor = Recorder()

    def test_returns_entries_and_respects_the_budget(self):
        result = run_nsga3(self.case, self.executor, pop_size=8, generations=3,
                           seed=3, budget=20)
        self.assertLessEqual(result["evaluations"], 20)
        self.assertTrue(result["entries"])
        self.assertEqual(result["case"], self.case.case_id)
        self.assertIn("reference_directions", result["history"][0])

    def test_sampling_is_reproducible_but_niching_ties_are_not(self):
        # pymoo seeds the initial population from the seed, so the first evaluated candidate
        # repeats; NSGA-III's niching breaks exact ties randomly, so the evaluation count can
        # differ by a couple between runs - recorded here rather than pretended away
        first = run_nsga3(self.case, Recorder(), pop_size=8, generations=2, seed=7)
        second = run_nsga3(self.case, Recorder(), pop_size=8, generations=2, seed=7)
        self.assertEqual(first["entries"][0].params, second["entries"][0].params)
        self.assertLessEqual(abs(first["evaluations"] - second["evaluations"]), 4)

    def test_feasible_entries_win_over_infeasible_ones(self):
        result = run_nsga3(self.case, self.executor, pop_size=8, generations=4, seed=1)
        feasible = [entry for entry in result["entries"] if entry.feasible]
        if feasible:
            best = max(feasible, key=lambda entry: entry.metrics["s11_bandwidth"])
            for entry in result["entries"]:
                if not entry.feasible:
                    self.assertGreaterEqual(best.metrics["s11_bandwidth"],
                                            entry.metrics["s11_bandwidth"] - 1e-9)

    def test_cache_prevents_duplicate_evaluations(self):
        result = run_nsga3(self.case, self.executor, pop_size=8, generations=3, seed=2)
        self.assertEqual(self.executor.calls, result["evaluations"])


if __name__ == "__main__":
    unittest.main()
