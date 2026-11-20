"""Cost estimates: a budget is a promise, so an unknown cost must not be silently assumed cheap."""
from __future__ import annotations

import unittest

import numpy as np

from antopt.cost import estimate, fit_cost_model, measured_costs

NAMES = ("ring_outer", "ring_height")


def run(index: int, ring: float, height: float, seconds: float, status: str = "COMPLETED") -> dict:
    return {"run_id": "r%d" % index, "status": status,
            "params": {"ring_outer": ring, "ring_height": height},
            "total_seconds": seconds}


class MeasuredCostTests(unittest.TestCase):
    def test_failures_count_towards_the_rate_but_not_the_fit(self):
        rows = [run(0, 2.0, 0.6, 1200.0), run(1, 2.2, 0.6, 1300.0),
                run(2, 2.4, 0.6, 1400.0), run(3, 2.6, 0.6, 1500.0),
                run(4, 3.0, 0.6, 0.0, status="FAILED")]
        model = fit_cost_model(rows, NAMES)
        self.assertAlmostEqual(model.failure_rate, 0.2, places=3)
        self.assertEqual(model.samples, 4)

    def test_runs_without_a_cost_are_skipped(self):
        rows = [run(0, 2.0, 0.6, 1200.0), {"run_id": "x", "status": "COMPLETED",
                                         "params": {"ring_outer": 2.2, "ring_height": 0.6}}]
        x, y, ids, _rate = measured_costs(rows, NAMES)
        self.assertEqual(len(y), 1)
        self.assertEqual(ids, ["r0"])
        self.assertEqual(x.shape, (1, 2))


class ModelTests(unittest.TestCase):
    def test_a_linear_trend_is_learned(self):
        # 10 minutes plus a minute per 0.1 mm of ring outer
        rows = [run(i, 2.0 + 0.1 * i, 0.6, 600.0 + 60.0 * i) for i in range(8)]
        model = fit_cost_model(rows, NAMES)
        self.assertTrue(model.fitted)
        predicted = model.predict({"ring_outer": 2.3, "ring_height": 0.6})
        self.assertEqual(predicted.method, "ridge")
        self.assertAlmostEqual(predicted.seconds, 780.0, delta=60.0)

    def test_too_few_points_fall_back_to_the_median(self):
        rows = [run(0, 2.0, 0.6, 1200.0), run(1, 2.4, 0.6, 1400.0)]
        model = fit_cost_model(rows, NAMES)
        self.assertFalse(model.fitted)
        predicted = model.predict({"ring_outer": 2.3, "ring_height": 0.6})
        self.assertEqual(predicted.method, "median")
        self.assertAlmostEqual(predicted.seconds, 1300.0)

    def test_no_data_at_all_is_zero_with_no_fake_confidence(self):
        predicted = estimate([], NAMES, {"ring_outer": 2.0, "ring_height": 0.6})
        self.assertEqual(predicted.seconds, 0.0)
        self.assertEqual(predicted.samples, 0)

    def test_the_estimate_is_never_negative(self):
        rows = [run(i, 2.0 + 0.1 * i, 0.6, 100.0) for i in range(6)]
        model = fit_cost_model(rows, NAMES)
        predicted = model.predict({"ring_outer": 5.0, "ring_height": 5.0})
        self.assertGreaterEqual(predicted.seconds, 1.0)

    def test_the_spread_reflects_a_noisy_trend(self):
        rows = [run(0, 2.0, 0.6, 1000.0), run(1, 2.1, 0.6, 1500.0),
                run(2, 2.2, 0.6, 900.0), run(3, 2.3, 0.6, 1600.0),
                run(4, 2.4, 0.6, 1100.0), run(5, 2.5, 0.6, 1450.0)]
        model = fit_cost_model(rows, NAMES)
        self.assertGreater(model.spread, 50.0)


if __name__ == "__main__":
    unittest.main()
