"""The variable-driven case must reproduce the reference geometry and stay idempotent.

The model rebuilds the two bowls and the disc from parameterised profiles, raises the
ground ring as a collar on the bowl tip face and drills the reference hole arrays. It never
unites anything: the only boolean operations are the hole subtractions the reference design
itself performs.
"""
from __future__ import annotations

import os
import unittest
from collections import Counter

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")

from antopt.cases import lens28_var
from antopt.parametric.aedt import render


def ops_by_name(ops, name):
    return next(op["args"]["points"] for op in ops
                if op["op"] == "revolve_profile" and op["args"]["name"] == name)


class VariableCaseTests(unittest.TestCase):
    def setUp(self):
        self.model = lens28_var.build_model()
        self.base = self.model.space.default()

    def test_default_bowl_matches_the_reference_profile(self):
        ops = lens28_var.operations_full(dict(self.base, ring_outer=2.0))
        bowl = ops_by_name(ops, "cone_dn")
        # no ring at the wall radius, so the profile is the plain nine-point bowl
        self.assertEqual(len(bowl), 9)
        self.assertEqual(bowl[0], [0.0, -1.0])
        self.assertEqual(bowl[2], [9.5, -4.8])
        self.assertEqual(bowl[3], [24.0, -4.8])
        self.assertEqual(bowl[4], [24.0, -5.8])

    def test_ring_is_a_collar_on_the_bowl_tip_face(self):
        ops = lens28_var.operations_full(dict(self.base, ring_outer=2.2, ring_height=0.6))
        bowl = ops_by_name(ops, "cone_dn")
        self.assertIn([1.2, -1.0], bowl)
        self.assertIn([1.2, -0.7], bowl)
        self.assertIn([2.2, -0.7], bowl)
        self.assertIn([2.2, -1.0], bowl)

    def test_ring_height_and_shift_move_the_collar_top(self):
        ops = lens28_var.operations_full(dict(self.base, ring_outer=2.4, ring_height=1.0,
                                              ring_shift=0.1))
        bowl = ops_by_name(ops, "cone_dn")
        zs = [point[1] for point in bowl if abs(point[0] - 2.4) < 1e-9]
        self.assertAlmostEqual(max(zs), -1.0 + 0.5 + 0.1)

    def test_ring_at_the_wall_radius_adds_no_collar(self):
        ops = lens28_var.operations_full(dict(self.base, ring_outer=1.2))
        self.assertEqual(len(ops_by_name(ops, "cone_dn")), 9)

    def test_reference_hole_arrays_are_drilled(self):
        ops = lens28_var.operations_full(dict(self.base, ring_outer=2.2))
        counts = Counter(op["op"] for op in ops)
        self.assertEqual(counts["create_cylinder"], 36)
        self.assertEqual(counts["subtract"], 2)
        holes = {op["args"]["name"] for op in ops if op["op"] == "create_cylinder"}
        self.assertEqual(len(holes), 36)
        small = [name for name in holes if name.startswith("hole_small")]
        rim = [name for name in holes if name.startswith("hole_rim")]
        self.assertEqual(len(small), 12)   # six per bowl
        self.assertEqual(len(rim), 24)     # twelve per bowl

    def test_no_union_is_used_to_attach_the_ring(self):
        ops = lens28_var.operations_full(dict(self.base, ring_outer=2.2))
        names = {op["op"] for op in ops}
        self.assertNotIn("unite", names)
        self.assertNotIn("add_step_ring", names)
        code = render(ops, project_path=r"X:\p.aedt", design_name="f24g05", port=1)
        self.assertNotIn("app.modeler.unite", code)

    def test_tip_offset_moves_both_bowls_symmetrically(self):
        default = lens28_var.operations_full(dict(self.base, ring_outer=2.0))
        deeper = lens28_var.operations_full(dict(self.base, ring_outer=2.0, tip_offset=1.2))
        down_default = ops_by_name(default, "cone_dn")
        down_deeper = ops_by_name(deeper, "cone_dn")
        self.assertAlmostEqual(min(p[1] for p in down_deeper),
                               min(p[1] for p in down_default) - 0.2)
        up_default = ops_by_name(default, "cone_up")
        up_deeper = ops_by_name(deeper, "cone_up")
        self.assertAlmostEqual(max(p[1] for p in up_deeper),
                               max(p[1] for p in up_default) + 0.2)

    def test_disc_scales_about_its_mid_plane(self):
        thick = lens28_var.operations_full(dict(self.base, disc_factor=1.1))
        disc = ops_by_name(thick, "disc")
        self.assertEqual(len(disc), 237)
        zs = [point[1] for point in disc]
        self.assertAlmostEqual((min(zs) + max(zs)) / 2.0, 0.0, places=6)

    def test_spec_carries_the_touchstone_retry_policy(self):
        # the sweep is often still solving when the export arrives; without the retries the
        # guard sees a one-row touchstone and refuses the run
        from antopt.cases import lens28_var as module

        policy = module.build_case().to_spec()["execution_policy"]
        self.assertGreaterEqual(policy["touchstone_retries"], 60)
        self.assertGreater(policy["touchstone_retry_delay_s"], 0)

    def test_radiation_efficiency_is_refused_with_both_reasons(self):
        from antopt.cases import lens28_var as module

        with self.assertRaises(ValueError) as caught:
            module.build_case(include_efficiency=True)
        message = str(caught.exception)
        self.assertIn("sphere-grid", message)
        self.assertIn("pending verification", message)
        self.assertNotIn("radiation_efficiency",
                         [o.kind for o in module.build_case().objectives])

    def test_geometry_rules_reject_impossible_parameters(self):
        report = self.model.validate_geometry(dict(self.base, ring_outer=1.5))
        self.assertFalse(report.valid)
        self.assertTrue(self.model.validate_geometry(dict(self.base, tip_offset=1.5)).valid)
        self.assertFalse(self.model.validate_em_setup(dict(self.base, tip_offset=1.5)).valid)


if __name__ == "__main__":
    unittest.main()
