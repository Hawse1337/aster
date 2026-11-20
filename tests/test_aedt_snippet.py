"""Modelling-operation tests: validation and snippet rendering (no AEDT)."""
from __future__ import annotations

import os
import unittest

from antopt.cases.lens28 import build_case
from antopt.cases.lens28_builder import operations
from antopt.parametric.aedt import (
    OperationError,
    render,
    render_variable_declaration,
    render_variable_update,
    validate_operations,
)

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")


class OperationValidationTests(unittest.TestCase):
    def test_lens28_operations_are_valid(self):
        case = build_case()
        ops = operations({"step_L": 2.0, "step_ro": 0.8, "wedge": 1, "cone_dz": 0.0})
        checked = validate_operations(ops)
        self.assertEqual(len(checked), len(ops))
        self.assertEqual(checked[0]["op"], "delete_if_present")

    def test_unknown_operation_rejected(self):
        with self.assertRaises(OperationError):
            validate_operations([{"op": "explode_model"}])

    def test_malformed_arguments_rejected(self):
        with self.assertRaises(OperationError):
            validate_operations([{"op": "create_cylinder", "args": {"name": "x"}}])
        with self.assertRaises(OperationError):
            validate_operations([{"op": "subtract", "args": {"target": "a"}}])
        with self.assertRaises(OperationError):
            validate_operations([{"op": "set_wedge", "args": {"present": "yes"}}])
        with self.assertRaises(OperationError):
            validate_operations([{"op": "delete_if_present", "args": {"names": []}}])


class SnippetRenderingTests(unittest.TestCase):
    def _render(self, params, port=59131):
        case = build_case()
        return render(
            operations(params),
            project_path=r"D:\sandbox\b0_copy.aedt",
            design_name=case.notes and "B0_f24_thin",
            port=port,
        )

    def test_snippet_attaches_existing_project_and_never_solves(self):
        code = self._render({"step_L": 2.0, "step_ro": 0.8, "wedge": 1, "cone_dz": 0.0})
        self.assertIn("_AntoptHfss(", code)
        self.assertIn("B0_f24_thin", code)
        self.assertIn("port=59131", code)
        self.assertNotIn("Analyze", code)
        self.assertNotIn("analyze", code)
        self.assertIn("app.modeler.create_cylinder", code)
        self.assertIn("app.modeler.subtract", code)
        self.assertIn("validate_simple", code)

    def test_parameters_are_baked_into_the_snippet(self):
        code = self._render({"step_L": 3.25, "step_ro": 0.65, "wedge": 0, "cone_dz": -0.4})
        self.assertIn("3.25", code)
        self.assertIn("0.65", code)
        self.assertIn("-0.4", code)
        # wedge disabled -> deletion branch, not the wireframe branch
        self.assertIn("app.modeler.delete('wedge')", code)
        self.assertNotIn("display_wireframe", code)

    def test_validation_result_is_reported(self):
        code = self._render({"step_L": 1.0, "step_ro": 0.9, "wedge": 1, "cone_dz": 0.2})
        self.assertIn("'validated': all(", code)
        self.assertIn("'objects': sorted(app.modeler.object_names)", code)

    def test_variable_declaration_snippet(self):
        code = render_variable_declaration(
            {"step_L": "2mm", "step_ro": "0.8mm"},
            project_path=r"D:\sandbox\b0_copy.aedt", design_name="B0_f24_thin", port=59131,
        )
        self.assertIn('app["step_L"] = "2mm"', code)
        self.assertIn('app["step_ro"] = "0.8mm"', code)
        self.assertNotIn("create_cylinder", code)
        self.assertNotIn("Analyze", code)

    def test_variable_update_snippet_validates(self):
        code = render_variable_update(
            {"step_L": "3mm", "cone_dz": "-0.2mm"},
            project_path=r"D:\sandbox\b0_copy.aedt", design_name="B0_f24_thin",
        )
        self.assertIn('app["step_L"] = "3mm"', code)
        self.assertIn("validate_simple", code)
        self.assertIn("'updated'", code)


if __name__ == "__main__":
    unittest.main()

class StructureOperationSnippetTests(unittest.TestCase):
    def test_scale_and_shift_are_rendered(self):
        from antopt.parametric.aedt import render

        code = render(
            [{"op": "scale_objects", "args": {"targets": ["disc", "disc2"], "factor": 1.06}},
             {"op": "shift_objects", "args": {"targets": ["disc"], "delta": [0, 0, -0.4]}},
             {"op": "validate_simple", "args": {"attempts": 2}}],
            project_path=r"D:\sandbox\b0_copy.aedt", design_name="B0_f24_thin",
        )
        self.assertIn("app.modeler.scale([\"disc\", \"disc2\"], 1.06, 1.06, 1.06)", code)
        self.assertIn("app.modeler.move([\"disc\"], [0.0, 0.0, -0.4])", code)
        self.assertNotIn("Analyze", code)

    def test_bad_structure_arguments_are_rejected(self):
        from antopt.parametric.aedt import OperationError, validate_operations

        with self.assertRaises(OperationError):
            validate_operations([{"op": "scale_objects", "args": {"targets": []}}])
        with self.assertRaises(OperationError):
            validate_operations([{"op": "scale_objects",
                                  "args": {"targets": ["disc"], "factor": 0}}])
        with self.assertRaises(OperationError):
            validate_operations([{"op": "shift_objects",
                                  "args": {"targets": ["disc"], "delta": [0, 0]}}])
