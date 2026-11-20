"""Consistency-check tests and environment guard tests."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from antopt.apply import ModelApplier
from antopt.campaign import CampaignRunner, EnvironmentUnhealthy
from antopt.cases import lens28_builder
from antopt.cases.lens28 import build_case
from antopt.executor import GuardConfig
from antopt.parametric.consistency import (
    boxes_from_snippet_result,
    check_inside,
    check_structure_consistency,
)

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")

PARAMS = {"step_L": 2.0, "step_ro": 0.8, "wedge": 1, "cone_dz": 0.0}


def _boxes(disc_size=140.0, region_size=300.0, port=(0.0, 0.0, -20.0)):
    half = region_size / 2.0
    return {
        "disc": {"min": [-disc_size / 2, -disc_size / 2, -2.0],
                 "max": [disc_size / 2, disc_size / 2, 2.0]},
        "disc2": {"min": [-25.0, -25.0, -4.0], "max": [25.0, 25.0, -3.0]},
        "rad_region": {"min": [-half, -half, -half], "max": [half, half, half]},
        "port_disc": {"min": [port[0] - 1.2, port[1] - 1.2, port[2] - 0.01],
                      "max": [port[0] + 1.2, port[1] + 1.2, port[2] + 0.01]},
    }


class ConsistencyTests(unittest.TestCase):
    def test_consistent_structure_passes(self):
        report = check_structure_consistency(_boxes())
        self.assertTrue(report.ok, report.messages)
        self.assertTrue(report.checks["disc_inside_region"])

    def test_structure_outside_region_fails(self):
        boxes = _boxes(disc_size=340.0, region_size=300.0)
        report = check_structure_consistency(boxes)
        self.assertFalse(report.ok)
        self.assertFalse(report.checks["disc_inside_region"])

    def test_port_movement_is_detected(self):
        baseline = _boxes()["port_disc"]
        boxes = _boxes(port=(0.0, 0.0, -19.0))
        report = check_structure_consistency(boxes, baseline_port=baseline)
        self.assertFalse(report.ok)
        self.assertFalse(report.checks["port_unchanged"])

    def test_missing_object_is_reported(self):
        boxes = _boxes()
        boxes.pop("rad_region")
        report = check_structure_consistency(boxes)
        self.assertFalse(report.ok)
        self.assertIn("objects_present", report.checks)

    def test_margin_can_reject_touching_boxes(self):
        inner = {"min": [-1.0, -1.0, -1.0], "max": [1.0, 1.0, 1.0]}
        outer = {"min": [-1.0, -1.0, -1.0], "max": [1.0, 1.0, 1.0]}
        self.assertTrue(check_inside(inner, outer))
        self.assertFalse(check_inside(inner, outer, margin_mm=0.5))

    def test_payload_normalisation(self):
        payload = {"disc": {"size": [10, 10, 2], "min": [0, 0, 0], "max": [10, 10, 2]},
                   "junk": {"size": [1, 1, 1]}}
        boxes = boxes_from_snippet_result(payload)
        self.assertIn("disc", boxes)
        self.assertNotIn("junk", boxes)


class FakeBridge:
    def __init__(self, healthy=True, metrics=None):
        self.healthy = healthy
        self.metrics = metrics or {"s11": {"value": -12.0, "passed": 1},
                                   "peak_gain": {"value": 6.5, "passed": 1},
                                   "ripple": {"value": 1.5, "passed": 1}}
        self.codes = 0
        self.submissions = 0

    def execute_code(self, code, *, pid=None, port=None):
        self.codes += 1
        if not self.healthy:
            raise RuntimeError("backend_error: GetObjectsInGroup")
        return {"executed": True,
                "result": {"validated": True, "objects": ["disc", "disc2"]},
                "stdout": "", "stderr": ""}

    def submit(self, **kwargs):
        self.submissions += 1
        return {"run_id": f"run-{self.submissions}", "status": "QUEUED"}

    def status(self, campaign_dir, run_id):
        return {"run_id": run_id, "status": "COMPLETED", "terminal": True,
                "metrics": self.metrics}


def _runner(bridge, tmp):
    case = build_case()
    applier = ModelApplier(lens28_builder.build_model(), bridge,
                           project_path=str(Path(tmp) / "p.aedt"), design_name="D", pid=1)
    config = GuardConfig(campaign_dir=str(Path(tmp) / "camp"), project_name="p",
                         design_name="D", setup_name="FF_Swp", pid=1,
                         poll_interval_s=0.01, timeout_s=2.0)
    probe = (lambda: bridge.execute_code("result = {'ok': True}"))
    return CampaignRunner(case, applier, config, campaign_dir=Path(tmp) / "camp",
                          bridge=bridge, health_probe=probe)


class EnvironmentGuardTests(unittest.TestCase):
    def test_unhealthy_environment_stops_the_campaign(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmp:
            bridge = FakeBridge(healthy=False)
            runner = _runner(bridge, tmp)
            with self.assertRaises(EnvironmentUnhealthy):
                runner.evaluate(PARAMS)

    def test_healthy_environment_records_the_run(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmp:
            bridge = FakeBridge()
            runner = _runner(bridge, tmp)
            record = runner.evaluate(PARAMS)
            self.assertEqual(record.status, "COMPLETED")
            self.assertEqual(bridge.submissions, 1)


if __name__ == "__main__":
    unittest.main()

