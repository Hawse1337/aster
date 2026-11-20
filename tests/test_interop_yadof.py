"""Interop tests: the yadof bridge must round-trip our parameter definitions."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from antopt.cases import lens28_var
from antopt.core import SpecError
from antopt.interop.yadof import (parameters_constraints_source,
                                  space_from_optimization_setup, to_recorded_data,
                                  write_recorded_data)


class SourceRenderingTests(unittest.TestCase):
    def setUp(self):
        self.space = lens28_var.build_model().space

    def test_renders_parameter_objects_with_ranges_and_units(self):
        source = parameters_constraints_source(self.space)
        compile(source, "parameters_constraints.py", "exec")
        self.assertIn("from yadof.job_template import Parameter", source)
        self.assertIn("Parameter('ring_outer', ((1.8, 5.0),), unit='mm')", source)
        self.assertIn("def get_parameters()", source)
        self.assertIn("CONSTRAINTS = ()", source)

    @unittest.skipUnless(importlib.util.find_spec("yadof") is not None,
                         "yadof is not installed")
    def test_generated_file_satisfies_yadof_parameter_contract(self):
        import tempfile
        from pathlib import Path
        from yadof.job_template.api import get_parameter_definitions

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "job_template").mkdir()
            (root / "job_template" / "parameters_constraints.py").write_text(
                parameters_constraints_source(self.space), encoding="utf-8")
            definitions = get_parameter_definitions(root)
            names = [item.name for item in definitions]
            self.assertEqual(names, [spec.name for spec in self.space.params])
            ring = next(item for item in definitions if item.name == "ring_outer")
            self.assertEqual(ring.unit, "mm")
            self.assertAlmostEqual(ring.ranges[0][0], 1.8)

    def test_module_documents_where_it_came_from(self):
        source = parameters_constraints_source(self.space)
        self.assertIn("antopt.interop.yadof", source)
        self.assertIn("diverge", source)


class OptimizationSetupImportTests(unittest.TestCase):
    ROWS = [
        {"name": "ring_outer", "value": 2.2, "low": 1.8, "high": 5.0, "unit": "mm",
         "include": True},
        {"name": "tip_offset", "value": 1.0, "low": 0.9, "high": 1.4, "unit": "mm",
         "include": True},
        {"name": "Freq", "value": 28.0, "low": 20.0, "high": 40.0, "unit": "GHz",
         "include": False},
    ]

    def test_only_included_variables_become_parameters(self):
        space = space_from_optimization_setup(self.ROWS)
        self.assertEqual([spec.name for spec in space.params], ["ring_outer", "tip_offset"])
        self.assertEqual(space.default()["ring_outer"], 2.2)

    def test_include_only_false_keeps_every_variable(self):
        space = space_from_optimization_setup(self.ROWS, include_only=False)
        self.assertEqual(len(space.params), 3)
        self.assertEqual(space.params[2].unit, "GHz")

    def test_missing_range_or_duplicates_are_rejected(self):
        with self.assertRaises(SpecError):
            space_from_optimization_setup([{"name": "bad", "value": 1.0}])
        with self.assertRaises(SpecError):
            space_from_optimization_setup([
                {"name": "a", "value": 1.0, "low": 0.0, "high": 2.0},
                {"name": "a", "value": 1.0, "low": 0.0, "high": 2.0},
            ])
        with self.assertRaises(SpecError):
            space_from_optimization_setup([])

    def test_discrete_variables_need_choices(self):
        with self.assertRaises(SpecError):
            space_from_optimization_setup([
                {"name": "mode", "value": 1, "low": 0, "high": 2, "type": "discrete"},
            ])
        space = space_from_optimization_setup([
            {"name": "mode", "value": 1, "low": 0, "high": 2, "type": "discrete",
             "choices": [0, 1, 2]},
        ])
        self.assertEqual(space.params[0].kind, "choice")


class RecordedDataTests(unittest.TestCase):
    def test_rows_keep_parameters_outputs_and_cost(self):
        entry = SimpleNamespace(candidate_id="c1", family_id="lens28_var",
                                params={"ring_outer": 2.2},
                                metrics={"s11_bandwidth": 3.0, "peak_gain": 6.4},
                                feasible=True, total_violation=0.0, status="COMPLETED")
        rows = to_recorded_data([entry])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["parameters"]["ring_outer"], 2.2)
        self.assertEqual(rows[0]["outputs"]["peak_gain"], 6.4)
        self.assertAlmostEqual(rows[0]["cost"], 9.4)
        self.assertTrue(rows[0]["feasible"])

    def test_written_file_is_jsonl(self):
        entry = SimpleNamespace(candidate_id="c1", family_id="f", params={},
                                metrics={"a": 1.0}, feasible=False, total_violation=0.5,
                                status="COMPLETED")
        with tempfile.TemporaryDirectory() as tmp:
            path = write_recorded_data(Path(tmp) / "recorded_data" / "rows.jsonl",
                                       to_recorded_data([entry]))
            self.assertTrue(path.is_file())
            lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
            self.assertEqual(len(lines), 1)
            payload = json.loads(lines[0])
            self.assertEqual(payload["candidate_id"], "c1")
            self.assertAlmostEqual(payload["violation"], 0.5)


if __name__ == "__main__":
    unittest.main()
