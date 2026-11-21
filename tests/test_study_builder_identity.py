"""Replay approved-builder identity checks using the real local queue and a recording guard."""
import pytest

from aster.cases import lens2g4_10g
from aster.design_history import load_study_history
from aster.design_study import DesignStudy
from aster.study_queue import enqueue_geometry_actions
from aster.worker import run_once

from test_approved_model_workflow import RecordingGuard, make_manager
from test_design_history import campaign, log


EXPECTED = "ab" * 32


def build_plan(tmp_path, guard, *, pinned=True):
    manager = make_manager(tmp_path, guard)
    case = lens2g4_10g.build_case()
    contract = manager.contract_for(case.case_id)
    profile = {
        "case_id": case.case_id, "profile_id": "builder-pin", "revision": 1,
        "recipe_id": contract["identity"], "band": {"band_ghz": [5, 18], "max_gap_ghz": 1},
        "cost_seconds": {"geometry": 10},
    }
    if pinned:
        profile["builder_sha256"] = EXPECTED
    study = DesignStudy(case, lens2g4_10g.build_model(), profile)
    plan = study.plan([], budget_seconds=10, count=1, seed=2)
    candidate, = plan["actions"]
    baseline = dict(candidate["params"])
    baseline["R_KNEE"] = 20.4 if baseline["R_KNEE"] != 20.4 else 21.2
    manager.store_instance.save_job({
        "run_id": "recipe-reference", "case": case.case_id, "status": "COMPLETED",
        "params": baseline, "telemetry": {"learning_contract": contract},
    })
    identity = {k: plan[k] for k in ("case_id", "recipe_id", "profile_sha256")}
    return manager, plan, identity


@pytest.mark.parametrize(("returned", "pinned", "solved"), [
    (EXPECTED.upper(), True, True),
    ("cd" * 32, True, False),
    (None, True, False),
    ("invalid", True, False),
    (None, False, True),
])
def test_queue_to_submit_and_persisted_diagnostics(tmp_path, monkeypatch, returned, pinned, solved):
    guard = RecordingGuard()
    guard.pending = False
    build = guard.run_approved_builder

    def respond(**kwargs):
        result = build(**kwargs)
        if returned is None:
            result.pop("script_sha256")
        else:
            result["script_sha256"] = returned
        return result

    monkeypatch.setattr(guard, "run_approved_builder", respond)
    manager, plan, identity = build_plan(tmp_path, guard, pinned=pinned)
    result = enqueue_geometry_actions(tmp_path, plan, **identity)
    new, = result["enqueued"]
    store = manager.store_instance
    run_id = new["run_id"]
    queued = next(r for r in store.jobs() if r["run_id"] == run_id)
    assert queued["telemetry"]["study_action"].get("builder_sha256") == (EXPECTED if pinned else None)
    assert run_once(manager, store, "test-worker") == run_id
    final = next(r for r in store.jobs() if r["run_id"] == run_id)
    assert bool(guard.solved) == solved
    if solved:
        assert final["status"] == "COMPLETED"
        assert final["telemetry"]["builder_result"]["script_sha256"] == returned
    else:
        assert [label for label, _ in guard.calls] == ["builder"]
        assert final["status"] == "FAILED" and final["guard_run_id"] is None
        assert final["error_code"] == "BUILDER_SHA256_MISMATCH"
        assert EXPECTED in final["error_detail"]
        assert final["telemetry"]["builder_identity_check"] == {
            "expected_sha256": EXPECTED, "actual_sha256": returned if returned == "cd" * 32 else None,
            "submitted": False,
        }


def test_mutated_plan_digest_cannot_queue_prior_action_id(tmp_path):
    manager, plan, identity = build_plan(tmp_path, RecordingGuard())
    for wrong, error in (("xy", "builder_sha256"), ("ef" * 32, "action ID"), (None, "action ID")):
        with pytest.raises(ValueError, match=error):
            enqueue_geometry_actions(tmp_path, {**plan, "builder_sha256": wrong}, **identity)
    assert [j["run_id"] for j in manager.store_instance.jobs()] == ["recipe-reference"]


def test_history_never_attributes_cached_script_or_conflicting_claims(campaign):
    root, case, job, _ = campaign

    def tagged(rid, digest, **fields):
        telemetry = {**job["telemetry"], "builder_result": {"script_sha256": digest},
                     "study_action": {"builder_sha256": "ff" * 32}}
        return {**job, "run_id": rid, "telemetry": telemetry, **fields}

    original = tagged("original", EXPECTED.upper(), cache_hit=False)
    cached = tagged("replay", "cc" * 32, cache_hit=True)
    log(root, original, cached)
    row, = load_study_history(root, case)
    assert row["builder_sha256"] == EXPECTED
    for only in (cached, tagged("missing", None, cache_hit=False),
                 tagged("invalid", "ab" * 31, cache_hit=False), tagged("unverified", EXPECTED)):
        log(root, only)
        row, = load_study_history(root, case)
        assert "builder_sha256" not in row
    log(root, original, tagged("different-origin", "11" * 32, cache_hit=False))
    assert load_study_history(root, case) == []

    # The guard result predates this job's SOLVING stage although the old
    # worker omitted its cache flag. Its builder cannot identify the older solve.
    later = tagged("later", "de" * 32, updated_utc="2026-01-02T11:00:00Z")
    start = {**later, "stage": "SOLVING", "updated_utc": "2026-01-02T10:59:59Z"}
    log(root, start, later)
    row, = load_study_history(root, case)
    assert row["cache_hit"] and "builder_sha256" not in row
    log(root, original, start, later)
    row, = load_study_history(root, case)
    assert row["job_run_id"] == "original" and row["builder_sha256"] == EXPECTED
