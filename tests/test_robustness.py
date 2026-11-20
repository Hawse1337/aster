"""Robustness skeleton: perturbed designs inside the box, and honest worst-case scoring."""
from __future__ import annotations

import unittest

from antopt.cases.lens28_var import build_case
from antopt.robustness import ToleranceSpec, perturbed_designs, score_robustness


def _space():
    return build_case().family.param_space


class PerturbationTests(unittest.TestCase):
    def setUp(self):
        self.space = _space()
        self.nominal = self.space.default()

    def test_corners_are_two_per_parameter_doubled(self):
        spec = ToleranceSpec(parameters=("ring_inner", "ring_outer"), deltas=(0.05,))
        plan = perturbed_designs(self.space, self.nominal, spec)
        self.assertEqual(len(plan["variants"]), 4, "2^2 corners")
        for variant in plan["variants"]:
            self.assertTrue(self.space.validate(variant))

    def test_a_single_parameter_tolerance_is_two_points(self):
        spec = ToleranceSpec(parameters=("ring_inner",))
        plan = perturbed_designs(self.space, self.nominal, spec)
        self.assertEqual(len(plan["variants"]), 2)

    def test_one_at_a_time_reports_each_parameter_alone(self):
        # a nominal on the boundary clips one sign away, so pick the middle of the box
        nominal = dict(self.nominal)
        for item in self.space:
            if item.name in ("ring_inner", "ring_outer"):
                nominal[item.name] = (float(item.low) + float(item.high)) / 2.0
        spec = ToleranceSpec(parameters=("ring_inner", "ring_outer"), corners_only=False)
        plan = perturbed_designs(self.space, nominal, spec)
        self.assertEqual(len(plan["variants"]), 4)
        moved = [set(name for name in nominal if variant[name] != nominal[name])
                 for variant in plan["variants"]]
        self.assertEqual(sorted(len(names) for names in moved), [1, 1, 1, 1])

    def test_perturbations_are_clipped_to_the_box(self):
        space = _space()
        low = {item.name: item.low for item in space}
        nominal = dict(self.nominal)
        for name, floor in low.items():
            nominal[name] = floor
        spec = ToleranceSpec(parameters=tuple(low), deltas=(0.05,), corners_only=False)
        plan = perturbed_designs(space, nominal, spec)
        for variant in plan["variants"]:
            self.assertTrue(space.validate(variant), variant)

    def test_already_measured_variants_are_dropped(self):
        spec = ToleranceSpec(parameters=("ring_inner",))
        first = perturbed_designs(self.space, self.nominal, spec)
        from antopt.distributed import config_hash

        measured = [config_hash("", variant) for variant in first["variants"]]
        again = perturbed_designs(self.space, self.nominal, spec, measured=measured)
        self.assertEqual(again["variants"], [])

    def test_an_empty_parameter_list_is_refused(self):
        with self.assertRaises(ValueError):
            ToleranceSpec(parameters=())

    def test_a_negative_tolerance_is_refused(self):
        with self.assertRaises(ValueError):
            ToleranceSpec(parameters=("ring_inner",), deltas=(-0.1,))


class ScoringTests(unittest.TestCase):
    rows = [{"s11_bandwidth": 3.0, "peak_gain": 6.4},
            {"s11_bandwidth": 2.6, "peak_gain": 5.9},
            {"s11_bandwidth": 2.9, "peak_gain": 6.1}]

    def test_worst_case_is_the_minimum_not_the_mean(self):
        report = score_robustness(self.rows)
        self.assertEqual(report["worst"]["s11_bandwidth"], 2.6)
        self.assertEqual(report["worst"]["peak_gain"], 5.9)
        self.assertAlmostEqual(report["mean"]["peak_gain"], 6.1333, places=3)
        self.assertAlmostEqual(report["spread"]["s11_bandwidth"], 0.4, places=6)

    def test_yield_counts_neighbours_that_meet_every_constraint(self):
        report = score_robustness(self.rows,
                                  constraints={"peak_gain": (">=", 6.0)})
        self.assertAlmostEqual(report["yield"], 2 / 3, places=6)

    def test_missing_values_count_as_failures(self):
        rows = self.rows + [{"s11_bandwidth": 3.0}]
        report = score_robustness(rows, constraints={"peak_gain": (">=", 6.0)})
        self.assertAlmostEqual(report["yield"], 2 / 4, places=6)

    def test_no_measurements_is_an_honest_zero(self):
        report = score_robustness([])
        self.assertEqual(report["count"], 0)
        self.assertEqual(report["yield"], 0.0)
        self.assertEqual(report["yield_basis"], "no measurements")


if __name__ == "__main__":
    unittest.main()
