"""Campaign runner tests with fake bridges (no AEDT)."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from antopt.campaign import CampaignRunner, batch_params
from antopt.cases import lens28_builder
from antopt.cases.lens28 import build_case
from antopt.executor import GuardConfig

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")

PARAMS = {"step_L": 2.0, "step_ro": 0.8, "wedge": 1, "cone_dz": 0.0}


class FakeBridge:
    def __init__(self, metrics=None, run_status="COMPLETED", apply_payload=None):
        self.metrics = metrics if metrics is not None else {
            "s11": {"value": -12.0, "passed": 1},
            "peak_gain": {"value": 6.5, "passed": 1},
            "ripple": {"value": 1.4, "passed": 1},
        }
        self.run_status = run_status
        self.apply_payload = apply_payload or {
            "validated": True, "objects": ["disc", "step_out"], "operations": [],
        }
        self.codes: list[str] = []
        self.submissions: list[dict] = []

    # model application
    def execute_code(self, code, *, pid=None, port=None):
        self.codes.append(code)
        return {"executed": True, "result": self.apply_payload, "stdout": "", "stderr": ""}

    # guarded run
    def submit(self, **kwargs):
        self.submissions.append(kwargs)
        return {"run_id": f"run-{len(self.submissions)}", "status": "QUEUED"}

    def status(self, campaign_dir, run_id):
        return {"run_id": run_id, "status": self.run_status,
                "metrics": self.metrics, "terminal": True}


def _runner(bridge, tmp, prepare=True):
    case = build_case()
    model = lens28_builder.build_model()
    from antopt.apply import ModelApplier

    applier = ModelApplier(model, bridge, project_path=str(Path(tmp) / "p.aedt"),
                           design_name="D", pid=1)
    config = GuardConfig(campaign_dir=str(Path(tmp) / "camp"), project_name="p",
                         design_name="D", setup_name="FF_Swp", pid=1,
                         poll_interval_s=0.01, timeout_s=2.0)
    return CampaignRunner(case, applier, config, campaign_dir=Path(tmp) / "camp",
                          bridge=bridge, reference=(0.0, -30.0))


class CampaignRunnerTests(unittest.TestCase):
    def test_evaluate_applies_then_runs_and_archives(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmp:
            bridge = FakeBridge()
            runner = _runner(bridge, tmp)
            record = runner.evaluate(PARAMS)
            self.assertEqual(record.status, "COMPLETED")
            self.assertTrue(record.feasible)
            self.assertTrue(record.applied and record.validated)
            self.assertEqual(record.run_id, "run-1")
            self.assertEqual(len(bridge.codes), 1)          # parameters applied once
            self.assertEqual(len(bridge.submissions), 1)    # one guarded run
            self.assertEqual(runner.summary()["feasible"], 1)
            self.assertIsNotNone(runner.hypervolumes)

    def test_invalid_parameters_are_rejected_before_submission(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmp:
            bridge = FakeBridge()
            runner = _runner(bridge, tmp)
            record = runner.evaluate({"step_L": 2.0, "step_ro": 1.19, "wedge": 1, "cone_dz": 0.0})
            self.assertIn(record.status, {"MODEL_FAILED"})
            self.assertEqual(bridge.codes, [])           # never reached AEDT
            self.assertEqual(bridge.submissions, [])

    def test_bridge_apply_failure_is_recorded(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmp:
            bridge = FakeBridge(apply_payload={"validated": False, "objects": []})
            runner = _runner(bridge, tmp)
            record = runner.evaluate(PARAMS)
            self.assertEqual(record.status, "MODEL_FAILED")
            self.assertIn("model application failed", record.error)
            self.assertEqual(bridge.submissions, [])

    def test_decision_and_topology_signal_are_explainable(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmp:
            failing = {"s11": {"value": -8.0, "passed": 0},
                       "peak_gain": {"value": 2.0, "passed": 0},
                       "ripple": {"value": 1.0, "passed": 1}}
            bridge = FakeBridge(metrics=failing)
            runner = _runner(bridge, tmp)
            for _ in range(3):
                runner.evaluate(PARAMS)
            decision = runner.decide(stalled_rounds=4)
            self.assertIn("strategy", decision)
            self.assertTrue(decision["reasons"])
            signal = runner.topology_signal()
            self.assertIn("reasons", signal)
            self.assertIn("s11", signal.get("persistent_constraints") or [])

    def test_unmeasured_run_is_not_reported_as_satisfied(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmp:
            bridge = FakeBridge(metrics={}, run_status="FAILED")
            runner = _runner(bridge, tmp)
            runner.evaluate(PARAMS)
            decision = runner.decide()
            self.assertIn("failing", " ".join(decision["reasons"]).lower())
            self.assertNotIn("all hard constraints satisfied", " ".join(decision["reasons"]))

    def test_summary_persists_to_disk(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmp:
            bridge = FakeBridge()
            runner = _runner(bridge, tmp)
            runner.evaluate(PARAMS)
            target = runner.save()
            self.assertTrue(target.is_file())
            self.assertIn("records", target.read_text(encoding="utf-8"))

    def test_campaign_executor_can_feed_nsga2(self):
        from antopt.campaign import CampaignExecutor
        from antopt.optimizer.nsga2 import run_nsga2

        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmp:
            bridge = FakeBridge()
            runner = _runner(bridge, tmp)
            executor = CampaignExecutor(runner)
            result = run_nsga2(runner.case, executor, pop_size=4, generations=1,
                               seed=0, budget=6)
            self.assertLessEqual(result["evaluations"], 6)
            self.assertGreaterEqual(executor.calls, 1)
            self.assertEqual(len(runner.records), executor.calls)

    def test_batch_params_respect_bounds(self):
        case = build_case()
        batch = batch_params(case, 5, seed=3)
        self.assertEqual(len(batch), 5)
        for params in batch:
            self.assertEqual(set(params), {"step_L", "step_ro", "wedge", "cone_dz"})


if __name__ == "__main__":
    unittest.main()

class CampaignSensitivityTests(unittest.TestCase):
    def test_sensitivity_recovers_derivatives_from_campaign_runs(self):
        class LinearBridge(FakeBridge):
            def status(self, campaign_dir, run_id):
                params = self.submissions[-1]["params"]
                gain = 2.0 + 4.0 * float(params["step_L"]) - 1.0 * float(params["step_ro"])
                s11 = -20.0 + 3.0 * float(params["step_L"])
                return {"run_id": run_id, "status": "COMPLETED", "terminal": True,
                        "metrics": {"s11": {"value": s11, "passed": 1},
                                    "peak_gain": {"value": gain, "passed": 1},
                                    "ripple": {"value": 1.0, "passed": 1}}}

        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmp:
            bridge = LinearBridge()
            runner = _runner(bridge, tmp)
            matrix = runner.sensitivity(metric_kinds=["peak_gain", "s11"],
                                        step_fraction=0.05)
            self.assertAlmostEqual(matrix.raw["peak_gain"]["step_L"], 4.0, places=4)
            self.assertAlmostEqual(matrix.raw["peak_gain"]["step_ro"], -1.0, places=4)
            self.assertAlmostEqual(matrix.raw["s11"]["step_L"], 3.0, places=4)
            self.assertGreaterEqual(len(runner.records), 3)
            self.assertEqual(matrix.evaluations, len(runner.records))

    def test_sensitivity_counts_only_new_records_under_hard_budget(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmp:
            runner = _runner(FakeBridge(), tmp)
            runner.evaluate(PARAMS)
            before = len(runner.records)
            matrix = runner.sensitivity(metric_kinds=["peak_gain"], budget=2)
            self.assertEqual(matrix.evaluations, 2)
            self.assertEqual(len(runner.records) - before, 2)
            self.assertFalse(matrix.complete)

    def test_sensitivity_uses_shared_measurement_gate(self):
        from antopt.physics.sensitivity import BudgetedEvaluator, EvaluationBudgetExceeded

        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmp:
            runner = _runner(FakeBridge(), tmp)
            gate = BudgetedEvaluator(runner.case, runner.evaluate, 3)
            gate(PARAMS)
            before = gate.calls
            matrix = runner.sensitivity(metric_kinds=["peak_gain"], evaluator=gate)
            self.assertEqual(matrix.evaluations, gate.calls - before)
            self.assertEqual(gate.calls, 3)
            self.assertFalse(matrix.complete)
            with self.assertRaises(EvaluationBudgetExceeded):
                gate({**PARAMS, "step_L": 3.5})

    def test_measure_reuses_recorded_candidate(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmp:
            bridge = FakeBridge()
            runner = _runner(bridge, tmp)
            first = runner.measure(PARAMS)
            second = runner.measure(PARAMS)
            self.assertEqual(first.candidate_id, second.candidate_id)
            self.assertEqual(len(bridge.submissions), 1)

    def test_measure_retries_failed_record_instead_of_reusing_it(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmp:
            bridge = FakeBridge(run_status="FAILED")
            runner = _runner(bridge, tmp)
            first = runner.measure(PARAMS)
            self.assertEqual(first.status, "FAILED")
            bridge.run_status = "COMPLETED"
            second = runner.measure(PARAMS)
            self.assertEqual(second.status, "COMPLETED")
            self.assertEqual(len(bridge.submissions), 2)











