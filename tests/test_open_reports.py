"""Explicit-PID native report opener: no real AEDT or guard calls."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

if not (Path(__file__).parents[1] / "aster" / "__init__.py").exists():
    sys.path.insert(0, r"D:\AntennaDesignSystem")
    spec = importlib.util.spec_from_file_location(
        "aster.open_reports", Path(__file__).parents[1] / "aster" / "open_reports.py")
    opener = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(opener)
else:
    from aster import open_reports as opener


class OpenReportsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "Saved.aedt"
        self.project.write_text("saved", encoding="utf-8")
        self.executable = Path(self.tmp.name) / "ansysedt.exe"
        self.executable.write_text("exe", encoding="utf-8")
        self.manifest = {"project": str(self.project), "design": "Antenna", "reports": ["3D", "E", "H", "EH"], "pid": 40}
        self.env = SimpleNamespace(aedt_exe=self.executable, guard_repo=None)
        self.bridge = Mock()
        self.bridge.execute_code.return_value = {"result": {"opened": self.manifest["reports"]}}

    def run_open(self):
        return opener.open_saved_reports(self.manifest, environment=self.env, bridge=self.bridge)

    def test_existing_gui_uses_only_recorded_pid_and_no_launch(self):
        process = Mock()
        process.cmdline.return_value = [str(self.executable), "-grpcsrv", "12345"]
        with patch.object(opener, "_process", return_value=process), patch.object(opener.subprocess, "Popen") as launch:
            self.assertEqual(self.run_open()["pid"], 40)
        launch.assert_not_called()
        self.assertEqual(self.bridge.execute_code.call_count, 1)
        self.assertEqual(self.bridge.execute_code.call_args.kwargs["pid"], 40)
        self.bridge.connect.assert_not_called()

    def test_dead_pid_starts_gui_and_addresses_its_exact_pid(self):
        self.bridge.execute_code.side_effect = [{"result": {"ready": True}}, {"result": {"opened": self.manifest["reports"]}}]
        with patch.object(opener, "_process", return_value=None), patch.object(opener, "_free_port", return_value=32001), patch.object(opener.subprocess, "Popen", return_value=SimpleNamespace(pid=81)) as launch:
            self.assertEqual(self.run_open()["pid"], 81)
        self.assertEqual(launch.call_args.args[0], [str(self.executable), "-grpcsrv", "32001"])
        self.assertEqual([call.kwargs["pid"] for call in self.bridge.execute_code.call_args_list], [81, 81])

    def test_ng_release_precedes_gui_launch(self):
        process = Mock()
        process.cmdline.return_value = [str(self.executable), "-ng", "-grpcsrv", "22"]
        responses = [{"result": {"released": True}}, {"result": {"ready": True}}, {"result": {"opened": self.manifest["reports"]}}]
        events = []
        self.bridge.execute_code.side_effect = lambda *a, **k: (events.append(("code", k["pid"])), responses.pop(0))[1]
        def launch(*args, **kwargs):
            events.append(("launch", 81))
            return SimpleNamespace(pid=81)
        with patch.object(opener, "_process", return_value=process), patch.object(opener.subprocess, "Popen", side_effect=launch):
            self.assertEqual(self.run_open()["pid"], 81)
        self.assertEqual(events, [("code", 40), ("launch", 81), ("code", 81), ("code", 81)])

    def test_failed_ng_release_does_not_launch_or_close_anything_else(self):
        process = Mock()
        process.cmdline.return_value = ["ansysedt.exe", "-ng"]
        self.bridge.execute_code.side_effect = RuntimeError("project save failed")
        with patch.object(opener, "_process", return_value=process), patch.object(opener.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(RuntimeError, "save failed"):
                self.run_open()
        launch.assert_not_called()

    def test_readiness_retries_remain_bounded_and_explicit(self):
        bridge = Mock()
        bridge.execute_code.side_effect = RuntimeError("starting")
        with patch.object(opener.time, "monotonic", side_effect=[0, 0, 0, 1, 2, 2, 3]), patch.object(opener.time, "sleep"):
            with self.assertRaises(TimeoutError):
                opener._wait_ready(bridge, 71, timeout=2)
        self.assertTrue(bridge.execute_code.called)
        self.assertTrue(all(c.kwargs["pid"] == 71 and 0 < c.kwargs["timeout"] <= 2 for c in bridge.execute_code.call_args_list))

    def test_manifest_validation_precedes_any_connection(self):
        self.manifest["project"] = "relative.aedt"
        with self.assertRaisesRegex(ValueError, "absolute"):
            self.run_open()
        self.bridge.execute_code.assert_not_called()

    def test_changed_project_hash_refuses_to_connect(self):
        self.manifest["project_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "changed since"):
            self.run_open()
        self.bridge.execute_code.assert_not_called()

    def test_manifest_remembers_new_gui_for_the_next_launch(self):
        manifest_path = Path(self.tmp.name) / "reports.json"
        manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        selected = []
        def opened(manifest):
            selected.append(manifest["pid"])
            return {"opened": manifest["reports"], "pid": 81}
        with patch.object(opener, "open_saved_reports", side_effect=opened):
            self.assertEqual(opener.main(["--manifest", str(manifest_path)]), 0)
            self.assertEqual(opener.main(["--manifest", str(manifest_path)]), 0)
        self.assertEqual(selected, [40, 81])
        self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8"))["pid"], 81)

    def test_pid_update_preserves_a_manifest_replaced_during_open(self):
        manifest_path = Path(self.tmp.name) / "reports.json"
        manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        newer = {**self.manifest, "pid": 99, "reports": ["NewRunE", "NewRunH"]}
        def opened(manifest):
            manifest_path.write_text(json.dumps(newer), encoding="utf-8")
            return {"opened": manifest["reports"], "pid": 81}
        with patch.object(opener, "open_saved_reports", side_effect=opened):
            self.assertEqual(opener.main(["--manifest", str(manifest_path)]), 0)
        self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8")), newer)

    def test_serialized_code_never_solves_or_closes_all_projects(self):
        code = opener.report_code(self.project, "Antenna", ["3D"], release_only=True)
        self.assertNotIn("Analyze", code)
        self.assertNotIn("close_projects", code)
        self.assertIn("odesktop.CloseProject(name)", code)
        self.assertIn("str(actual).casefold() == str(expected).casefold()", code)


class NativeCodeTests(unittest.TestCase):
    def matching_project(self, busy=False):
        project = Mock()
        project.GetPath.return_value = r"C:\target"
        project.GetName.return_value = "Saved"
        desktop = Mock()
        desktop.GetProjectList.return_value = ["Saved"]
        desktop.SetActiveProject.return_value = project
        desktop.AreThereSimulationsRunning.return_value = busy
        return project, desktop

    def test_busy_ng_project_is_neither_saved_nor_closed(self):
        project, desktop = self.matching_project(busy=True)
        with self.assertRaisesRegex(RuntimeError, "running a simulation"):
            exec(opener.report_code(Path(r"C:\target\Saved.aedt"), "Design", ["E"], release_only=True), {"odesktop": desktop})
        project.Save.assert_not_called()
        desktop.CloseProject.assert_not_called()

    def test_failed_busy_query_is_neither_saved_nor_closed(self):
        project, desktop = self.matching_project()
        desktop.AreThereSimulationsRunning.side_effect = RuntimeError("query failed")
        with self.assertRaisesRegex(RuntimeError, "Cannot verify whether AEDT is busy"):
            exec(opener.report_code(Path(r"C:\target\Saved.aedt"), "Design", ["E"], release_only=True), {"odesktop": desktop})
        project.Save.assert_not_called()
        desktop.CloseProject.assert_not_called()

    def test_idle_matching_ng_project_is_saved_before_close(self):
        project, desktop = self.matching_project()
        events = []
        project.Save.side_effect = lambda: events.append("save")
        desktop.CloseProject.side_effect = lambda name: events.append(("close", name))
        context = {"odesktop": desktop}
        exec(opener.report_code(Path(r"C:\target\Saved.aedt"), "Design", ["E"], release_only=True), context)
        self.assertTrue(context["result"]["released"])
        self.assertEqual(events, ["save", ("close", "Saved")])

    def test_release_ignores_same_name_project_from_another_directory(self):
        project = Mock()
        project.GetPath.return_value = r"C:\unrelated"
        project.GetName.return_value = "Saved"
        desktop = Mock()
        desktop.GetProjectList.return_value = ["Saved"]
        desktop.SetActiveProject.return_value = project
        context = {"odesktop": desktop}
        exec(opener.report_code(Path(r"C:\target\Saved.aedt"), "Design", ["E"], release_only=True), context)
        self.assertFalse(context["result"]["released"])
        project.Save.assert_not_called()
        desktop.CloseProject.assert_not_called()
        desktop.AreThereSimulationsRunning.assert_not_called()

    def test_same_name_collision_fails_before_opening_reports(self):
        project = Mock()
        project.GetPath.return_value = r"C:\unrelated"
        project.GetName.return_value = "Saved"
        desktop = Mock()
        desktop.GetProjectList.return_value = ["Saved"]
        desktop.SetActiveProject.return_value = project
        with self.assertRaisesRegex(ValueError, "different project"):
            exec(opener.report_code(Path(r"C:\target\Saved.aedt"), "Design", ["E"]), {"odesktop": desktop})
        desktop.OpenProject.assert_not_called()


if __name__ == "__main__":
    unittest.main()
