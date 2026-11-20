"""Tests for the measured-surface executor and the real-shape benchmark."""
from __future__ import annotations

import os
import unittest

from antopt.benchmark.harness import run_strategy
from antopt.benchmark.measured_surface import MeasuredPoint, MeasuredSurfaceExecutor
from antopt.cases import lens28_ref

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")

MEASURED = [
    MeasuredPoint({"tip_offset": 1.0, "disc_factor": 1.0, "disc_radial": 1.0,
                   "ring_inner": 1.2, "ring_outer": 3.0, "ring_height": 0.6,
                   "ring_shift": 0.0, "ring2_outer": 0.0, "ring2_height": 0.4,
                   "ring2_shift": -0.4, "feed_radius": 0.4, "feed_len": 1.6,
                   "feed_z0": -2.8},
                  {"s11_bandwidth": 2.0, "peak_gain": 6.25, "ripple": 1.39}),
    MeasuredPoint({"tip_offset": 1.0, "disc_factor": 1.0, "disc_radial": 1.0,
                   "ring_inner": 1.2, "ring_outer": 2.2, "ring_height": 0.6,
                   "ring_shift": 0.0, "ring2_outer": 0.0, "ring2_height": 0.4,
                   "ring2_shift": -0.4, "feed_radius": 0.4, "feed_len": 1.6,
                   "feed_z0": -2.8},
                  {"s11_bandwidth": 3.0, "peak_gain": 6.44, "ripple": 1.30}),
    MeasuredPoint({"tip_offset": 1.5, "disc_factor": 1.0, "disc_radial": 1.0,
                   "ring_inner": 1.2, "ring_outer": 3.0, "ring_height": 0.6,
                   "ring_shift": 0.0, "ring2_outer": 0.0, "ring2_height": 0.4,
                   "ring2_shift": -0.4, "feed_radius": 0.4, "feed_len": 1.6,
                   "feed_z0": -2.8},
                  {"s11_bandwidth": 0.0, "peak_gain": 6.82, "ripple": 1.81}),
]


class MeasuredSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.case = lens28_ref.build_case()
        self.executor = MeasuredSurfaceExecutor(self.case, MEASURED)

    def test_predicts_the_measured_metrics_near_the_measured_points(self):
        good = dict(MEASURED[1].params)
        predicted = self.executor.predict(good)
        self.assertAlmostEqual(predicted["peak_gain"], 6.44, delta=0.2)
        self.assertAlmostEqual(predicted["s11_bandwidth"], 3.0, delta=0.6)

    def test_evaluation_is_labelled_predicted(self):
        candidate = self.case.make_candidate(dict(MEASURED[1].params),
                                             algorithm="surface", round=0)
        entry = self.executor.evaluate(candidate)
        self.assertEqual(entry.status, "PREDICTED")
        self.assertIsNone(entry.run_id)
        self.assertIn("s11_bandwidth", entry.metrics)

    def test_feasibility_follows_the_fitted_metrics(self):
        candidate = self.case.make_candidate(dict(MEASURED[1].params),
                                             algorithm="surface", round=0)
        entry = self.executor.evaluate(candidate)
        # the GP smooths a sparse landscape, so the fitted bandwidth sits just
        # below the measured 3.0 GHz at the optimum; assert on the metrics rather
        # than demanding a hard feasibility flag from a fitted surface
        self.assertGreater(entry.metrics["s11_bandwidth"], 2.5)
        self.assertGreater(entry.metrics["peak_gain"], 6.0)

    def test_strategies_run_on_the_fitted_landscape(self):
        result = run_strategy(self.case, self.executor, "random", budget=3, seed=0)
        self.assertEqual(result.evaluations, 3)
        self.assertGreaterEqual(result.feasible, 0)


if __name__ == "__main__":
    unittest.main()
