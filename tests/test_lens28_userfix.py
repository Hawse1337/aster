"""Tests for the user-corrected bowl case and the gap_bowls operation."""
from __future__ import annotations

import os
import unittest

from antopt.cases import lens28_userfix
from antopt.parametric.aedt import OperationError, render, validate_operations
from antopt.parametric.registry import clear_registry, get_case, list_cases, register_case

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")

PARAMS = {"step_L": 2.0, "step_ro": 0.8, "tip_offset": 1.0, "cone_dz": 0.0}


class UserfixCaseTests(unittest.TestCase):
    def test_parameters_are_the_four_axes(self):
        case = lens28_userfix.build_case()
        names = [spec.name for spec in case.family.param_space]
        self.assertEqual(names, ["step_L", "step_ro", "tip_offset", "cone_dz"])
        clean = case.family.param_space.validate(PARAMS)
        self.assertEqual(clean["tip_offset"], 1.0)

    def test_gap_bounds_are_enforced(self):
        case = lens28_userfix.build_case()
        with self.assertRaises(Exception):
            case.family.param_space.validate({**PARAMS, "tip_offset": 5.0})

    def test_operations_include_gap_and_no_wedge(self):
        ops = lens28_userfix.operations(PARAMS)
        names = [op["op"] for op in ops]
        self.assertIn("gap_bowls", names)
        self.assertNotIn("set_wedge", names)
        gap_op = next(op for op in ops if op["op"] == "gap_bowls")
        self.assertEqual(gap_op["args"]["tip_offset_mm"], 1.0)

    def test_checks_pass_for_a_sane_candidate(self):
        model = lens28_userfix.build_model()
        report = model.validate_geometry(PARAMS)
        self.assertTrue(report.valid, report.messages)
        self.assertTrue(model.validate_em_setup(PARAMS).valid)

    def test_bandwidth_is_a_hard_requirement(self):
        case = lens28_userfix.build_case()
        kinds = {constraint.kind for constraint in case.constraints}
        self.assertIn("s11_bandwidth", kinds)
        objectives = {objective.kind for objective in case.objectives}
        self.assertIn("s11_bandwidth", objectives)
        spec = case.to_spec()
        self.assertIn("s11_bandwidth", {m["kind"] for m in spec["metrics"]})

    def test_registration(self):
        clear_registry()
        register_case(lens28_userfix.register())
        self.assertEqual(list_cases(), ["lens28_userfix"])
        registration = get_case("lens28_userfix")
        self.assertEqual(registration.build_case().case_id, "lens28_userfix")
        self.assertTrue(registration.build_model().space.default())


class GapOperationRenderingTests(unittest.TestCase):
    def test_gap_bowls_is_rendered_as_absolute_placement(self):
        code = render(
            [{"op": "gap_bowls", "args": {"tip_offset_mm": 0.8, "disc_offset_mm": 1.0}},
             {"op": "validate_simple", "args": {"attempts": 2}}],
            project_path=r"D:\sandbox\b0.aedt", design_name="B0_f24_thin",
        )
        self.assertIn("_coax_end = app.modeler['coax_outer'].bounding_box[5]", code)
        self.assertIn("_disc_box = app.modeler['disc'].bounding_box", code)
        self.assertIn("app.modeler.move('cone_dn'", code)
        self.assertIn("app.modeler.move('cone_up'", code)
        self.assertIn("0.8", code)
        self.assertNotIn("Analyze", code)

    def test_gap_must_be_positive(self):
        with self.assertRaises(OperationError):
            validate_operations([{"op": "gap_bowls", "args": {"tip_offset_mm": 0}}])
        with self.assertRaises(OperationError):
            validate_operations([{"op": "gap_bowls", "args": {}}])


if __name__ == "__main__":
    unittest.main()





