"""Recommendations: only measured runs train the surrogate, and the answer says why."""
from __future__ import annotations

import unittest

import numpy as np

from aster.recommend import recommend_from_runs, tighten_bounds, training_set

NAMES = ("ring_outer", "ring_height", "tip_offset")
BOUNDS = [(1.2, 3.0), (0.3, 1.2), (0.9, 1.4)]


def run(run_id: str, ring: float, height: float, tip: float, bandwidth: float,
        gain: float, ripple: float = 1.6, status: str = "COMPLETED") -> dict:
    return {"run_id": run_id, "status": status,
            "params": {"ring_outer": ring, "ring_height": height, "tip_offset": tip},
            "metrics": {"s11_bandwidth": bandwidth, "peak_gain": gain, "ripple": ripple}}


def campaign(size: int = 8, ripple: float = 1.6) -> list[dict]:
    rows = []
    rng = np.random.RandomState(0)
    for index in range(size):
        ring = 1.4 + 1.4 * rng.rand()
        height = 0.4 + 0.7 * rng.rand()
        tip = 0.95 + 0.4 * rng.rand()
        rows.append(run("r%d" % index, round(ring, 3), round(0.4 + 0.6 * rng.rand(), 3), round(tip, 3),
                        bandwidth=round(1.5 + 1.4 * rng.rand(), 3),
                        gain=round(5.5 + 1.0 * rng.rand(), 3), ripple=ripple))
    return rows


class TrainingSetTests(unittest.TestCase):
    def test_only_completed_runs_are_used(self):
        rows = campaign(3) + [run("failed", 2.2, 0.6, 1.0, 3.0, 6.0, status="FAILED")]
        x, y, used = training_set(rows, NAMES)
        self.assertEqual(len(x), 3)
        self.assertNotIn("failed", [row["run_id"] for row in used])

    def test_objectives_are_negated_for_minimisation(self):
        rows = [run("a", 2.0, 0.6, 1.0, bandwidth=3.0, gain=6.0)]
        _, y, _ = training_set(rows, NAMES)
        self.assertEqual(list(y[0]), [-3.0, -6.0])

    def test_a_run_missing_an_objective_is_skipped_not_filled(self):
        rows = campaign(3)
        rows[0]["metrics"] = {"s11_bandwidth": 3.0}
        x, _, _ = training_set(rows, NAMES)
        self.assertEqual(len(x), 2)


class RecommendationTests(unittest.TestCase):
    def test_both_refined_edges_are_accepted_and_close_to_the_rule(self):
        def accepts(params):
            return 0.33 <= params["x"] <= 0.65

        [(low, high)] = tighten_bounds([(0.0, 1.0)], accepts, {"x": 0.5},
                                      ["x"], samples=5)
        self.assertTrue(accepts({"x": low}))
        self.assertTrue(accepts({"x": high}))
        self.assertAlmostEqual(low, 0.33, delta=0.001)
        self.assertAlmostEqual(high, 0.65, delta=0.001)

    def test_too_few_runs_are_reported_not_guessed(self):
        result = recommend_from_runs(campaign(2), count=2, names=NAMES, bounds=BOUNDS)
        self.assertEqual(result["recommendations"], [])
        self.assertEqual(result["method"], "none")
        self.assertIn("实测数据不足", result["reason"])

    def test_recommendations_stay_inside_the_bounds(self):
        result = recommend_from_runs(campaign(8), count=2, method="believer",
                                     names=NAMES, bounds=BOUNDS)
        self.assertEqual(len(result["recommendations"]), 2)
        for item in result["recommendations"]:
            params = item["params"]
            for name, (low, high) in zip(NAMES, BOUNDS):
                self.assertGreaterEqual(params[name], low - 1e-9)
                self.assertLessEqual(params[name], high + 1e-9)

    def test_the_answer_names_the_backend_and_the_evidence(self):
        result = recommend_from_runs(campaign(8), count=2, method="believer",
                                     names=NAMES, bounds=BOUNDS)
        self.assertEqual(result["method"], "diverse_ei")
        self.assertEqual(result["trained_on"], 8)
        self.assertEqual(len(result["source_runs"]), 8)
        self.assertIn("实测数据拟合", result["reason"])

    def test_neighbour_violation_does_not_certify_an_unmeasured_candidate(self):
        result = recommend_from_runs(campaign(8, ripple=2.4), count=1, method="believer",
                                     names=NAMES, bounds=BOUNDS)
        item = result["recommendations"][0]
        self.assertIsNone(item["feasible"])
        self.assertEqual(item["feasibility_status"], "unknown")
        self.assertEqual(item["violations"], {})

    def test_feasible_neighbour_does_not_mark_new_candidate_measured(self):
        result = recommend_from_runs(campaign(8, ripple=1.2), count=1, method="believer",
                                     names=NAMES, bounds=BOUNDS)
        self.assertIsNone(result["recommendations"][0]["feasible"])
        self.assertEqual(result["recommendations"][0]["feasibility_status"], "unknown")

    def test_every_candidate_points_at_the_run_it_was_judged_by(self):
        result = recommend_from_runs(campaign(8), count=2, method="believer",
                                     names=NAMES, bounds=BOUNDS)
        for item in result["recommendations"]:
            self.assertIn(item["nearest_run"], result["source_runs"])

    def test_duplicate_proposals_are_dropped(self):
        from unittest import mock

        import aster.recommend as module

        same = np.array([[2.2, 0.6, 1.0], [2.2, 0.6, 1.0]])

        class _Selection:
            points = same
            method = "fake"
            note = ""

        with mock.patch.object(module, "select_batch", return_value=_Selection()):
            result = module.recommend_from_runs(campaign(8), count=2, method="believer",
                                                names=NAMES, bounds=BOUNDS)
        self.assertEqual(len(result["recommendations"]), 1)
        self.assertEqual(result["dropped_duplicates"], 1)

    def test_a_boundary_proposal_says_so(self):
        from unittest import mock

        import aster.recommend as module

        edge = np.array([[2.0, 0.4, 1.0]])   # ring_outer above its 1.5-2.9 default here

        class _Selection:
            points = edge
            method = "fake"
            note = ""

        bounds = [(1.2, 2.0), (0.3, 1.2), (0.9, 1.4)]
        with mock.patch.object(module, "select_batch", return_value=_Selection()):
            result = module.recommend_from_runs(campaign(8), count=1, method="believer",
                                                names=NAMES, bounds=bounds)
        self.assertIn("界", result["recommendations"][0]["note"])

    def test_missing_names_are_rejected(self):
        with self.assertRaises(ValueError):
            recommend_from_runs(campaign(8), count=1, names=None, bounds=BOUNDS)


if __name__ == "__main__":
    unittest.main()
