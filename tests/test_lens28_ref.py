"""Tests for the reference lens family (tip offset + disc thickness)."""
from __future__ import annotations

import os
import unittest

from antopt.cases import lens28_ref
from antopt.parametric.aedt import OperationError, render, validate_operations
from antopt.parametric.registry import clear_registry, get_case, list_cases, register_case

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")

PARAMS = {"tip_offset": 1.0, "disc_factor": 1.0, "disc_radial": 1.0,
          "ring_inner": 1.2, "ring_outer": 3.0, "ring_height": 0.6,
          "ring_shift": 0.0, "ring2_outer": 0.0, "ring2_height": 0.4,
          "ring2_shift": -0.4, "feed_radius": 0.4, "feed_len": 1.6,
          "feed_z0": -2.8}


class ReferenceCaseTests(unittest.TestCase):
    def test_parameters_and_baseline(self):
        case = lens28_ref.build_case()
        names = [spec.name for spec in case.family.param_space]
        self.assertEqual(names[:6], ["tip_offset", "disc_factor", "disc_radial",
                                     "ring_inner", "ring_outer", "ring_height"])
        self.assertEqual(names[6:], ["ring_shift", "ring2_outer", "ring2_height",
                                     "ring2_shift", "feed_radius", "feed_len",
                                     "feed_z0"])
        clean = case.family.param_space.validate(PARAMS)
        self.assertEqual(clean["tip_offset"], 1.0)
        self.assertEqual(clean["disc_factor"], 1.0)
        self.assertEqual(case.setup_name, "P4")

    def test_bandwidth_is_a_hard_requirement(self):
        case = lens28_ref.build_case()
        kinds = {constraint.kind for constraint in case.constraints}
        self.assertIn("s11_bandwidth", kinds)
        objectives = {objective.kind for objective in case.objectives}
        self.assertIn("s11_bandwidth", objectives)

    def test_operations_scale_disc_then_place_bowls(self):
        ops = lens28_ref.operations_full({"tip_offset": 1.1, "disc_factor": 1.1,
                                    "disc_radial": 0.7})
        names = [op["op"] for op in ops]
        self.assertEqual(names[0], "scale_xy_about_axis")
        self.assertEqual(names[1], "scale_z_about_centre")
        self.assertIn("gap_bowls", names)
        radial = ops[0]["args"]
        self.assertEqual(radial["name"], "disc")
        self.assertAlmostEqual(radial["factor"], 0.7)
        thickness = ops[1]["args"]
        self.assertEqual(thickness["name"], "disc")
        self.assertAlmostEqual(thickness["factor"], 1.1)

    def test_geometry_checks_reject_absurd_thickness(self):
        model = lens28_ref.build_model()
        ok = model.validate_geometry(PARAMS)
        self.assertTrue(ok.valid, ok.messages)
        bad = model.validate_geometry({"tip_offset": 1.0, "disc_factor": 1.6,
                                       "disc_radial": 1.0})
        self.assertFalse(bad.valid)

    def test_registration(self):
        clear_registry()
        register_case(lens28_ref.register())
        self.assertEqual(list_cases(), ["lens28_ref"])
        registration = get_case("lens28_ref")
        self.assertEqual(registration.build_case().case_id, "lens28_ref")
        self.assertTrue(registration.build_model().space.default())


class ScaleZRenderingTests(unittest.TestCase):
    def test_scale_about_centre_keeps_the_object_in_place(self):
        code = render(
            [{"op": "scale_z_about_centre", "args": {"name": "disc", "factor": 0.9}},
             {"op": "validate_simple", "args": {"attempts": 1}}],
            project_path=r"D:\sandbox\b0.aedt", design_name="B0_f24_thin",
        )
        self.assertIn("_mid = (_box[2] + _box[5]) / 2.0", code)
        self.assertIn("app.modeler.scale(['disc'], 1.0, 1.0, 0.9)", code)
        self.assertIn("_mid - (_box[2] + _box[5]) / 2.0", code)

    def test_scale_requires_a_name_and_positive_factor(self):
        with self.assertRaises(OperationError):
            validate_operations([{"op": "scale_z_about_centre",
                                  "args": {"factor": 1.1}}])
        with self.assertRaises(OperationError):
            validate_operations([{"op": "scale_z_about_centre",
                                  "args": {"name": "disc", "factor": 0}}])


