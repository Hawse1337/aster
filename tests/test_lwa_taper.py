"""P4: the leaky-wave rib profile is described by three numbers, not twenty heights."""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")

from antopt.cases import lwa
from antopt.parametric.aedt import render


class HeightLawTests(unittest.TestCase):
    def setUp(self):
        self.base = dict(lwa.build_model().space.default())

    def test_flat_default_gives_one_height_for_every_rib(self):
        heights = lwa.unit_heights(self.base)
        self.assertEqual(len(heights), int(self.base["n_units"]))
        self.assertTrue(all(abs(value - self.base["h_mm"]) < 1e-12 for value in heights))

    def test_linear_term_tilts_the_profile(self):
        heights = lwa.unit_heights(dict(self.base, a1=1.0))
        self.assertAlmostEqual(heights[0], self.base["h_mm"])
        self.assertAlmostEqual(heights[-1], self.base["h_mm"] + 1.0)
        rising = all(b > a for a, b in zip(heights, heights[1:]))
        self.assertTrue(rising)

    def test_quadratic_term_bends_the_profile(self):
        heights = lwa.unit_heights(dict(self.base, a2=-1.0))
        middle = heights[len(heights) // 2]
        self.assertLess(middle, heights[0] - 0.1)
        self.assertAlmostEqual(heights[-1], self.base["h_mm"] - 1.0)

    def test_law_matches_the_documented_formula(self):
        params = dict(self.base, n_units=5, h_mm=2.0, a1=0.6, a2=-0.3)
        heights = lwa.unit_heights(params)
        expected = [2.0 + 0.6 * u + (-0.3) * u * u for u in (0.0, 0.25, 0.5, 0.75, 1.0)]
        for got, want in zip(heights, expected):
            self.assertAlmostEqual(got, want, places=9)

    def test_single_rib_does_not_divide_by_zero(self):
        heights = lwa.unit_heights(dict(self.base, n_units=1))
        self.assertEqual(heights, [self.base["h_mm"]])


class GeometryGuardTests(unittest.TestCase):
    def setUp(self):
        self.base = dict(lwa.build_model().space.default())

    def test_default_profile_passes(self):
        self.assertTrue(lwa.geometry_checks(self.base).valid)

    def test_profile_that_dips_below_the_wall_is_rejected(self):
        report = lwa.geometry_checks(dict(self.base, h_mm=0.6, a1=-1.5, a2=0.0))
        self.assertFalse(report.valid)
        self.assertIn("taper_stays_printable", report.checks)
        self.assertFalse(report.checks["taper_stays_printable"])

    def test_profile_that_reaches_the_period_is_rejected(self):
        report = lwa.geometry_checks(dict(self.base, p_mm=6.0, h_mm=4.0, a1=1.5, a2=1.5))
        self.assertFalse(report.valid)
        self.assertFalse(report.checks["taper_stays_below_period"])


class RenderingTests(unittest.TestCase):
    def test_rendered_snippet_uses_the_additive_law(self):
        params = dict(lwa.build_model().space.default(), a1=0.5, a2=-0.2)
        code = render(lwa.operations(params), project_path=r"X:\lwa.aedt",
                      design_name="LWA", port=1)
        self.assertIn("_h = 2.0 + 0.5 * _t + -0.2 * _t * _t", code)
        self.assertNotIn("2.0 * (1.0 + 0.5", code)

    def test_relative_mode_is_still_available(self):
        code = render([{"op": "create_tapered_units",
                        "args": {"count": 2, "pitch": 10.0, "h0": 2.0, "a1": 0.5,
                                 "a2": 0.0, "width": 5.0, "depth": 1.0,
                                 "mode": "relative"}}],
                      project_path=r"X:\lwa.aedt", design_name="LWA", port=1)
        self.assertIn("_h = 2.0 * (1.0 + 0.5 * _t", code)

    def test_unknown_mode_is_rejected(self):
        from antopt.parametric.aedt import OperationError, validate_operations

        with self.assertRaises(OperationError):
            validate_operations([{"op": "create_tapered_units",
                                  "args": {"count": 2, "pitch": 10.0, "h0": 2.0,
                                           "width": 5.0, "depth": 1.0,
                                           "mode": "cubic"}}])


if __name__ == "__main__":
    unittest.main()
