"""Stored measurements predate a dimension: they must stay usable, without inventing metrics."""
from __future__ import annotations

import unittest

from antopt.cases import lens28_var
from antopt.core import SpecError
from antopt.recommend import training_set


class CompleteTests(unittest.TestCase):
    def setUp(self):
        self.space = lens28_var.build_space()

    def test_missing_dimensions_take_their_backward_compatible_defaults(self):
        legacy = {"tip_offset": 1.0, "disc_factor": 0.95, "disc_radial": 1.0,
                  "ring_inner": 1.2, "ring_outer": 2.3, "ring_height": 0.5,
                  "ring_shift": 0.0}
        completed = self.space.complete(legacy)
        self.assertEqual(set(completed), {spec.name for spec in self.space})
        self.assertEqual(completed["ring_mid"], 3.0)
        self.assertEqual(completed["ring_height_outer"], 0.6)
        self.assertEqual(completed["ring_outer"], 2.3)

    def test_complete_is_idempotent(self):
        full = dict(self.space.default())
        self.assertEqual(self.space.complete(full), full)

    def test_unknown_keys_are_still_rejected(self):
        with self.assertRaises(SpecError):
            self.space.complete({**self.space.default(), "bogus": 1.0})

    def test_strict_validation_has_not_been_weakened(self):
        legacy = {"ring_outer": 2.3}
        with self.assertRaises(SpecError):
            self.space.validate(legacy)


class LegacyTrainingTests(unittest.TestCase):
    def test_defaults_do_not_invent_historical_training_dimensions(self):
        names = [spec.name for spec in lens28_var.build_space()]
        defaults = dict(lens28_var.build_space().default())
        runs = [{"status": "COMPLETED",
                 "params": {"tip_offset": 1.0, "disc_factor": 0.95, "disc_radial": 1.0,
                            "ring_inner": 1.2, "ring_outer": 2.3, "ring_height": 0.5,
                            "ring_shift": 0.0},
                 "metrics": {"s11_bandwidth": 2.0, "peak_gain": 6.4}}]
        x, y, used = training_set(runs, names, defaults=defaults)
        self.assertEqual(x.shape[0], 0, "missing physical dimensions need an explicit migration")
        self.assertEqual(used, [])

    def test_without_defaults_the_old_strict_behaviour_remains(self):
        names = [spec.name for spec in lens28_var.build_space()]
        runs = [{"status": "COMPLETED",
                 "params": {"ring_outer": 2.3},
                 "metrics": {"s11_bandwidth": 2.0, "peak_gain": 6.4}}]
        x, _y, used = training_set(runs, names)
        self.assertEqual(x.shape[0], 0)
        self.assertEqual(used, [])


if __name__ == "__main__":
    unittest.main()
