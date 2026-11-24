import json
from pathlib import Path
import sqlite3

import pytest

from aster.approved_builder import ApprovedBuilderExecutor, run_approved_campaign
from aster.opt_workflow import OptimizationPlan, WorkflowError, WorkflowGateError, WorkflowRunner
from aster.workflow_evidence import attach_run
from test_design_history import campaign  # fixture creates both guarded ledgers
from test_opt_workflow import _case, _config, _FakeBridge


def test_replay_from_both_ledgers_and_reopen_after_delivery(campaign):
    root, case, job, folder = campaign
    project = root / "base.aedt"
    project.write_bytes(b"test fixture")
    class Completed:
        def run_approved_builder(self, **kwargs):
            return {"metrics": {"s11_bandwidth": 5, "peak_gain": 7, "ripple": 2}, "run_id": "screen-1"}

    config = _config(root / "workflow", project)
    plan = OptimizationPlan(case_id=case.case_id,
        strategy={"screening": "lhs", "optimizer": "none", "verification": "full_model"},
        verification_profile={"recipe_id": job["telemetry"]["learning_contract"]["identity"],
                              "band": {"band_ghz": [24, 25], "max_gap_ghz": 1}})
    runner = WorkflowRunner(case, config, plan, bridge=Completed())
    runner.record_baseline("guard-1", {"campaign_dir": str(root)})
    runner.run_screening(candidates=[case.family.param_space.default()])
    assert runner.record_acceptance({"campaign_dir": str(root), "run_id": "guard-1"})["passed"]
    report = root / "results.txt"
    report.write_text("test delivery")
    runner.record_delivery(str(report))
    reopened = WorkflowRunner(case, config, plan, bridge=Completed())
    assert reopened.gate()["passed"]
    (folder / "result.s1p").write_text("# GHZ S DB R 50\n24 -9 0\n25 -15 0\n")
    assert not reopened.gate()["passed"]


def test_attach_run_writes_formal_replay_ledger(campaign):
    root, case, job, _folder = campaign
    recipe = {"design_name": "Trunc_10GHz", "setup_name": "Setup1"}
    result = attach_run(
        root, case, guard_run_id="guard-attached", params={"x": 5.0},
        recipe=recipe, builder_sha256="a" * 64, run_id="attached-1",
        candidate_id="candidate-1",
    )
    lines = (root / "real_runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert lines
    attached = json.loads(lines[-1])
    assert attached["guard_run_id"] == "guard-attached"
    assert attached["params"] == {"x": 5.0}
    assert attached["telemetry"]["learning_contract"]["identity"] == result["contract"]["identity"]
    archive = json.loads(Path(result["archive"]).read_text(encoding="utf-8"))
    assert archive["entries"][0]["params"] == {"x": 5.0}
    assert archive["entries"][0]["status"] == "COMPLETED"


def test_search_gate_lives_on_disk_and_diagnostics_can_close(tmp_path):
    project = tmp_path / "base.aedt"
    project.touch()
    case, config = _case(project), _config(tmp_path, project)
    plan = OptimizationPlan(case_id=case.case_id, budget={"screening_candidates": 1, "optimizer_evaluations": 3})
    runner = WorkflowRunner(case, config, plan, bridge=_FakeBridge())
    runner.record_baseline("baseline")
    runner.run_screening(candidates=[{"x": 5}])
    runner.run_search(pop_size=4, generations=1)
    assert runner.gate()["checks"]["search_gate"]
    with pytest.raises(WorkflowGateError, match="positive acceptance"):
        runner.record_acceptance({"passed": True})
    runner.record_acceptance({"passed": False, "reason": "no full-model evidence"})
    report = tmp_path / "report.txt"
    report.write_text("no verified design")
    runner.record_delivery(str(report))
    outcome = runner.gate()
    assert outcome["workflow_complete"] and not outcome["passed"]
    with pytest.raises(WorkflowGateError, match="already run"):
        runner.run_screening(candidates=[{"x": 5}])
    with pytest.raises(WorkflowError, match="different plan"):
        WorkflowRunner(case, config, OptimizationPlan(case_id=case.case_id), bridge=_FakeBridge())


def test_pending_or_repeated_builder_attempt_does_not_inherit_observations(tmp_path):
    project = tmp_path / "base.aedt"
    project.touch()
    case, config = _case(project), _config(tmp_path, project)
    candidate = case.make_candidate({"x": 5})
    class Bridge:
        calls = 0
        def run_approved_builder(self, **args):
            self.calls += 1
            if self.calls == 1:
                return _FakeBridge().run_approved_builder(**args)
            if self.calls == 2:
                return {"status": "approval_required"}
            if self.calls == 3:
                return {"artifacts": []}
            raise TypeError("error after entering builder")
    bridge = Bridge()
    executor = ApprovedBuilderExecutor(case, config, bridge=bridge)
    assert [executor.evaluate(candidate).status for _ in range(4)] == [
        "COMPLETED", "APPROVAL_REQUIRED", "MEASUREMENT_MISSING", "MODEL_FAILED"]
    assert bridge.calls == 4


def test_direct_budget_and_shared_state(tmp_path):
    project = tmp_path / "base.aedt"
    project.touch()
    case, config = _case(project), _config(tmp_path, project)
    with pytest.raises(ValueError, match="budget"):
        run_approved_campaign(case, config, budget=0, candidates=[{"x": 5}], bridge=_FakeBridge())
    plan = OptimizationPlan(case_id=case.case_id)
    runner = WorkflowRunner(case, config, plan, bridge=_FakeBridge())
    runner.record_baseline("base")
    with sqlite3.connect(runner.state_path.with_suffix(".lock.sqlite")) as conn:
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(WorkflowGateError, match="lock"):
            runner.deviation("independent runner is active")
    state = json.loads(runner.state_path.read_text())
    state["in_flight"] = "screening"
    runner.state_path.write_text(json.dumps(state))
    with pytest.raises(WorkflowGateError, match="interrupted"):
        runner.run_screening()
