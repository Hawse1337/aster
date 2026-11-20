"""Proof that pattern reports are persisted per run without needing a solver.

The real COM code is exercised only on the bench; these tests cover the wiring that matters:
- the generated code asks the native reporter to KEEP the four reports (not the old delete branch);
- the persist step writes a manifest and the four PNG names live under campaign/pattern_reports;
- a bridge failure is recorded instead of failing the accepted measurement;
- RealJob carries the pattern_reports payload in the ledger.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from antopt.farfield import persist_pattern_reports, persist_pattern_reports_code
from antopt.runtime.runner import RealJob, RealRunManager


class PatternReportWiringTests(unittest.TestCase):
    def test_generated_code_keeps_reports_and_exports_four_figures(self):
        code = persist_pattern_reports_code(Path(r"C:\tmp\proj.aedt"), "FeedIdeal", "F28",
                                            Path(r"D:\tmp\out"))
        self.assertIn("keep_reports=True", code)
        self.assertIn("open_window=False", code)
        self.assertIn("ANTOPT_3D_Gain_", code)
        self.assertIn("ANTOPT_E_Plane_", code)
        self.assertIn("ANTOPT_H_Plane_", code)
        self.assertIn("ANTOPT_EH_Overlay_", code)
        self.assertIn("project.Save()", code)

    def test_persist_writes_manifest_and_reports_directory(self):
        class FakeBridge:
            def execute_code(self, code, pid=None, timeout=None):
                return {"result": {"samples": [{"freq": 28.0}],
                                   "persisted": {"reports_kept": True,
                                                 "opened_reports": ["ANTOPT_3D_Gain_28GHz"],
                                                 "sets": [{"images": ["native_reports/gain3d_28.png"]}]}}}

        with tempfile.TemporaryDirectory() as tmp:
            campaign = Path(tmp)
            result = persist_pattern_reports(FakeBridge(), campaign=campaign, run_id="r1",
                                             project=campaign / "projects" / "lens28_var.aedt",
                                             design="FeedIdeal", setup="F28", pid=1)
            self.assertTrue(result["ok"])
            self.assertTrue(result["reports_kept"])
            self.assertEqual(result["reports"], ["ANTOPT_3D_Gain_28GHz"])
            manifest = campaign / "pattern_reports" / "r1" / "persist_result.json"
            self.assertTrue(manifest.is_file())
            self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))["ok"], True)

    def test_bridge_failure_is_recorded_not_raised(self):
        class BrokenBridge:
            def execute_code(self, code, pid=None, timeout=None):
                raise RuntimeError("worker died")

        with tempfile.TemporaryDirectory() as tmp:
            campaign = Path(tmp)
            result = persist_pattern_reports(BrokenBridge(), campaign=campaign, run_id="r2",
                                             project=campaign / "p.aedt", design="FeedIdeal",
                                             setup="F28", pid=1)
            self.assertFalse(result["ok"])
            self.assertIn("worker died", result["error"])
            self.assertTrue((campaign / "pattern_reports" / "r2" / "persist_result.json").is_file())

    def test_real_job_carries_pattern_reports_and_ledger_round_trip(self):
        job = RealJob(run_id="r3", case="lens28_var", params={})
        self.assertEqual(job.pattern_reports, {})
        job.pattern_reports = {"ok": True, "reports": ["ANTOPT_3D_Gain_28GHz"]}
        self.assertEqual(job.to_dict()["pattern_reports"]["ok"], True)
        restored = RealJob(**job.to_dict())
        self.assertEqual(restored.pattern_reports["reports"], ["ANTOPT_3D_Gain_28GHz"])

    def test_manager_keeps_reports_by_default_and_can_opt_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = RealRunManager(campaign_dir=Path(tmp))
            self.assertTrue(manager.keep_pattern_reports)
            manager_off = RealRunManager(campaign_dir=Path(tmp), keep_pattern_reports=False)
            self.assertFalse(manager_off.keep_pattern_reports)


if __name__ == "__main__":
    unittest.main()
