"""Physics-guided local search tests with a linear fake bridge (no AEDT)."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from aster.apply import ModelApplier
from aster.campaign import CampaignRunner
from aster.cases import lens28_builder
from aster.cases.lens28 import build_case
from aster.executor import GuardConfig
from aster.strategy_loop import run_local_search

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")


class LinearBridge:
    """Measured metrics are linear in the applied parameters."""

    def __init__(self, base_s11=-14.0, base_gain=6.5):
        self.base_s11 = base_s11
        self.base_gain = base_gain
        self.submissions = []
        self.codes = []

    def execute_code(self, code, *, pid=None, port=None):
        self.codes.append(code)
        return {"executed": True, "result": {"validated": True, "objects": ["disc"]},
                "stdout": "", "stderr": ""}

    def submit(self, **kwargs):
        self.submissions.append(kwargs)
        return {"run_id": f"run-{len(self.submissions)}", "status": "QUEUED"}

    def status(self, campaign_dir, run_id):
        params = self.submissions[-1]["params"]
        step_L = float(params["step_L"])
        step_ro = float(params["step_ro"])
        gain = self.base_gain + 3.0 * (step_L - 2.0) - 2.0 * (step_ro - 0.8)
        s11 = self.base_s11 + 4.0 * (step_L - 2.0)
        return {"run_id": run_id, "status": "COMPLETED", "terminal": True,
                "metrics": {"s11": {"value": s11, "passed": 1},
                            "peak_gain": {"value": gain, "passed": 1},
                            "ripple": {"value": 1.0, "passed": 1}}}


def _runner(bridge, tmp):
    case = build_case()
    applier = ModelApplier(lens28_builder.build_model(), bridge,
                           project_path=str(Path(tmp) / "p.aedt"), design_name="D", pid=1)
    config = GuardConfig(campaign_dir=str(Path(tmp) / "camp"), project_name="p",
                         design_name="D", setup_name="FF_Swp", pid=1,
                         poll_interval_s=0.01, timeout_s=2.0)
    return CampaignRunner(case, applier, config, campaign_dir=Path(tmp) / "camp",
                          bridge=bridge, reference=(0.0, -30.0))


BASE = {"step_L": 2.0, "step_ro": 0.8, "wedge": 1, "cone_dz": 0.0}


class LocalSearchTests(unittest.TestCase):
    def test_search_improves_gain_while_protecting_s11(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmp:
            bridge = LinearBridge()
            runner = _runner(bridge, tmp)
            runner.evaluate(BASE)
            result = run_local_search(runner, target_kind="peak_gain",
                                      desired_change=1.0, protected={"s11": 1.5},
                                      budget=40, max_iterations=3, step_fraction=0.1)
            self.assertTrue(result.steps)
            self.assertTrue(any(step.improved for step in result.steps))
            self.assertNotEqual(result.best_params, BASE)
            # protection respected on the kept candidate
            kept = next(record for record in runner.records
                        if record.params == result.best_params)
            self.assertLessEqual(abs(kept.metrics["s11"] - -14.0), 1.5 + 1e-6)

    def test_search_stops_when_proposal_rejected(self):
        class FlatBridge(LinearBridge):
            def status(self, campaign_dir, run_id):
                params = self.submissions[-1]["params"]
                return {"run_id": run_id, "status": "COMPLETED", "terminal": True,
                        "metrics": {"s11": {"value": -14.0, "passed": 1},
                                    "peak_gain": {"value": 4.0, "passed": 1},
                                    "ripple": {"value": 1.0, "passed": 1}}}

        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmp:
            runner = _runner(FlatBridge(), tmp)
            runner.evaluate(BASE)
            result = run_local_search(runner, target_kind="peak_gain",
                                      desired_change=1.0, protected={"s11": 0.5},
                                      budget=20, max_iterations=2)
            self.assertTrue(result.steps)
            self.assertFalse(any(step.improved for step in result.steps))

    def test_budget_is_respected(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmp:
            runner = _runner(LinearBridge(), tmp)
            runner.evaluate(BASE)
            result = run_local_search(runner, target_kind="peak_gain",
                                      desired_change=0.5, protected={"s11": 5.0}, budget=2)
            self.assertLessEqual(len(result.steps), 2)
            self.assertGreaterEqual(result.evaluations, 1)

    def test_search_requires_a_measurement(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmp:
            runner = _runner(LinearBridge(), tmp)
            with self.assertRaises(ValueError):
                run_local_search(runner, target_kind="peak_gain", desired_change=1.0)


if __name__ == "__main__":
    unittest.main()





