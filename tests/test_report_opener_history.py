"""Opening old history must leave the current project's launcher intact."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

if not (Path(__file__).parents[1] / "aster" / "__init__.py").exists():
    sys.path.insert(0, r"D:\AntennaDesignSystem")
    spec = importlib.util.spec_from_file_location(
        "aster.farfield", Path(__file__).parents[1] / "aster" / "farfield.py")
    farfield = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(farfield)
else:
    from aster import farfield


class ReportOpenerHistoryTests(unittest.TestCase):
    def test_same_saved_project_keeps_the_last_launched_gui_pid(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "Saved.aedt"
            project.write_bytes(b"same geometry")
            digest = hashlib.sha256(project.read_bytes()).hexdigest()
            manifest = project.with_suffix(".aster-reports.json")
            manifest.write_text(json.dumps({"project": str(project.resolve()), "design": "Antenna",
                                           "reports": ["E", "H"], "pid": 81,
                                           "project_sha256": digest}), encoding="utf-8")
            with patch("aster.config.load", return_value=SimpleNamespace(venv_python=None)):
                farfield.write_report_opener(project, "Antenna", ["E", "H"], pid=40, project_sha256=digest)
            self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))["pid"], 81)

    def test_different_project_hash_uses_the_new_snapshot_pid(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "Saved.aedt"
            project.write_bytes(b"new geometry")
            digest = hashlib.sha256(project.read_bytes()).hexdigest()
            manifest = project.with_suffix(".aster-reports.json")
            manifest.write_text(json.dumps({"project": str(project.resolve()), "design": "Antenna",
                                           "reports": ["E", "H"], "pid": 81,
                                           "project_sha256": "0" * 64}), encoding="utf-8")
            with patch("aster.config.load", return_value=SimpleNamespace(venv_python=None)):
                farfield.write_report_opener(project, "Antenna", ["E", "H"], pid=40, project_sha256=digest)
            self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))["pid"], 40)

    def test_old_run_does_not_overwrite_current_project_sidecars(self):
        with tempfile.TemporaryDirectory() as temporary:
            campaign = Path(temporary)
            project = campaign / "Reused.aedt"
            project.write_bytes(b"current geometry and reports")
            manifest = project.with_suffix(".aster-reports.json")
            current_manifest = json.dumps({"project": str(project), "design": "Current",
                                           "reports": ["CurrentE", "CurrentH"], "pid": 72,
                                           "capture_sha256": hashlib.sha256(project.read_bytes()).hexdigest()}).encode()
            manifest.write_bytes(current_manifest)
            launcher = project.with_name(project.stem + "_查看方向图.cmd")
            current_launcher = b"@echo off\r\necho current launcher\r\n"
            launcher.write_bytes(current_launcher)
            farfield.save_snapshot(campaign, "old-run", {
                "project": str(project), "design": "Current", "aedt_pid": 40,
                "project_sha256": hashlib.sha256(b"previous geometry").hexdigest(),
                "native_reports": {"saved": True, "opened_reports": ["OldE", "OldH"]}})
            with patch("subprocess.run") as launch:
                with self.assertRaisesRegex(ValueError, "changed since"):
                    farfield.open_saved_reports(campaign, "old-run")
            launch.assert_not_called()
            self.assertEqual(manifest.read_bytes(), current_manifest)
            self.assertEqual(launcher.read_bytes(), current_launcher)

    def test_normal_aedt_save_does_not_disable_latest_launcher(self):
        with tempfile.TemporaryDirectory() as temporary:
            campaign = Path(temporary)
            project = campaign / "Saved.aedt"
            project.write_bytes(b"solved project")
            digest = hashlib.sha256(project.read_bytes()).hexdigest()
            reports = ["3D", "E", "H", "EH"]
            farfield.save_snapshot(campaign, "latest", {
                "project": str(project), "design": "Antenna", "aedt_pid": 40,
                "project_sha256": digest,
                "native_reports": {"saved": True, "opened_reports": reports}})
            with patch("aster.config.load", return_value=SimpleNamespace(venv_python=None)):
                farfield.write_report_opener(project, "Antenna", reports, 81, digest)
                # Saving window/layout metadata changes the .aedt file as observed on 2026 R1.
                project.write_bytes(b"solved project plus saved window positions")
                with patch("subprocess.run", return_value=SimpleNamespace(returncode=0)) as launch:
                    farfield.open_saved_reports(campaign, "latest")
                self.assertEqual(launch.call_count, 1)
            manifest = json.loads(project.with_suffix(".aster-reports.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["capture_sha256"], digest)
            self.assertNotIn("project_sha256", manifest)
            self.assertEqual(manifest["pid"], 81)


if __name__ == "__main__":
    unittest.main()
