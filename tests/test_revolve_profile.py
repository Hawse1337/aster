"""Revolve-profile geometry: the reference bodies are closed polylines around Z.

The measured design is built from three such profiles (lower/upper bowl, the coax with its
inner pin, and the corrugated disc). Rebuilding them from parameters - instead of scaling
and uniting existing bodies - is what makes repeated parameter changes idempotent.
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")

from aster.parametric.aedt import OperationError, render, validate_operations
from aster.parametric.profile import (BOWL_TEMPLATE, bowl_profile, with_ground_ring,
                                       with_ring_collar)


class BowlProfileTests(unittest.TestCase):
    def test_template_matches_the_reference_project(self):
        self.assertEqual(len(BOWL_TEMPLATE), 9)
        self.assertEqual(BOWL_TEMPLATE[0], (0.0, -1.0))
        self.assertEqual(BOWL_TEMPLATE[2], (9.5, -4.8))
        self.assertEqual(BOWL_TEMPLATE[4], (24.0, -5.8))
        self.assertEqual(BOWL_TEMPLATE[-1], BOWL_TEMPLATE[0])

    def test_tip_offset_moves_the_whole_profile(self):
        # the tip plane sits at -tip_offset, so the whole bowl moves with it
        default = bowl_profile(1.0, 1.0)
        deeper = bowl_profile(1.2, 1.0)
        self.assertAlmostEqual(max(p[1] for p in default), -1.0)
        self.assertAlmostEqual(max(p[1] for p in deeper), -1.2)
        self.assertAlmostEqual(min(p[1] for p in deeper),
                               min(p[1] for p in default) - 0.2)
        # radii are untouched by a tip change
        self.assertEqual([p[0] for p in default], [p[0] for p in deeper])

    def test_profile_is_closed_and_non_negative(self):
        profile = bowl_profile(1.0, 1.0)
        self.assertEqual(profile[0], profile[-1])
        self.assertTrue(all(point[0] >= 0 for point in profile))

    def test_invalid_parameters_are_rejected(self):
        with self.assertRaises(ValueError):
            bowl_profile(0.0, 1.0)
        with self.assertRaises(ValueError):
            bowl_profile(1.0, -0.1)

    def test_ring_collar_sits_on_the_tip_face(self):
        # the measured ring is a collar standing half its height above the bowl tip face
        collar = with_ring_collar(bowl_profile(1.0, 1.0), 1.2, 2.2, 0.6)
        self.assertIn([1.2, -1.0], collar)
        self.assertIn([2.2, -0.7], collar)
        with self.assertRaises(ValueError):
            with_ring_collar(bowl_profile(1.0, 1.0), 2.2, 1.2, 0.6)

    def test_ring_is_welded_into_a_wider_profile(self):
        wide = [[0.0, 0.0], [1.0, 0.0], [1.0, -1.0], [0.0, -1.0], [0.0, 0.0]]
        merged = with_ground_ring(wide, 1.2, 2.2, 0.6)
        self.assertEqual(len(merged), len(wide) - 1 + 4 + 2)
        self.assertEqual(merged[0], merged[-1])


class RevolveOpTests(unittest.TestCase):
    def test_renders_polyline_and_revolve(self):
        profile = bowl_profile(1.0, 1.0)
        code = render([{"op": "revolve_profile",
                        "args": {"points": profile, "name": "bowl_dn"}}],
                      project_path=r"X:\p.aedt", design_name="d", port=1)
        self.assertIn("create_polyline(", code)
        # an open chain is revolved into a solid; a closed sheet would give a shell
        self.assertIn("close_surface=False", code)
        self.assertIn("sweep_around_axis(_poly, axis='Z'", code)
        self.assertIn("sweep_angle=360.0", code)

    def test_existing_object_is_replaced_not_united(self):
        profile = bowl_profile(1.0, 1.0)
        code = render([{"op": "revolve_profile",
                        "args": {"points": profile, "name": "bowl_dn"}}],
                      project_path=r"X:\p.aedt", design_name="d", port=1)
        self.assertIn("if 'bowl_dn' in app.modeler.object_names:", code)
        self.assertIn("app.modeler.delete('bowl_dn')", code)
        self.assertNotIn("unite", code)

    def test_invalid_points_are_rejected(self):
        with self.assertRaises(OperationError):
            validate_operations([{"op": "revolve_profile", "args": {"name": "x"}}])
        with self.assertRaises(OperationError):
            validate_operations([{"op": "revolve_profile",
                                  "args": {"name": "x", "points": [[0, 0], [1, 0]]}}])
        with self.assertRaises(OperationError):
            validate_operations([{"op": "revolve_profile",
                                  "args": {"name": "x", "points": [[-1, 0], [1, 0], [0, 1]]}}])


if __name__ == "__main__":
    unittest.main()
