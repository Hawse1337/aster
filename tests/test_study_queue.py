"""Study actions retain an exact approved-builder recipe in the real SQLite queue."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from aster.cases import lens28_var
from aster.design_study import _digest
from aster.runtime.runner import RealRunManager
from aster.runtime.store import JobStore
from aster.study_queue import enqueue_geometry_actions
from aster.worker import run_once


def setup_campaign(tmp_path):
    source = tmp_path / "template.aedt"
    source.write_text("unit-test stub", encoding="utf-8")
    manager = RealRunManager(tmp_path, project_source=source, approved_builder="unit-test",
                             case_name="lens28_var")
    store = JobStore(tmp_path / "queue.sqlite")
    contract = manager.contract_for("lens28_var")
    params = lens28_var.build_space().default()
    store.save_job({"run_id": "baseline", "case": "lens28_var", "status": "COMPLETED",
                    "params": params, "telemetry": {"learning_contract": contract}})
    return manager, store, contract, params


def make_plan(contract, params):
    profile = "f" * 64
    def action(kind, p, frequency=None):
        geometry = kind in {"geometry", "interaction", "tolerance"}
        key = {"profile": profile, "task": "geometry" if geometry else kind,
               "params": p, "frequency_ghz": frequency, "run_id": None if geometry else "baseline"}
        return {"kind": kind, "params": p, "frequency_ghz": frequency,
                "run_id": "baseline" if kind != "geometry" else None, "id": _digest(key)}
    identity = {"case_id": "lens28_var", "recipe_id": contract["identity"], "profile_sha256": profile}
    plan = {**identity, "actions": [action("geometry", dict(params, ring_outer=2.3)),
                                    action("interaction", dict(params, ring_outer=2.4)),
                                    action("frequency", params, 28), action("coverage", params, 26),
                                    action("tolerance", params)]}
    return identity, plan


def test_concurrent_enqueue_claim_and_replay_after_terminal_failure(tmp_path, monkeypatch):
    manager, store, contract, params = setup_campaign(tmp_path)
    identity, plan = make_plan(contract, params)
    def enqueue(_):
        return enqueue_geometry_actions(tmp_path, plan, **identity)
    with ThreadPoolExecutor(max_workers=3) as pool:
        replies = list(pool.map(enqueue, range(3)))
    created = [row["run_id"] for reply in replies for row in reply["enqueued"]]
    assert len(created) == len(set(created)) == 2
    assert {row["kind"] for row in replies[0]["skipped"]} >= {"frequency", "coverage", "tolerance"}
    def check_only(job):
        manager._bind_contract(job)
        assert job.telemetry["study_action"]["recipe_id"] == contract["identity"]
        job.status = "FAILED"
        store.save_job(job.to_dict())
    # Exercise claim_next and reconstruction in the standard approved-builder worker.
    monkeypatch.setattr(manager, "_run_job", check_only)
    assert run_once(manager, store, "unit") in created
    assert enqueue(None)["enqueued"] == []
    assert {row["run_id"] for row in store.jobs()} == {"baseline", *created}


def test_bad_ids_and_recipe_do_not_partially_enqueue(tmp_path):
    manager, store, contract, params = setup_campaign(tmp_path)
    identity, plan = make_plan(contract, params)
    plan["actions"][1]["params"]["ring_outer"] = 2.5
    with pytest.raises(ValueError, match="action ID"):
        enqueue_geometry_actions(tmp_path, plan, **identity)
    assert len(store.jobs()) == 1
    with pytest.raises(ValueError, match="recipe_id"):
        enqueue_geometry_actions(tmp_path, plan, **{**identity, "recipe_id": "wrong"})
    identity, plan = make_plan(contract, params)
    result = enqueue_geometry_actions(tmp_path, {**plan, "recipe_id": "missing"},
                                      **{**identity, "recipe_id": "missing"})
    assert not result["enqueued"] and any("no matching" in r["reason"] for r in result["skipped"])
    assert len(store.jobs()) == 1


def test_unbound_recipe_and_unavailable_campaign(tmp_path):
    manager, store, contract, params = setup_campaign(tmp_path)
    store.save_job({"run_id": "baseline", "case": "lens28_var", "status": "COMPLETED", "params": params,
                    "telemetry": {"learning_contract": {**contract, "measurement_recipe_binding": "execution"}}})
    identity, plan = make_plan(contract, params)
    for root in (tmp_path, tmp_path / "empty"):
        assert not enqueue_geometry_actions(root, plan, **identity)["enqueued"]
    assert not (tmp_path / "empty").exists()


@pytest.mark.parametrize("wrong_mode", ["supervised", "other_builder", "other_template"])
def test_wrong_worker_skips_bound_study_and_can_take_later_ordinary_job(tmp_path, monkeypatch, wrong_mode):
    manager, store, contract, params = setup_campaign(tmp_path)
    identity, plan = make_plan(contract, params)
    plan["actions"] = plan["actions"][:1]
    created = enqueue_geometry_actions(tmp_path, plan, **identity)["enqueued"][0]["run_id"]
    ordinary, = store.enqueue([dict(params, ring_outer=2.8)], "lens28_var", "unbound")
    other = tmp_path / "different.aedt"
    other.write_bytes(b"a different geometry")
    wrong = RealRunManager(tmp_path, case_name=manager.case_name,
                           project_source=other if wrong_mode == "other_template" else manager.project_source,
                           approved_builder={"supervised": None, "other_builder": "other",
                                             "other_template": manager.approved_builder}[wrong_mode])
    received = []
    def record(job):
        received.append(job.run_id)
        job.status = "COMPLETED"
        store.save_job(job.to_dict())
    monkeypatch.setattr(wrong, "_run_job", record)
    assert run_once(wrong, store, "wrong") == ordinary
    assert run_once(wrong, store, "wrong") is None
    assert {r["run_id"]: r["status"] for r in store.jobs()}[created] == "QUEUED"
    monkeypatch.setattr(manager, "_run_job", record)
    assert run_once(manager, store, "matched") == created
    assert received == [ordinary, created]


def test_approval_retry_also_needs_its_bound_study_recipe(tmp_path, monkeypatch):
    manager, store, contract, params = setup_campaign(tmp_path)
    identity, plan = make_plan(contract, params)
    created = enqueue_geometry_actions(tmp_path, plan, **identity)["enqueued"][0]["run_id"]
    row = next(r for r in store.jobs() if r["run_id"] == created)
    store.save_job({**row, "status": "APPROVAL_REQUIRED"})
    wrong = RealRunManager(tmp_path, case_name=manager.case_name, project_source=manager.project_source,
                           approved_builder="other")
    assert run_once(wrong, store, "wrong", retry_approval=created) is None
    assert next(r["status"] for r in store.jobs() if r["run_id"] == created) == "APPROVAL_REQUIRED"
    monkeypatch.setattr(manager, "_run_job", lambda job: manager._bind_contract(job))
    assert run_once(manager, store, "matched", retry_approval=created) == created


def test_campaign_plan_and_enqueue_refill_slots_without_learning_from_queue(tmp_path, capsys):
    import argparse
    import json
    from aster.design_study import cli, run_cli
    _, store, contract, _ = setup_campaign(tmp_path)
    profile = {"case_id": "lens28_var", "profile_id": "queue-fixture", "revision": 1,
               "recipe_id": contract["identity"], "band": {"band_ghz": [26, 30], "max_gap_ghz": 1},
               "objectives": ["band_margin_db"], "cost_seconds": {"geometry": 20}}
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile), encoding="utf-8")
    parser = argparse.ArgumentParser()
    cli(parser)
    options = ["--campaign", str(tmp_path), "--profile", str(path), "--count", "2",
               "--seed", "7", "--budget-seconds", "40"]

    def plan(enqueue=False):
        run_cli(parser.parse_args(options + (["--enqueue-geometry"] if enqueue else [])))
        result = json.loads(capsys.readouterr().out)
        assert len(result["actions"]) == 2
        assert result["trained_on"] == 0 and result["assessments"] == [] and result["nominal_front"] == []
        if enqueue:
            assert len(result["queue"]["enqueued"]) == 2
        return result["actions"]

    first = plan(enqueue=True)
    jobs = {r["run_id"]: r for r in store.jobs()}
    for action, status in zip(first, ("APPROVAL_REQUIRED", "RUNNING")):
        row = jobs["study-" + action["id"]]
        store.save_job({**row, "status": status, "metrics": {"peak_gain": 999}, "feasible": True})
    db = tmp_path / "queue.sqlite"
    original = db.read_bytes()
    alternate = plan()
    assert plan() == alternate and db.read_bytes() == original
    assert alternate == plan(enqueue=True)
    assert {a["id"] for a in first}.isdisjoint(a["id"] for a in alternate)
    assert all(a["params"] != b["params"] for a in first for b in alternate)


def test_queue_reservations_require_recipe_and_handle_older_schema(tmp_path):
    from contextlib import closing
    import sqlite3
    from aster.design_history import read_study_reservations
    _, store, contract, params = setup_campaign(tmp_path)
    case = lens28_var.build_case()
    study = {"case": case.case_id, "params": params, "status": "QUEUED"}
    for suffix, binding in (("ordinary", contract), ("wrong", {**contract, "identity": "unrelated"}),
                            ("unbound", {**contract, "measurement_recipe_binding": "execution"})):
        store.save_job({**study, "run_id": suffix, "telemetry": {"learning_contract": binding}})
    rows = read_study_reservations(tmp_path, case, contract["identity"])
    assert {r["run_id"] for r in rows} == {"ordinary", "baseline"}
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    assert not read_study_reservations(legacy, case, contract["identity"])
    db = legacy / "queue.sqlite"
    assert not db.exists()
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("CREATE TABLE jobs(run_id TEXT PRIMARY KEY, case_name TEXT, params TEXT)")
    original = db.read_bytes()
    assert not read_study_reservations(legacy, case, contract["identity"])
    assert db.read_bytes() == original
