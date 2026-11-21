from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from aster.cases.lens28_ref import build_case
from aster.design_history import load_study_history
from aster.learning_contract import case_contract
from aster.pareto import archive_from_runs


@pytest.fixture
def campaign(tmp_path):
    case = build_case()
    params = case.family.param_space.default()
    contract = case_contract(case, measurement_recipe={"design_name": "D1", "setup_name": "S1"})
    job = {"case": case.case_id, "run_id": "real-1", "guard_run_id": "guard-1",
           "status": "COMPLETED", "params": params, "telemetry": {"learning_contract": contract},
           "feasible": True, "metrics": {"peak_gain": 999}, "total_seconds": 100}
    db = tmp_path / "campaign.sqlite"
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.executescript("""
            CREATE TABLE runs(run_id TEXT PRIMARY KEY, project TEXT, spec_version INTEGER,
                params_json TEXT, status TEXT, artifact_dir TEXT, created_utc TEXT,
                updated_utc TEXT, notes TEXT, cache_key TEXT);
            CREATE TABLE metrics(run_id TEXT,name TEXT,value REAL,unit TEXT,passed INTEGER,
                detail_json TEXT,PRIMARY KEY(run_id,name));
        """)
        conn.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?)",
                     ("guard-1", case.project, 1, json.dumps({**params, "project_name": "cand-A",
                      "design_name": "D1", "setup_name": "S1", "pid": 123}), "COMPLETED",
                      "D:/moved-campaign/runs/guard-1/artifacts", "2026-01-01T10:00:00Z",
                      "2026-01-01T11:00:00Z", None, None))
        conn.executemany("INSERT INTO metrics VALUES(?,?,?,?,?,?)",
                         [("guard-1", n, v, "", 1, None)
                          for n, v in (("peak_gain", 7), ("bandwidth", 5), ("ripple", 2))])
    folder = tmp_path / "runs" / "guard-1" / "artifacts"
    folder.mkdir(parents=True)
    (folder / "result.s1p").write_bytes(b"# GHz S DB R 50\n24 -12 0\n25 -11 0\n")
    log(tmp_path, job)
    return tmp_path, case, job, folder


def log(path, *events):
    (path / "real_runs.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events),
                                          encoding="utf-8")


def test_join_uses_guard_id_ledger_metrics_and_hash_after_move(campaign):
    root, case, job, folder = campaign
    db = root / "campaign.sqlite"
    before = db.read_bytes()
    row, = load_study_history(root, case)
    assert row["job_run_id"] == job["run_id"] and row["run_id"] == row["guard_run_id"] == "guard-1"
    assert row["source"] == "MEASURED" and row["feasible"]
    assert row["metrics"] == {"peak_gain": 7, "s11_bandwidth": 5, "ripple": 2}
    assert row["learning_contract"] == job["telemetry"]["learning_contract"]
    art = row["touchstone"]
    assert art["run_id"] == row["run_id"] and Path(art["path"]) == folder / "result.s1p"
    assert art["sha256"] == hashlib.sha256(Path(art["path"]).read_bytes()).hexdigest()
    assert db.read_bytes() == before


def test_native_snapshot_and_csv_must_identify_run_and_realized_quantity(campaign):
    root, case, _, folder = campaign
    meta = {"quantity": "dB(RealizedGainTotal)", "source": "MEASURED",
            "run_id": "guard-1", "coordinate_system": "Global", "samples": [[24, 90, 0, 5]]}
    (folder / "farfield_views.json").write_text(json.dumps({**meta, "quantity": "dB(GainTotal)"}))
    (folder / "wrong.json").write_text(json.dumps({**meta, "run_id": "another"}))
    (folder / "realized.json").write_text(json.dumps(meta))
    far = folder / "farfield"
    far.mkdir()
    csv = far / "coverage.csv"
    csv.write_bytes(b"Freq [GHz],Theta [deg],Phi [deg],dB(RealizedGainTotal) []\n25,90,0,6\n")
    sidecar = far / "coverage.csv.meta.json"
    sidecar.write_text(json.dumps({**meta, "sha256": hashlib.sha256(csv.read_bytes()).hexdigest()}))
    row, = load_study_history(root, case)
    assert sorted(s["frequency_ghz"] for s in row["coverage_snapshots"]) == [24, 25]
    csv.write_bytes(csv.read_bytes().replace(b"0,6", b"0,8"))
    (folder / "result.s1p").write_bytes(b"# GHz S DB R 50\n24 -22 0\n25 bad 0\n26 -22 0\n")
    row, = load_study_history(root, case)
    assert [s["frequency_ghz"] for s in row["coverage_snapshots"]] == [24]
    assert "touchstone" not in row and len(row["artifact_warnings"]) == 2


