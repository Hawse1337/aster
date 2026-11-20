"""Preferences: narrow the search, express a trade-off, and never widen the physics."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from aster import preferences


PARAMS = [
    {"name": "ring_outer", "low": 1.2, "high": 3.0, "unit": "mm", "default": 2.2},
    {"name": "tip_offset", "low": 0.9, "high": 1.4, "unit": "mm", "default": 1.0},
]


class StoreTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "prefs.json"
            stored = preferences.Preferences(
                bounds={"lens28_var": {"ring_outer": [1.5, 2.8]}},
                reference_point={"lens28_var": [2.0, 5.0]},
                weights={"lens28_var": [1.0, 3.0]})
            preferences.save(stored, target)
            loaded = preferences.load(target)
            self.assertEqual(loaded.bounds["lens28_var"]["ring_outer"], [1.5, 2.8])
            self.assertEqual(loaded.reference_point["lens28_var"], [2.0, 5.0])
            self.assertEqual(loaded.weights["lens28_var"], [1.0, 3.0])

    def test_a_missing_file_is_empty_preferences(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = preferences.load(Path(tmp) / "nope.json")
            self.assertEqual(loaded.to_dict(), {"bounds": {}, "reference_point": {}, "weights": {}})

    def test_a_corrupt_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "prefs.json"
            target.write_text("not json", encoding="utf-8")
            self.assertEqual(preferences.load(target).bounds, {})

    def test_the_default_path_is_under_appdata(self):
        self.assertTrue(str(preferences.default_path()).endswith("preferences.json"))


class BoundValidationTests(unittest.TestCase):
    def test_narrowing_is_accepted(self):
        self.assertEqual(preferences.validate_bounds("ring_outer", [1.5, 2.8], [1.2, 3.0]),
                         [1.5, 2.8])

    def test_widening_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            preferences.validate_bounds("ring_outer", [1.0, 3.2], [1.2, 3.0])
        self.assertIn("outside the case limits", str(caught.exception))

    def test_an_inverted_range_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            preferences.validate_bounds("tip_offset", [1.2, 1.0], [0.9, 1.4])
        self.assertIn("must be below", str(caught.exception))

    def test_a_wrong_shape_is_refused(self):
        with self.assertRaises(ValueError):
            preferences.validate_bounds("tip_offset", [1.0], [0.9, 1.4])


class ApplyTests(unittest.TestCase):
    def test_without_preferences_the_parameters_are_unchanged(self):
        result = preferences.apply_bounds("lens28_var", PARAMS, preferences.Preferences())
        self.assertEqual([(item["low"], item["high"]) for item in result],
                         [(1.2, 3.0), (0.9, 1.4)])

    def test_a_stored_bound_replaces_the_case_bound(self):
        stored = preferences.Preferences(bounds={"lens28_var": {"ring_outer": [1.5, 2.8]}})
        result = preferences.apply_bounds("lens28_var", PARAMS, stored)
        self.assertEqual((result[0]["low"], result[0]["high"]), (1.5, 2.8))
        self.assertEqual((result[1]["low"], result[1]["high"]), (0.9, 1.4))

    def test_another_cases_preferences_do_not_leak(self):
        stored = preferences.Preferences(bounds={"lens28_ref": {"ring_outer": [1.5, 2.8]}})
        result = preferences.apply_bounds("lens28_var", PARAMS, stored)
        self.assertEqual((result[0]["low"], result[0]["high"]), (1.2, 3.0))

    def test_a_stored_bound_that_no_longer_fits_is_ignored(self):
        stored = preferences.Preferences(bounds={"lens28_var": {"ring_outer": [1.0, 3.5]}})
        result = preferences.apply_bounds("lens28_var", PARAMS, stored)
        self.assertEqual((result[0]["low"], result[0]["high"]), (1.2, 3.0))

    def test_the_original_list_is_not_mutated(self):
        stored = preferences.Preferences(bounds={"lens28_var": {"ring_outer": [1.5, 2.8]}})
        preferences.apply_bounds("lens28_var", PARAMS, stored)
        self.assertEqual((PARAMS[0]["low"], PARAMS[0]["high"]), (1.2, 3.0))


if __name__ == "__main__":
    unittest.main()
