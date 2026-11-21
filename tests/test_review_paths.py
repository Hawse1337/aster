"""Regression checks through the campaign, artifact and transport entry points."""
import os
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from aster.acceptance import replay
from aster.band_acceptance import BandAcceptanceSpec
from aster.bridge import InProcessBridge, StdioBridge
from aster.cases.lens28 import build_case
from aster.executor import GuardConfig, GuardExecutor, run_campaign
from aster.farfield import save_snapshot, view


class CampaignBoundaryTests(unittest.TestCase):
    def test_bad_batch_never_evaluates_a_candidate(self):
        case = build_case()
        executor = Mock()
        for batch, proposals in [(0, []), (1, []), (2, [{}, {}, {}])]:
            with self.subTest(batch=batch), self.assertRaises(ValueError):
                run_campaign(case, executor, batch=batch, budget=2,
                             strategy=lambda *args: proposals)
        executor.evaluate.assert_not_called()

    def test_ambiguous_target_refused_before_model_preparation(self):
        prepare = Mock()
        config = GuardConfig("campaign", "project", "design", pid=1, port=2, prepare=prepare)
        with self.assertRaises(ValueError):
            GuardExecutor(build_case(), config, bridge=Mock())
        prepare.assert_not_called()

    def test_passing_summary_cannot_override_failed_guard_frequency(self):
        case = build_case()
        params = {"step_L": 2, "step_ro": 0.8, "wedge": 1, "cone_dz": 0}
        values = {"s11": -14.0, "peak_gain": 8.0, "ripple": 1.0}
        checks = {"s11": 1, "peak_gain": 0, "ripple": 1}
        config = GuardConfig("campaign", "project", "design", pid=1)
        for cached, legacy in [(False, False), (True, False), (True, True)]:
            with self.subTest(cached=cached, legacy=legacy):
                bridge = Mock()
                bridge.submit.return_value = {"run_id": "r", "status": "COMPLETED",
                                              "cached": cached, "metrics": values}
                if not legacy:
                    bridge.submit.return_value["metric_checks"] = checks
                bridge.status.return_value = {"run_id": "r", "terminal": True, "status": "COMPLETED",
                    "metrics": {k: {"value": v, "passed": checks[k]} for k, v in values.items()}}
                result = GuardExecutor(case, config, bridge).evaluate(case.make_candidate(params))
                self.assertFalse(result.feasible)
                self.assertGreater(result.violations["peak_gain"], 0)


class ArtifactTests(unittest.TestCase):
    def test_conflicting_periodic_seam_blocks_only_the_affected_frequency(self):
        rows = [[f, t, p, 7.5 if t == 90 else -25.0] for f in [27, 28]
                for t in range(0, 181, 10) for p in range(0, 361, 10)]
        rows.append([28, 90, 360, -5])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_snapshot(root, "run1", {"coordinate_system": "Global", "samples": rows})
            result = replay(root, "run1", BandAcceptanceSpec((26, 29), 1), astrabb_target=True)
            self.assertEqual(result["pattern"]["status"], "INSUFFICIENT_EVIDENCE")
            self.assertEqual(result["pattern"]["rows"][0]["status"], "SAMPLED_PASS")
            self.assertIn("Conflicting", result["pattern"]["rows"][1]["reason"])

    def test_prediction_label_survives_public_view(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_snapshot(root, "run1", {"samples": [[28, 90, 0, 1]], "source": "PREDICTED"})
            self.assertEqual(view(root, "run1")["source"], "PREDICTED")


class BridgeTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("REVIEW_GUARD_SERVER"), "guard checkout required")
    def test_real_mcp_returns_the_unexecuted_out_of_scope_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "fixture.aedt"
            project.write_bytes(b"offline fixture")
            script = root / "fixture.py"
            script.write_text("def run(**args): raise AssertionError('must not execute')\n")
            digest = lambda file: hashlib.sha256(file.read_bytes()).hexdigest()
            policy = root / "policy.json"
            policy.write_text(json.dumps({"builders": {"lens": {
                "script": str(script), "sha256": digest(script), "entrypoint": "run",
                "output_roots": [str(root / "output")],
                "targets": [{"project_path": str(project), "design": "D",
                             "project_sha256": digest(project)}],
                "params": {"radius": {"type": "number", "required": True, "min": 19.2, "max": 21.6}}
            }}}), encoding="utf8")
            environment = {"AEDT_GUARD_BUILDERS": str(policy), "AEDT_MCP_STATE_DIR": str(root/"state")}
            with StdioBridge(args=[os.environ["REVIEW_GUARD_SERVER"]], env=environment) as bridge:
                result = bridge.run_approved_builder(builder_id="lens", project_path=str(project),
                    outdir=str(root/"output"), params={"design": "D", "radius": 22.0}, pid=7)
            self.assertEqual(result["status"], "approval_required")
            self.assertFalse(result["executed"])
            self.assertEqual(result["request"]["project_sha256"], digest(project))
            self.assertFalse((root/"output").exists())

    def test_bridges_preserve_the_operator_code_policy(self):
        flag = "AEDT_MCP_ALLOW_ARBITRARY_CODE"
        with patch.dict(os.environ, clear=False):
            os.environ.pop(flag, None)
            self.assertNotIn(flag, StdioBridge().env)
            bridge = InProcessBridge.__new__(InProcessBridge)
            bridge._mcp = Mock()
            def check(**arguments):
                self.assertNotIn(flag, os.environ)
            bridge._mcp.connect_to_aedt.side_effect = check
            bridge._mcp.run_python_code.side_effect = check
            bridge.connect(pid=1)
            bridge.execute_code("", pid=1)

    @unittest.skipUnless(os.environ.get("REVIEW_GUARD_SERVER"), "guard checkout required")
    def test_real_stdio_preserves_spec_across_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            server = os.environ["REVIEW_GUARD_SERVER"]
            with StdioBridge(args=[server]) as bridge:
                spec = build_case().to_spec()
                created = bridge.create_spec(directory, spec)
                self.assertEqual(created["spec"]["objectives"], spec["objectives"])
                loaded = bridge._call("spec_get", {"campaign_dir": directory})
                self.assertEqual(loaded["spec"], created["spec"])
                self.assertEqual(len(loaded["versions"]), 1)
            with self.assertRaises(RuntimeError):
                bridge.archive(directory)
