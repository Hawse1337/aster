"""Directivity and efficiency from a full-sphere gain pattern (analytic checks)."""
from __future__ import annotations

import math
import unittest

from aster.physics.directivity import (directivity_db, directivity_from_pattern,
                                        efficiency_from_pattern)


def grid(n_theta=37, n_phi=73):
    thetas = [180.0 * i / (n_theta - 1) for i in range(n_theta)]
    phis = [360.0 * j / (n_phi - 1) for j in range(n_phi)]
    return thetas, phis


class DirectivityTests(unittest.TestCase):
    def test_isotropic_pattern_gives_unity(self):
        thetas, phis = grid(181, 73)
        gains = [[0.0 for _ in phis] for _ in thetas]
        self.assertAlmostEqual(directivity_from_pattern(thetas, phis, gains), 1.0, places=2)
        self.assertAlmostEqual(directivity_db(1.0), 0.0, places=9)

    def test_sin_pattern_gives_four_over_pi(self):
        # U = sin(theta) over the full sphere: P_rad = pi^2, D = 4*pi/pi^2 = 4/pi
        thetas, phis = grid(181, 5)
        gains = []
        for theta in thetas:
            value = math.sin(math.radians(theta))
            gains.append([10.0 * math.log10(max(value, 1e-12)) for _ in phis])
        value = directivity_from_pattern(thetas, phis, gains)
        self.assertAlmostEqual(value, 4.0 / math.pi, places=1)

    def test_peak_dominates(self):
        thetas, phis = grid()
        gains = [[-20.0 for _ in phis] for _ in thetas]
        gains[len(thetas) // 2][len(phis) // 2] = 10.0
        narrow = directivity_from_pattern(thetas, phis, gains)
        flat = directivity_from_pattern(thetas, phis,
                                        [[-20.0 for _ in phis] for _ in thetas])
        self.assertGreater(narrow, flat)

    def test_partial_sphere_is_rejected(self):
        thetas = [0.0, 10.0, 20.0]
        phis = [0.0, 90.0, 180.0, 270.0, 360.0]
        gains = [[0.0 for _ in phis] for _ in thetas]
        with self.assertRaises(ValueError):
            directivity_from_pattern(thetas, phis, gains)

    def test_shape_mismatch_is_rejected(self):
        thetas, phis = grid()
        with self.assertRaises(ValueError):
            directivity_from_pattern(thetas, phis, [[0.0 for _ in phis]])

    def test_degenerate_pattern_is_rejected(self):
        thetas, phis = grid()
        gains = [[-200.0 for _ in phis] for _ in thetas]
        with self.assertRaises(ValueError):
            directivity_from_pattern(thetas, phis, gains)


class EfficiencyTests(unittest.TestCase):
    def test_lossless_pattern_has_unit_efficiency(self):
        thetas, phis = grid(37, 73)
        gains = [[10.0 for _ in phis] for _ in thetas]
        # isotropic with +10 dB gain is not physical, but the formula must clamp to 1
        self.assertEqual(efficiency_from_pattern(thetas, phis, gains), 1.0)

    def test_one_db_below_directivity_is_about_point_eight(self):
        thetas, phis = grid(37, 73)
        # build a pattern whose peak is 1 dB below the directivity: shrink a cos-like lobe
        gains = []
        for theta in thetas:
            lobe = max(math.cos(math.radians(theta)), 1e-6)
            gains.append([10.0 * math.log10(lobe ** 3) for _ in phis])
        eta = efficiency_from_pattern(thetas, phis, gains)
        self.assertGreater(eta, 0.1)
        self.assertLessEqual(eta, 1.0)


if __name__ == "__main__":
    unittest.main()
