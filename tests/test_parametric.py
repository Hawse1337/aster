"""Parameterisation tests (S1): definitions, models, registry, snapshots."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from antopt.core import SpecError
from antopt.parametric.model import GeometryReport, OperationModel, check_manufacturing
from antopt.parametric.registry import (
    CaseRegistration,
    clear_registry,
    get_case,
    list_cases,
    register_case,
)
from antopt.parametric.spec import ParamDefinition, ParamFile

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")


def _param_file() -> ParamFile:
    return ParamFile.from_dict(
        {
            "version": 1,
            "case": "demo",
            "params": [
                {"name": "a", "unit": "mm", "low": 0.0, "high": 10.0, "default": 5.0,
                 "kind": "float", "step": 0.1, "physics": "first axis",
                 "manufacturing": {"min_gap_mm": 0.3}},
                {"name": "b", "unit": "", "low": 0, "high": 2, "default": 1,
                 "kind": "choice", "choices": [0, 1, 2]},
            ],
        }
    )


class ParamFileTests(unittest.TestCase):
    def test_roundtrip_and_space(self):
        param_file = _param_file()
        space = param_file.space()
        self.assertEqual([p.name for p in space], ["a", "b"])
        self.assertEqual(space.validate({"a": 3.0, "b": 1}), {"a": 3.0, "b": 1})
        payload = param_file.to_dict()
        self.assertEqual(payload["params"][0]["physics"], "first axis")

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmp:
            path = Path(tmp) / "lens28_params.json"
            _param_file().save(path)
            loaded = ParamFile.load(path)
            self.assertEqual(loaded.case, "demo")
            self.assertEqual(len(loaded.params), 2)

    def test_invalid_definitions_rejected(self):
        with self.assertRaises(SpecError):
            ParamDefinition.from_dict({"name": "x", "low": 5.0, "high": 1.0,
                                       "default": 3.0, "kind": "float"})
        with self.assertRaises(SpecError):
            ParamDefinition.from_dict({"name": "x", "low": 0, "high": 1, "default": 0,
                                       "kind": "choice"})
        with self.assertRaises(SpecError):
            ParamDefinition.from_dict({"name": "x", "low": 0, "high": 1, "default": 2,
                                       "kind": "float"})
        with self.assertRaises(SpecError):
            ParamFile.from_dict({"version": 1, "case": "x", "params": [
                {"name": "a", "low": 0, "high": 1, "default": 0, "kind": "float"},
                {"name": "a", "low": 0, "high": 1, "default": 0, "kind": "float"},
            ]})

    def test_unknown_keys_rejected(self):
        with self.assertRaises(SpecError):
            ParamDefinition.from_dict({"name": "x", "low": 0, "high": 1, "default": 0,
                                       "kind": "float", "typo": 1})


class OperationModelTests(unittest.TestCase):
    def _model(self, applied=None):
        space = _param_file().space()

        def operations(params):
            return [{"op": "set", "args": {"a": params["a"], "b": params["b"]}}]

        return OperationModel(space, operations, apply=applied)

    def test_build_and_update_track_state(self):
        applied = []
        model = self._model(applied.append)
        model.build({"a": 2.0, "b": 1})
        self.assertEqual(model.current, {"a": 2.0, "b": 1})
        model.update({"a": 3.0, "b": 0})
        self.assertEqual(model.current, {"a": 3.0, "b": 0})
        self.assertEqual(len(applied), 2)
        self.assertIsInstance(applied[0], list)

    def test_noop_update_does_not_apply(self):
        applied = []
        model = self._model(applied.append)
        model.build({"a": 2.0, "b": 1})
        model.update({"a": 2.0, "b": 1})
        self.assertEqual(len(applied), 1)

    def test_invalid_params_rejected_before_apply(self):
        applied = []
        model = self._model(applied.append)
        with self.assertRaises(SpecError):
            model.update({"a": 99.0, "b": 1})
        self.assertEqual(applied, [])

    def test_snapshot_and_restore(self):
        applied = []
        model = self._model(applied.append)
        model.build({"a": 1.0, "b": 0})
        model.snapshot("baseline")
        model.update({"a": 9.0, "b": 2})
        restored = model.restore("baseline")
        self.assertEqual(restored, {"a": 1.0, "b": 0})
        self.assertEqual(model.current, {"a": 1.0, "b": 0})
        with self.assertRaises(SpecError):
            model.restore("missing")

    def test_geometry_validators(self):
        space = _param_file().space()
        model = OperationModel(space, lambda p: [], geometry_checks=lambda p: GeometryReport(
            valid=True).add("ok", True), em_checks=lambda p: GeometryReport(valid=True))
        report = model.validate_geometry()
        self.assertTrue(report.valid)
        self.assertIn("ok", report.checks)
        report = model.validate_em_setup()
        self.assertTrue(report.valid)

    def test_manufacturing_rules(self):
        report = check_manufacturing({"a": 0.2}, {"a": {"min": 0.5}})
        self.assertFalse(report.valid)
        self.assertIn("manufacturing.a", report.checks)


class RegistryTests(unittest.TestCase):
    def tearDown(self):
        clear_registry()

    def test_register_and_lookup(self):
        registration = CaseRegistration(name="demo", case_factory=_param_file)
        register_case(registration)
        self.assertEqual(list_cases(), ["demo"])
        self.assertIs(get_case("demo"), registration)
        with self.assertRaises(ValueError):
            register_case(registration)
        with self.assertRaises(KeyError):
            get_case("nope")

    def test_model_factory_required(self):
        registration = CaseRegistration(name="demo", case_factory=_param_file)
        with self.assertRaises(LookupError):
            registration.build_model()


class Lens28BuilderTests(unittest.TestCase):
    def setUp(self):
        clear_registry()

    def tearDown(self):
        clear_registry()

    def test_lens28_registers_and_builds(self):
        from antopt.cases import lens28_builder

        register_case(lens28_builder.register())
        registration = get_case("lens28")
        case = registration.build_case()
        self.assertEqual(case.case_id, "lens28")
        model = registration.build_model()
        operations = model.build({"step_L": 2.0, "step_ro": 0.8, "wedge": 1, "cone_dz": 0.0})
        names = [op["op"] for op in operations]
        self.assertEqual(names[0], "delete_if_present")
        self.assertIn("subtract", names)
        self.assertEqual(names[-1], "validate_simple")
        report = model.validate_geometry()
        self.assertTrue(report.valid, report.messages)

    def test_lens28_geometry_rules_reject_impossible_step(self):
        from antopt.cases import lens28_builder

        model = lens28_builder.build_model()
        model.current = {"step_L": 2.0, "step_ro": 1.15, "wedge": 1, "cone_dz": 0.0}
        report = model.validate_geometry()
        self.assertFalse(report.valid)
        self.assertIn("step_gap", report.checks)

    def test_lens28_em_rule_rejects_large_cone_offset(self):
        from antopt.cases import lens28_builder

        model = lens28_builder.build_model()
        model.current = {"step_L": 2.0, "step_ro": 0.8, "wedge": 1, "cone_dz": 0.9}
        report = model.validate_em_setup()
        self.assertFalse(report.valid)


if __name__ == "__main__":
    unittest.main()
