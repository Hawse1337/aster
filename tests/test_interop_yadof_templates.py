"""Generated yadof workspace files must satisfy the installed package's contract."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from aster.interop.yadof_templates import (cost_source, evaluation_source,
                                           write_workspace_files)

HAS_YADOF = importlib.util.find_spec("yadof") is not None


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class EvaluationTemplateTests(unittest.TestCase):
    def test_exposes_the_session_api_yadof_imports(self):
        source = evaluation_source(campaign_dir="D:\\camp", project="D:\\camp\\p.aedt",
                                   pid=4321)
        compile(source, "evaluation.py", "exec")
        self.assertIn("def create_evaluation_session", source)
        self.assertIn("def evaluate_rawdata", source)
        self.assertIn("PID = 4321", source)
        self.assertIn("ModelApplier", source)
        self.assertIn("GuardExecutor", source)

    def test_session_creates_the_band_sweep_once(self):
        source = evaluation_source(campaign_dir="c", project="p", port=1,
                                   band_ghz=(24.0, 30.0), sweep_step_ghz=1.0)
        self.assertIn("def _ensure_sweep", source)
        # the sweep itself now comes from the shared builder
        self.assertIn("setup_config_code", source)
        self.assertIn("BAND_LOW = 24.0", source)
        self.assertIn("SWEEP_STEP = 1.0", source)
        # the sweep is created when the session connects, not per candidate
        self.assertIn("self._ensure_sweep(bridge)", source)
        self.assertNotIn("self._ensure_sweep(bridge)\n            self._ensure_sweep", source)

    def test_sweep_is_configured_once_per_session(self):
        source = evaluation_source(campaign_dir="c", project="p", port=1)
        # the definition plus a single call site, both inside the connect/ensure path
        self.assertIn("def _ensure_sweep", source)
        self.assertEqual(source.count("self._ensure_sweep(bridge)"), 1)

    def test_candidate_call_does_not_configure_the_setup(self):
        source = evaluation_source(campaign_dir="c", project="p", port=1)
        start = source.find("def _evaluate_once")
        body = source[start:]
        self.assertNotIn("setup_config_code", body)
        self.assertNotIn("_ensure_sweep", body)

    def test_sweep_is_not_rebuilt_per_candidate(self):
        # rebuilding raises InsertFrequencySweep and leaves a partially solved sweep, which
        # showed up as a 1 GHz band against the batch arm's 3 GHz
        source = evaluation_source(campaign_dir="c", project="p", port=1)
        self.assertNotIn("self._refresh_sweep(bridge, params)", source)

    def test_existing_spec_is_not_republished(self):
        source = evaluation_source(campaign_dir="c", project="p", port=1)
        self.assertIn("design_spec.json", source)

    def test_farfield_setup_is_named(self):
        # without it the export inherits the S11 sweep and CreateReport fails on a real solve
        source = evaluation_source(campaign_dir="c", project="p", port=1)
        self.assertIn("farfield_setup_name=SETUP", source)

    def test_requires_a_connection(self):
        with self.assertRaises(ValueError):
            evaluation_source(campaign_dir="c", project="p")

    def test_efficiency_adds_an_objective(self):
        source = evaluation_source(campaign_dir="c", project="p", port=1,
                                   include_efficiency=True)
        self.assertIn("radiation_efficiency", source)

    @unittest.skipUnless(HAS_YADOF, "yadof is not installed")
    def test_error_payload_matches_the_rawdata_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evaluation.py"
            path.write_text(evaluation_source(campaign_dir=tmp,
                                              project=str(Path(tmp) / "p.aedt"),
                                              port=50000), encoding="utf-8")
            module = load_module(path, "generated_evaluation")
            session = module.create_evaluation_session({"session_dir": tmp})
            payload = session._error_payload("boom")
            (name, data), = payload.items()
            self.assertEqual(name, "response.npz")
            from yadof.job_template.rawdata_contract import validate_rawdata_item
            validated = validate_rawdata_item(data)
            self.assertEqual(validated["values"].shape, (0,))


class CostTemplateTests(unittest.TestCase):
    def test_maximised_metrics_become_negative_costs(self):
        source = cost_source()
        compile(source, "calc_cost.py", "exec")
        self.assertIn("OBJECTIVE_NAMES = tuple(", source)
        self.assertIn("'s11_bandwidth', 'peak_gain'", source)
        self.assertIn("-value if kind in MAXIMISE else value", source)

    @unittest.skipUnless(HAS_YADOF, "yadof is not installed")
    def test_cost_module_returns_one_cost_per_objective(self):
        import json

        import numpy as np
        from yadof.job_template.rawdata_contract import validate_rawdata_item

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "calc_cost.py"
            path.write_text(cost_source(), encoding="utf-8")
            module = load_module(path, "generated_cost")
            metadata = {"schema_version": 1, "rawdata_name": "response", "shape": [2]}
            item = validate_rawdata_item({
                "values": np.asarray([3.0, 6.5]),
                "metadata": json.dumps(metadata),
            })
            costs = module.calculate_cost([item])
            self.assertEqual(len(costs), 2)
            self.assertAlmostEqual(costs[0], -3.0)
            self.assertAlmostEqual(costs[1], -6.5)

    @unittest.skipUnless(HAS_YADOF, "yadof is not installed")
    def test_broken_response_is_charged_the_error_cost(self):
        import json

        import numpy as np
        from yadof.job_template.rawdata_contract import validate_rawdata_item

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "calc_cost.py"
            path.write_text(cost_source(), encoding="utf-8")
            module = load_module(path, "generated_cost_error")
            metadata = {"schema_version": 1, "rawdata_name": "response", "shape": [0]}
            item = validate_rawdata_item({
                "values": np.asarray([], dtype=float),
                "metadata": json.dumps(metadata),
            })
            costs = module.calculate_cost([item])
            self.assertEqual(costs, (module.ERROR_COST, module.ERROR_COST))


class WriteWorkspaceTests(unittest.TestCase):
    def test_writes_into_an_initialised_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "job_template").mkdir()
            (root / "submit").mkdir()
            paths = write_workspace_files(root, campaign_dir=tmp,
                                          project=str(root / "p.aedt"), port=50001)
            self.assertTrue(Path(paths["evaluation"]).is_file())
            self.assertTrue(Path(paths["cost"]).is_file())

    def test_refuses_a_directory_that_is_not_a_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                write_workspace_files(tmp, campaign_dir=tmp, project="p", port=1)


if __name__ == "__main__":
    unittest.main()