class ScaleXYRenderingTests(unittest.TestCase):
    def test_radial_scale_only_touches_x_and_y(self):
        code = render(
            [{"op": "scale_xy_about_axis", "args": {"name": "disc", "factor": 0.7}}],
            project_path=r"D:\sandbox\b0.aedt", design_name="B0_f24_thin",
        )
        self.assertIn("app.modeler.scale(['disc'], 0.7, 0.7, 1.0)", code)

    def test_radial_scale_requires_name_and_positive_factor(self):
        with self.assertRaises(OperationError):
            validate_operations([{"op": "scale_xy_about_axis",
                                  "args": {"factor": 0.7}}])
        with self.assertRaises(OperationError):
            validate_operations([{"op": "scale_xy_about_axis",
                                  "args": {"name": "disc", "factor": -0.2}}])


if __name__ == "__main__":
    unittest.main()

class StepRingRenderingTests(unittest.TestCase):
    """Structural operator: PEC annulus bridging the coax mouth and the lower bowl."""

    def test_ring_is_created_and_united_into_the_target(self):
        code = render(
            [{"op": "add_step_ring", "args": {"inner_radius": 1.2, "outer_radius": 3.0,
                                              "height": 0.6}}],
            project_path=r"D:\sandbox\b0.aedt", design_name="B0_f24_thin",
        )
        self.assertIn("_ring_z = _coax_end - 0.3 + 0.0", code)
        self.assertIn("app.modeler.subtract('antopt_ring_out', 'antopt_ring_in'", code)
        # NOTE: the unite absorbs cone_dn, so a design can only be parameterised
        # once; see tests/test_step_ring_idempotency.py for the full history
        self.assertIn(
            "app.modeler.unite(['coax_outer', 'antopt_ring_out', 'cone_dn'])", code)

    def test_ring_arguments_are_validated(self):
        with self.assertRaises(OperationError):
            validate_operations([{"op": "add_step_ring",
                                  "args": {"inner_radius": 0, "outer_radius": 3.0,
                                           "height": 0.6}}])
        with self.assertRaises(OperationError):
            validate_operations([{"op": "add_step_ring",
                                  "args": {"inner_radius": 3.0, "outer_radius": 2.0,
                                           "height": 0.6}}])
        with self.assertRaises(OperationError):
            validate_operations([{"op": "add_step_ring",
                                  "args": {"inner_radius": 1.2, "outer_radius": 3.0,
                                           "height": 0}}])

class TransitionConeTests(unittest.TestCase):
    def test_cone_is_created_and_united(self):
        code = render(
            [{"op": "add_transition_cone",
              "args": {"radius_bottom": 1.2, "radius_top": 6.2, "height": 1.5}}],
            project_path=r"D:\sandbox\b0.aedt", design_name="B0_f24_thin",
        )
        self.assertIn("app.modeler.create_cone(", code)
        self.assertIn("radius_bottom=1.2, radius_top=6.2, height=1.5", code)
        self.assertIn("app.modeler.unite(['coax_outer', 'antopt_cone', 'cone_dn'])", code)
        self.assertIn('_antopt_ops.append({"op": "add_transition_cone"})', code)

    def test_cone_arguments_are_validated(self):
        with self.assertRaises(OperationError):
            validate_operations([{"op": "add_transition_cone",
                                  "args": {"radius_bottom": 6.2, "radius_top": 1.2,
                                           "height": 1.5}}])
        with self.assertRaises(OperationError):
            validate_operations([{"op": "add_transition_cone",
                                  "args": {"radius_bottom": 1.2, "radius_top": 6.2,
                                           "height": 0}}])

class InnerSleeveTests(unittest.TestCase):
    def test_bore_is_widened_before_the_sleeve(self):
        code = render(
            [{"op": "add_inner_sleeve",
              "args": {"radius": 0.7, "z0": -2.8, "length": 1.6}}],
            project_path=r"D:\sandbox\b0.aedt", design_name="B0_f24_thin",
        )
        self.assertIn("app.modeler.subtract('coax_diel', 'antopt_bore'", code)
        self.assertIn("radius=0.7, height=1.6, name='antopt_sleeve'", code)
        self.assertIn("app.modeler.unite(['coax_inner', 'antopt_sleeve'])", code)

    def test_sleeve_must_exceed_the_inner_conductor(self):
        with self.assertRaises(OperationError):
            validate_operations([{"op": "add_inner_sleeve",
                                  "args": {"radius": 0.4, "z0": -2.8, "length": 1.6}}])
        with self.assertRaises(OperationError):
            validate_operations([{"op": "add_inner_sleeve",
                                  "args": {"radius": 0.7, "z0": -2.8, "length": 0}}])