@pytest.mark.parametrize("sql", [
    "UPDATE metrics SET passed=0 WHERE name='bandwidth'",
    "UPDATE metrics SET passed=NULL WHERE name='bandwidth'",
    """UPDATE runs SET notes='[{"stage":"COMPLETED","feasible":false}]'""",
])
def test_failed_guard_acceptance_cannot_enter_front(campaign, sql):
    root, case, _, _ = campaign
    with closing(sqlite3.connect(root / "campaign.sqlite")) as conn, conn:
        conn.execute(sql)
    row, = load_study_history(root, case)
    assert row["status"] == "COMPLETED" and not row["feasible"]
    assert not archive_from_runs(case, [row]).feasible_front()


@pytest.mark.parametrize("changes", [
    {"source": "PREDICTED"}, {"is_prediction": True}, {"mode": "dry"}, {"run_id": "dry-x"},
    {"case": "wrong"}, {"telemetry": {}}, {"params": {}}, {"guard_run_id": "elsewhere"},
    {"guard_run_id": "../wrong"}, {"params": {"feed_radius": 0.4}},
])
def test_unidentified_wrong_case_or_dry_not_imported(campaign, changes):
    root, case, job, _ = campaign
    log(root, {**job, **changes})
    assert not load_study_history(root, case)


@pytest.mark.parametrize("change", [
    {"disc_radial": 1.1}, {"source": "predicted"}, {"design_name": "another"}, {"setup_name": "wrong"},
])
def test_guard_metadata_must_agree(campaign, change):
    root, case, _, _ = campaign
    with closing(sqlite3.connect(root / "campaign.sqlite")) as conn, conn:
        params = json.loads(conn.execute("SELECT params_json FROM runs").fetchone()[0])
        conn.execute("UPDATE runs SET params_json=?", (json.dumps({**params, **change}),))
    assert not load_study_history(root, case)


def test_duplicate_jobs_stages_partial_final_append_and_conflicting_recipe(campaign):
    root, case, job, _ = campaign
    job = {**job, "updated_utc": "2026-01-01T11:01:00Z"}
    cached = {**job, "run_id": "queued-alias", "cached": True, "total_seconds": 1}
    log(root, {**job, "status": "RUNNING", "stage": "SOLVING", "updated_utc": "2026-01-01T09:59:59Z"},
        job, job, cached)
    with (root / "real_runs.jsonl").open("ab") as stream:
        stream.write(b'{"run_id":')
    row, = load_study_history(root, case)
    assert row["total_seconds"] == 100 and row["job_run_id"] == job["run_id"]
    log(root, job, {**cached, "learning_contract":
                    {**job["telemetry"]["learning_contract"], "identity": "other-recipe"}})
    assert not load_study_history(root, case)


def test_bound_action_kind_and_cached_or_unverified_duration(campaign):
    root, case, job, _ = campaign
    telemetry = dict(job["telemetry"])
    telemetry["study_action"] = {"kind": "interaction", "id": "abc", "case_id": case.case_id,
                                 "recipe_id": telemetry["learning_contract"]["identity"]}
    job = {**job, "run_id": "study-abc", "telemetry": telemetry,
           "updated_utc": "2026-01-01T11:01:00Z"}
    log(root, job)
    row, = load_study_history(root, case)
    assert row["action_kind"] == "interaction" and "total_seconds" not in row
    log(root, {**job, "stage": "SOLVING", "updated_utc": "2026-01-01T09:59:59Z"}, job)
    row, = load_study_history(root, case)
    assert row["action_kind"] == "interaction" and row["total_seconds"] == 100
    late = {**job, "updated_utc": "2026-01-02T11:00:00Z"}
    log(root, {**late, "stage": "SOLVING", "updated_utc": "2026-01-02T10:59:59Z"}, late)
    row, = load_study_history(root, case)
    assert row["cache_hit"] and "total_seconds" not in row


def test_failed_status_and_missing_guard_results_supply_only_workflow_diagnostics(campaign):
    root, case, job, _ = campaign
    with closing(sqlite3.connect(root / "campaign.sqlite")) as conn, conn:
        conn.execute("UPDATE runs SET status='FAILED'")
    row, = load_study_history(root, case)
    assert row["status"] == "FAILED" and not ({"metrics", "touchstone", "coverage_snapshots"} & row.keys())
    (root / "campaign.sqlite").unlink()
    failure = {**job, "status": "FAILED", "guard_run_id": None, "error_code": "TIMEOUT",
               "run_id": "real-failed"}
    log(root, job, failure)
    row, = load_study_history(root, case)
    assert row["evidence_kind"] == "workflow_outcome_only" and row["error_code"] == "TIMEOUT"
    assert "metrics" not in row and not (root / "campaign.sqlite").exists()
    (root / "real_runs.jsonl").unlink()
    assert load_study_history(root, case) == []
