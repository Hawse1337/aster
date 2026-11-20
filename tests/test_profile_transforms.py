"""Variable-driven profile transforms: bowls shift, the disc scales, the coax steps.

The reference profiles come from `scripts/extract_profiles.py` (nine bowl points, the
237-point disc, the coax tube). Every parameter becomes a coordinate transform, so an
apply is "delete the managed bodies and revolve the new profiles" - no boolean operation
ever runs, which is what makes repeated parameter changes idempotent.
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")

from aster.parametric.profile import (coax_with_flange, load_profiles, scale_disc,
                                       shift_profile)

PROFILES = Path(__file__).resolve().parent.parent / "aster" / "cases" / "lens28_profiles.json"


class ExtractedProfilesTests(unittest.TestCase):
    def test_all_reference_bodies_are_present(self):
        self.assertTrue(PROFILES.is_file(), "run scripts/extract_profiles.py")
        payload = json.loads(PROFILES.read_text(encoding="utf-8"))
        bodies = payload["bodies"]
        for name in ("cone_dn", "cone_up", "disc", "coax_outer", "coax_diel"):
            self.assertIn(name, bodies)
        self.assertEqual(len(bodies["cone_dn"]["points"]), 9)
        self.assertEqual(len(bodies["disc"]["points"]), 237)
        # the bowl template in profile.py must match the project data
        self.assertEqual([list(p) for p in bodies["cone_dn"]["points"]][:2],
                         [[0.0, -1.0], [6.2, -1.0]])

    def test_loader_returns_floats(self):
        profiles = load_profiles(PROFILES)
        self.assertEqual(profiles["cone_dn"][2], [9.5, -4.8])


class TransformTests(unittest.TestCase):
    def setUp(self):
        self.profiles = load_profiles(PROFILES)

    def test_shift_moves_z_only(self):
        moved = shift_profile(self.profiles["cone_dn"], -0.2)
        self.assertAlmostEqual(min(p[1] for p in moved), -6.0)
        self.assertEqual([p[0] for p in moved], [p[0] for p in self.profiles["cone_dn"]])

    def test_disc_thickness_scales_about_the_mid_plane(self):
        disc = self.profiles["disc"]
        thick = scale_disc(disc, 1.0, 1.1)
        mid_original = (min(p[1] for p in disc) + max(p[1] for p in disc)) / 2.0
        mid_scaled = (min(p[1] for p in thick) + max(p[1] for p in thick)) / 2.0
        self.assertAlmostEqual(mid_original, mid_scaled)
        span_original = max(p[1] for p in disc) - min(p[1] for p in disc)
        span_scaled = max(p[1] for p in thick) - min(p[1] for p in thick)
        self.assertAlmostEqual(span_scaled, span_original * 1.1, places=6)

    def test_disc_radial_scales_only_x(self):
        disc = self.profiles["disc"]
        wide = scale_disc(disc, 1.2, 1.0)
        self.assertAlmostEqual(max(p[0] for p in wide),
                               max(p[0] for p in disc) * 1.2, places=6)
        self.assertAlmostEqual(min(p[1] for p in wide), min(p[1] for p in disc))

    def test_coax_flange_steps_the_outer_wall(self):
        tube = self.profiles["coax_outer"]
        stepped = coax_with_flange(tube, 2.2, 0.6)
        self.assertEqual(max(p[0] for p in stepped), 2.2)
        self.assertIn([2.2, -1.3], stepped)
        self.assertIn([2.2, -0.7], stepped)
        # the inner wall and the mouth stay where they were
        self.assertEqual(min(p[0] for p in stepped), 1.2)
        self.assertEqual(stepped[0], stepped[-1])

    def test_flange_can_be_shifted_and_must_be_wider(self):
        tube = self.profiles["coax_outer"]
        shifted = coax_with_flange(tube, 2.4, 0.4, -1.0, 0.1)
        self.assertAlmostEqual(max(p[0] for p in shifted), 2.4)
        zs = [p[1] for p in shifted if abs(p[0] - 2.4) < 1e-9]
        self.assertAlmostEqual(min(zs), -1.1, places=6)
        self.assertAlmostEqual(max(zs), -0.7, places=6)
        with self.assertRaises(ValueError):
            coax_with_flange(tube, 2.0, 0.6)
        with self.assertRaises(ValueError):
            coax_with_flange(tube, 2.2, 0.0)

    def test_invalid_scale_factors_are_rejected(self):
        with self.assertRaises(ValueError):
            scale_disc(self.profiles["disc"], 0.0, 1.0)
        with self.assertRaises(ValueError):
            scale_disc(self.profiles["disc"], 1.0, -1.0)


if __name__ == "__main__":
    unittest.main()
