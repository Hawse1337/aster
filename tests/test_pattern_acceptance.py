import math
import unittest

from aster.pattern_acceptance import ASTRABB_RING_TARGET, assess_ring_pattern


def sphere(gain, *, theta_step=2, phi_step=5):
    theta, phi = list(range(0, 181, theta_step)), list(range(0, 361, phi_step))
    return {"theta_deg": theta, "phi_deg": phi,
            "gain_db": [[gain(t, p) for p in phi] for t in theta]}


class RingAcceptanceTests(unittest.TestCase):
    def test_isotropic_fraction_is_half_and_seam_is_counted_once(self):
        result = assess_ring_pattern(sphere(lambda t, p: 0.0))
        self.assertEqual(result["status"], "SAMPLED_METRICS")
        self.assertEqual(result["grid"]["phi_count"], 72)
        self.assertAlmostEqual(result["metrics"]["belt_power_fraction"], 0.5, places=10)
        self.assertEqual(result["metrics"]["peak_theta_range_deg"], [0, 180])

    def test_sin_squared_belt_matches_solid_angle_integral(self):
        def sin_squared(theta, phi):
            return 10 * math.log10(max(1e-20, math.sin(math.radians(theta)) ** 2))

        result = assess_ring_pattern(sphere(sin_squared))
        self.assertAlmostEqual(result["metrics"]["belt_power_fraction"], 0.6875, places=3)
        self.assertEqual(result["metrics"]["peak_theta_deg"], 90)

    def test_peak_gain_does_not_hide_axial_or_equatorial_failure(self):
        result = assess_ring_pattern(sphere(lambda t, p: 9 if t == 0 else -5),
                                     target=ASTRABB_RING_TARGET)
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["checks"]["horizon_peak_dbi"]["passed"])
        self.assertFalse(result["checks"]["horizon_to_axial_db"]["passed"])

    def test_no_pass_from_sparse_cuts_predicted_or_wrong_quantity(self):
        for grid, kwargs in (
            (None, {}), (sphere(lambda t,p: 0.0, theta_step=45), {}),
            (sphere(lambda t,p: 0.0), {"source": "PREDICTED"}),
            (sphere(lambda t,p: 0.0), {"quantity": "dB(DirectivityTotal)"}),
        ):
            self.assertEqual(assess_ring_pattern(grid, **kwargs)["status"], "INSUFFICIENT_EVIDENCE")

    def test_inconsistent_periodic_seam_is_rejected(self):
        result = assess_ring_pattern(sphere(lambda t,p: p/180))
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        self.assertIn("seam", result["reason"])
