"""Normalised hypervolume progress: hand-checked areas, monotone curves, no invented data."""
from __future__ import annotations

import unittest

import numpy as np

from aster.metrics import (Normalisation, hypervolume_2d, hypervolume_progress,
                            normalisation_from_runs)


def run(run_id: str, bandwidth: float, gain: float, status: str = "COMPLETED",
        at: str = "") -> dict:
    return {"run_id": run_id, "status": status, "started_utc": at,
            "metrics": {"s11_bandwidth": bandwidth, "peak_gain": gain}}


class HypervolumeTests(unittest.TestCase):
    def test_one_point_covers_its_box(self):
        self.assertAlmostEqual(hypervolume_2d(np.array([[0.5, 0.5]])), 0.25)

    def test_a_two_point_front_is_summed(self):
        # (0, 0.5) covers 0.5 and (0.5, 0) adds 0.25 against the (1, 1) reference
        self.assertAlmostEqual(hypervolume_2d(np.array([[0.0, 0.5], [0.5, 0.0]])), 0.75)

    def test_a_dominated_point_adds_nothing(self):
        with_dominated = hypervolume_2d(np.array([[0.0, 0.0], [0.5, 0.5]]))
        clean = hypervolume_2d(np.array([[0.0, 0.0]]))
        self.assertAlmostEqual(with_dominated, clean)

    def test_points_on_the_reference_contribute_nothing(self):
        self.assertEqual(hypervolume_2d(np.array([[1.0, 0.0], [0.0, 1.0]])), 0.0)

    def test_points_outside_the_box_are_ignored(self):
        self.assertAlmostEqual(hypervolume_2d(np.array([[0.5, 0.5], [2.0, 2.0]])), 0.25)

    def test_an_empty_set_is_zero(self):
        self.assertEqual(hypervolume_2d(np.zeros((0, 2))), 0.0)


class NormalisationTests(unittest.TestCase):
    def test_measured_extremes_become_zero_and_one(self):
        runs = [run("a", 2.0, 6.0), run("b", 3.0, 6.2)]
        normalisation = normalisation_from_runs(runs)
        scaled = normalisation.scale(np.array([[-3.0, -6.2], [-2.0, -6.0]]))
        self.assertAlmostEqual(float(scaled[0, 0]), 0.0)
        self.assertAlmostEqual(float(scaled[0, 1]), 0.0)
        self.assertAlmostEqual(float(scaled[1, 0]), 1.0)
        self.assertAlmostEqual(float(scaled[1, 1]), 1.0)

    def test_values_are_clipped_to_a_bounded_window(self):
        # worse than the worst is clipped at 1; better than the ideal is kept but bounded at -1
        normalisation = Normalisation(ideal=(-3.0, -6.2), worst=(-2.0, -6.0))
        scaled = normalisation.scale(np.array([[-10.0, 0.0]]))
        self.assertAlmostEqual(float(scaled[0, 0]), -1.0)
        self.assertAlmostEqual(float(scaled[0, 1]), 1.0)

    def test_no_measurements_is_not_a_crash(self):
        normalisation = normalisation_from_runs([])
        self.assertEqual(len(normalisation.ideal), 2)
        self.assertEqual(len(normalisation.worst), 2)


class ProgressTests(unittest.TestCase):
    def test_the_curve_only_grows(self):
        runs = [run("a", 2.0, 6.0, at="1"), run("b", 3.0, 6.2, at="2"),
                run("c", 2.5, 6.1, at="3")]
        progress = hypervolume_progress(runs)
        curve = progress["cumulative"]
        self.assertEqual(len(curve), 3)
        for earlier, later in zip(curve, curve[1:]):
            self.assertGreaterEqual(later, earlier)

    def test_failed_runs_are_not_counted(self):
        runs = [run("a", 2.0, 6.0, at="1"),
                run("bad", 0.0, 0.0, status="FAILED", at="2"),
                run("b", 3.0, 6.2, at="3")]
        progress = hypervolume_progress(runs)
        self.assertEqual([point["run_id"] for point in progress["points"]], ["a", "b"])

    def test_the_first_point_has_no_hypervolume_of_its_own_beyond_itself(self):
        progress = hypervolume_progress([run("only", 3.0, 6.2, at="1")])
        # a single measurement is both ideal and worst, so it normalises to (0, 0) and covers the box
        self.assertAlmostEqual(progress["cumulative"][0], 1.0)
        self.assertAlmostEqual(progress["best"], 1.0)

    def test_an_empty_campaign_returns_an_empty_curve(self):
        progress = hypervolume_progress([])
        self.assertEqual(progress["cumulative"], [])
        self.assertEqual(progress["best"], 0.0)

    def test_an_explicit_normalisation_is_respected(self):
        runs = [run("a", 2.0, 6.0, at="1"), run("b", 3.0, 6.2, at="2")]
        normalisation = Normalisation(ideal=(-4.0, -7.0), worst=(-1.0, -5.0))
        progress = hypervolume_progress(runs, normalisation=normalisation)
        self.assertEqual(progress["normalisation"]["ideal"], [-4.0, -7.0])
        # neither point reaches the ideal, so only part of the box is covered
        self.assertGreater(progress["best"], 0.0)
        self.assertLess(progress["best"], 1.0)


if __name__ == "__main__":
    unittest.main()
