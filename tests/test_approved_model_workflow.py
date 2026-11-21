"""Approval-aware execution across the persisted queue and guarded solver boundary."""
import json
from pathlib import Path
from uuid import uuid4

from aster import worker
from aster.cases import lens2g4_10g


class RecordingGuard:
    def __init__(self):
        self.pending = True
        self.calls = []
        self.request = {"builder_id": "p0_lens_dimensions", "design": "Trunc_10GHz"}
        self.solved = []
        self.artifact = None

    def connect(self, **kw):
        return {"connected": True}

    def run_approved_builder(self, **kw):
        self.calls.append(("builder", kw))
        if self.pending:
            return {"status": "approval_required", "approval_required": True,
                    "refused": True, "executed": False, "approval_mode": "once",
                    "request": self.request, "request_id": "review-1", "reason": "range"}
        folder = Path(kw["outdir"])
        folder.mkdir(parents=True, exist_ok=True)
        saved = self.artifact or folder / ("candidate_" + uuid4().hex + ".aedt")
        if self.artifact is None:
            saved.write_bytes(b"validated candidate")
        return {"executed": True, "script_sha256": "a" * 64, "authorization": "one_time",
                "result": {"validated": True, "saved": str(saved), "design": "Trunc_10GHz"}}

    def create_spec(self, campaign_dir, spec):
        self.calls.append(("spec", spec))
        assert spec["frequency_ghz"] == [5.0, 18.0]
        return {"spec_path": str(Path(campaign_dir) / "design_spec.json")}

    def submit(self, **kw):
        self.calls.append(("submit", kw))
        self.solved.append(kw)
        return {"run_id": "guard-1", "cached": False}

    def status(self, campaign, run_id):
        return {"status": "COMPLETED", "terminal": True, "metrics": {
            "s11": {"value": -12.0, "passed": True}, "peak_gain": {"value": 6.5, "passed": True},
            "ripple": {"value": 2.0, "passed": True}}}


def make_manager(tmp_path, guard):
    from types import SimpleNamespace

    baseline = tmp_path / "reference.aedt"
    baseline.write_bytes(b"immutable baseline")
    args = worker.build_parser().parse_args([
        "--real", "--once", "--campaign", str(tmp_path), "--case", "lens2g4_10g",
        "--approved-builder", "p0_lens_dimensions", "--pid", "123",
        "--project", str(baseline), "--builder-outdir", str(tmp_path / "approved"),
        "--design", "Trunc_10GHz", "--setup", "Setup1", "--sweep", "Sweep_5_18"])
    manager = worker._manager(args, SimpleNamespace(reference_project=None))
    manager.bridge_factory = lambda pid: guard
    return manager


def test_pending_request_survives_restart_and_only_approved_candidate_is_solved(tmp_path):
    guard = RecordingGuard()
    manager = make_manager(tmp_path, guard)
    store = manager.store_instance
    case = "lens2g4_10g"
    params = lens2g4_10g.build_model().space.default()
    [run_id] = store.enqueue([params], case, "b")
    assert manager.prepare_in_caller_thread(case, 123) is None
    assert worker.run_once(manager, store, "w") == run_id
    [row] = store.jobs()
    assert row["status"] == "APPROVAL_REQUIRED" and not row["guard_run_id"]
    assert row["batch_id"] == "b"
    pending = row["telemetry"]["builder_approval"]
    assert pending["submitted"] is False and pending["request"] == guard.request
    exported = json.loads(Path(pending["request_file"]).read_text(encoding="utf8"))
    assert exported == {"status": "approval_required", "approval_required": True,
                        "refused": True, "executed": False, "approval_mode": "once",
                        "request": guard.request, "request_id": "review-1", "reason": "range"}
    assert store.enqueue([params], case, "b2") == []
    queue_contract = {**manager.contract_for(case), "measurement_recipe_binding": "execution",
                      "recipe_hash": "deferred"}
    assert store.enqueue([params], case, "b2", learning_contract=queue_contract) == []
    assert worker.run_once(manager, store, "w") is None
    assert [call[0] for call in guard.calls] == ["builder"]
    assert worker.run_once(manager, store, "w", retry_approval=run_id) == run_id
    assert not guard.solved  # retrying is independent of granting approval

    guard.pending = False
    manager = make_manager(tmp_path, guard)
    assert worker.run_once(manager, store, "w", retry_approval=run_id) == run_id
    [row] = store.jobs()
    assert row["status"] == "COMPLETED" and row["feasible"]
    assert row["batch_id"] == "b"
    assert row["guard_run_id"] == "guard-1"
    assert [name for name, _ in guard.calls] == ["builder"] * 3 + ["spec", "submit"]
    calls = [kw for name, kw in guard.calls if name == "builder"]
    assert all(kw == calls[0] for kw in calls)
    assert calls[0]["params"] == {**params, "design": "Trunc_10GHz"}
    [solve] = guard.solved
    assert solve["project_name"] == row["telemetry"]["builder_result"]["saved"]
    assert Path(solve["project_name"]).parent == tmp_path / "approved"
    assert solve["setup_name"] == "Setup1" and solve["sweep_name"] == "Sweep_5_18"
    assert solve["farfield_setup_name"] is None
    assert solve["frequencies_ghz"] == list(range(5, 19))
    assert (tmp_path / "reference.aedt").read_bytes() == b"immutable baseline"
    assert worker.run_once(manager, store, "w", retry_approval=run_id) is None


def test_protocol_failure_cannot_submit_the_baseline(tmp_path):
    guard = RecordingGuard()
    guard.pending = False
    guard.artifact = tmp_path / "reference.aedt"
    manager = make_manager(tmp_path, guard)
    store = manager.store_instance
    [run_id] = store.enqueue([lens2g4_10g.build_model().space.default()], "lens2g4_10g", "b")
    assert worker.run_once(manager, store, "w") == run_id
    assert store.jobs()[0]["error_code"] == "MODEL_FAILED"
    assert [name for name, _ in guard.calls] == ["builder"]
