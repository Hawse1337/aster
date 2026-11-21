"""Persist jobs and batches so a restart does not lose the queue.

The manager kept its state in memory plus an append-only ``real_runs.jsonl``. That is enough to
read history, but not enough to answer "what was I running when the app closed, and can I continue
it?" - which is the first thing a person asks after closing the window by accident.

This store is a small SQLite database in the campaign directory. It records each job's last known
state and each batch's membership, so :meth:`JobStore.unfinished` can tell the page what is worth
resuming and with which parameters.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterable

__all__ = ["JobStore", "finished_states"]

finished_states = ("COMPLETED", "FAILED", "CANCELLED", "APPROVAL_REQUIRED")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    run_id TEXT PRIMARY KEY,
    batch_id TEXT,
    case_name TEXT,
    params TEXT,
    status TEXT,
    stage TEXT,
    metrics TEXT,
    violations TEXT,
    feasible INTEGER,
    error_code TEXT,
    error_detail TEXT,
    failure TEXT,
    guard_run_id TEXT,
    timings TEXT,
    telemetry TEXT,
    total_seconds REAL,
    started_utc TEXT,
    updated_utc TEXT
);
CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY,
    case_name TEXT,
    state TEXT,
    total INTEGER,
    done INTEGER,
    run_ids TEXT,
    last_error TEXT,
    created_utc TEXT,
    updated_utc TEXT
);
CREATE INDEX IF NOT EXISTS jobs_batch ON jobs(batch_id);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class JobStore:
    """SQLite-backed record of what has run and what is still owed."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._session() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _session(self):
        """Commit on success and, crucially, close: Windows will not delete an open database."""
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    # -- jobs ------------------------------------------------------------
    def save_job(self, payload: dict[str, Any], batch_id: str | None = None) -> None:
        row = {
            "run_id": payload.get("run_id"),
            "batch_id": batch_id if batch_id is not None else payload.get("batch_id"),
            "case_name": payload.get("case"),
            "params": _dumps(payload.get("params") or {}),
            "status": payload.get("status"),
            "stage": payload.get("stage"),
            "metrics": _dumps(payload.get("metrics") or {}),
            "violations": _dumps(payload.get("violations") or {}),
            "feasible": 1 if payload.get("feasible") else 0,
            "error_code": payload.get("error_code"),
            "error_detail": payload.get("error_detail") or "",
            "failure": _dumps(payload.get("failure") or {}),
            "guard_run_id": payload.get("guard_run_id"),
            "timings": _dumps(payload.get("timings") or {}),
            "telemetry": _dumps(payload.get("telemetry") or {}),
            "total_seconds": float(payload.get("total_seconds") or 0.0),
            "started_utc": payload.get("started_utc") or _now(),
            "updated_utc": _now(),
        }
        if not row["run_id"]:
            return
        with self._session() as conn:
            conn.execute(
                "INSERT INTO jobs (run_id, batch_id, case_name, params, status, stage, "
                "metrics, violations, feasible, error_code, error_detail, failure, "
                "guard_run_id, timings, telemetry, total_seconds, started_utc, updated_utc) "
                "VALUES (:run_id, :batch_id, :case_name, :params, :status, :stage, "
                ":metrics, :violations, :feasible, :error_code, :error_detail, :failure, "
                ":guard_run_id, :timings, :telemetry, :total_seconds, :started_utc, "
                ":updated_utc) "
                "ON CONFLICT(run_id) DO UPDATE SET batch_id=COALESCE(excluded.batch_id, jobs.batch_id), "
                "case_name=:case_name, params=:params, status=:status, stage=:stage, "
                "metrics=:metrics, violations=:violations, feasible=:feasible, "
                "error_code=:error_code, error_detail=:error_detail, failure=:failure, "
                "guard_run_id=:guard_run_id, timings=:timings, telemetry=:telemetry, "
                "total_seconds=:total_seconds, updated_utc=:updated_utc",
                row)

    def jobs(self, batch_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM jobs"
        args: tuple[Any, ...] = ()
        if batch_id is not None:
            query += " WHERE batch_id = ?"
            args = (batch_id,)
        query += " ORDER BY started_utc DESC"
        with self._session() as conn:
            rows = [self._job_row(row) for row in conn.execute(query, args)]
        return rows

    # -- queue (control plane writes, worker claims) ----------------------
    def enqueue(self, params_list: list[dict[str, float]], case_name: str,
                batch_id: str, learning_contract: dict[str, Any] | None = None, *,
                study_actions: list[dict[str, Any]] | None = None) -> list[str]:
        """Write candidates as QUEUED rows; returns the run ids that were created."""
        import time as _time

        if study_actions is not None and len(study_actions) != len(params_list):
            raise ValueError("each parameter point requires its study action")
        run_ids: list[str] = []
        stamp = _time.strftime("%Y%m%dT%H%M%S", _time.gmtime())
        def key(params):
            return tuple(sorted((name, round(float(value), 9)
                                 if isinstance(value, (int, float)) else str(value))
                                for name, value in params.items()))
        with self._session() as conn:
            # Reserve a batch atomically, including concurrent double-clicks/workers.
            conn.execute("BEGIN IMMEDIATE")
            occupied = set()
            if learning_contract:
                from ..learning_contract import compatibility, stored_contract
                for record in conn.execute("SELECT * FROM jobs WHERE case_name = ?",
                                           (case_name,)):
                    row = self._job_row(record)
                    status = row.get("status")
                    if status not in {"COMPLETED", "QUEUED", "RUNNING", "RECOVERY_REQUIRED", "APPROVAL_REQUIRED"}:
                        continue
                    matches, _ = compatibility(row, learning_contract)
                    if status == "APPROVAL_REQUIRED" and learning_contract.get("measurement_recipe_binding") == "execution":
                        # A queued request from a dry GUI acquires the worker's exact recipe on claim.
                        contract = stored_contract(row)
                        matches = all(contract.get(k) == learning_contract[k]
                                      for k in ("case_id", "family_id", "structure_hash"))
                    if matches:
                        occupied.add(key(row["params"]))
            else:
                for row in conn.execute("SELECT params FROM jobs WHERE case_name=? "
                                        "AND status='APPROVAL_REQUIRED'", (case_name,)):
                    occupied.add(key(_loads(row["params"], {})))
            for index, params in enumerate(params_list):
                identity = key(params)
                if identity in occupied:
                    continue
                occupied.add(identity)
                import uuid
                telemetry = {"learning_contract": learning_contract} if learning_contract else {}
                if study_actions is None:
                    run_id = "queued-%s-%02d-%s" % (stamp, index, uuid.uuid4().hex[:8])
                else:
                    action = study_actions[index]
                    run_id = "study-" + action["id"]
                    if conn.execute("SELECT 1 FROM jobs WHERE run_id=?", (run_id,)).fetchone():
                        continue  # A failed or cancelled previous attempt is still the same action.
                    telemetry["study_action"] = action
                index_conn = conn.execute(
                    "INSERT INTO jobs (run_id, batch_id, case_name, params, status, stage, "
                    "metrics, violations, feasible, error_code, error_detail, failure, "
                    "guard_run_id, timings, telemetry, total_seconds, started_utc, updated_utc) VALUES "
                    "(?, ?, ?, ?, 'QUEUED', 'QUEUED', '{}', '{}', 0, NULL, '', '{}', NULL, "
                    "'{}', ?, 0.0, ?, ?)",
                    (run_id, batch_id, case_name, _dumps(params),
                     _dumps(telemetry),
                     _now(), _now()))
                if index_conn.rowcount:
                    run_ids.append(run_id)
        return run_ids

    def claim_next(self, worker: str, *, case_name: str | None = None,
                   study_contract_for: Callable[[str], dict] | None = None) -> dict[str, Any] | None:
        """Take the oldest eligible queued job; a study job needs its exact approved recipe."""
        return self._claim(worker, "QUEUED", case_name, None, study_contract_for)

    def claim_approval_retry(self, run_id: str, case_name: str, worker: str, *,
                             study_contract_for: Callable[[str], dict] | None = None) -> dict | None:
        """Claim one paused job with the same checks as its initial claim."""
        return self._claim(worker, "APPROVAL_REQUIRED", case_name, run_id, study_contract_for)

    def _claim(self, worker, status, case_name, run_id, study_contract_for):
        from ..learning_contract import compatibility

        with self._session() as conn:
            candidates = conn.execute(
                "SELECT run_id, case_name, telemetry FROM jobs WHERE status=? "
                "AND (? IS NULL OR case_name=?) AND (? IS NULL OR run_id=?) "
                "ORDER BY started_utc, rowid",
                (status, case_name, case_name, run_id, run_id)).fetchall()
            # Hash the worker's template only if an actual study job needs it.
            # No write transaction is held while reading its potentially large file.
            expected_by_case = {}
            for candidate in candidates:
                telemetry = _loads(candidate["telemetry"], {})
                if "study_action" in telemetry:
                    action = telemetry["study_action"]
                    bound = telemetry.get("learning_contract")
                    name = candidate["case_name"]
                    if not (study_contract_for and isinstance(action, dict) and isinstance(bound, dict)
                            and action.get("case_id") == name and action.get("recipe_id")
                            and action["recipe_id"] == bound.get("identity")
                            and bound.get("measurement_recipe_binding") != "execution"):
                        continue
                    if name not in expected_by_case:
                        expected_by_case[name] = study_contract_for(name)
                    expected = expected_by_case[name]
                    record = {"case": name, "learning_contract": bound}
                    if (not expected.get("measurement_recipe", {}).get("builder")
                            or bound.get("measurement_recipe") != expected.get("measurement_recipe")
                            or not compatibility(record, expected)[0]
                            or bound.get("parameter_schema") != expected.get("parameter_schema")):
                        continue
                # Compare the row snapshot as well as its state in the atomic
                # UPDATE: a competing claim or changed binding requires a fresh read.
                updated = conn.execute(
                    "UPDATE jobs SET status='RUNNING', stage='CLAIMED', updated_utc=?, error_detail=? "
                    "WHERE run_id=? AND status=? AND case_name IS ? AND telemetry IS ?",
                    (_now(), "worker:%s" % worker, candidate["run_id"], status,
                     candidate["case_name"], candidate["telemetry"]))
                if updated.rowcount == 1:
                    row = conn.execute("SELECT * FROM jobs WHERE run_id=?",
                                       (candidate["run_id"],)).fetchone()
                    return self._job_row(row)
                conn.rollback()  # Release the no-op write before testing another candidate.
        return None

    def requeue_claim(self, run_id: str) -> bool:
        """Put a claimed-but-abandoned row back in the queue (worker crashed before starting)."""
        with self._session() as conn:
            result = conn.execute(
                "UPDATE jobs SET status = 'QUEUED', stage = 'QUEUED', updated_utc = ? "
                "WHERE run_id = ? AND status = 'RUNNING' AND stage = 'CLAIMED'",
                (_now(), run_id))
        return result.rowcount == 1

    def queued(self) -> list[dict[str, Any]]:
        with self._session() as conn:
            rows = [self._job_row(row) for row in conn.execute(
                "SELECT * FROM jobs WHERE status = 'QUEUED' ORDER BY started_utc")]
        return rows

    def unfinished(self) -> list[dict[str, Any]]:
        """Jobs that never reached a terminal state - the ones a restart should offer to finish."""
        with self._session() as conn:
            rows = [self._job_row(row) for row in conn.execute(
                "SELECT * FROM jobs WHERE status NOT IN (%s) ORDER BY started_utc"
                % ",".join("?" * len(finished_states)), finished_states)]
        return rows

    @staticmethod
    def _job_row(row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        for key, default in (("params", {}), ("metrics", {}), ("violations", {}),
                             ("failure", {}), ("timings", {}), ("telemetry", {})):
            payload[key] = _loads(payload.get(key), default)
        payload["feasible"] = bool(payload.get("feasible"))
        payload["case"] = payload.get("case_name")
        return payload

    # -- batches ---------------------------------------------------------
    def save_batch(self, payload: dict[str, Any]) -> None:
        batch_id = payload.get("batch_id")
        if not batch_id:
            return
        row = {
            "batch_id": batch_id,
            "case_name": payload.get("case"),
            "state": payload.get("state"),
            "total": int(payload.get("total") or 0),
            "done": int(payload.get("done") or 0),
            "run_ids": _dumps(payload.get("run_ids") or []),
            "last_error": payload.get("last_error") or "",
            "created_utc": payload.get("created_utc") or _now(),
            "updated_utc": _now(),
        }
        with self._session() as conn:
            conn.execute(
                "INSERT INTO batches (batch_id, case_name, state, total, done, run_ids, last_error, "
                "created_utc, updated_utc) VALUES (:batch_id, :case_name, :state, :total, :done, "
                ":run_ids, :last_error, :created_utc, :updated_utc) "
                "ON CONFLICT(batch_id) DO UPDATE SET case_name=:case_name, state=:state, total=:total, "
                "done=:done, run_ids=:run_ids, last_error=:last_error, updated_utc=:updated_utc",
                row)

    def batches(self) -> list[dict[str, Any]]:
        with self._session() as conn:
            rows = []
            for row in conn.execute("SELECT * FROM batches ORDER BY created_utc DESC"):
                payload = dict(row)
                payload["run_ids"] = _loads(payload.get("run_ids"), [])
                payload["case"] = payload.get("case_name")
                rows.append(payload)
        return rows

    def resumable(self) -> list[dict[str, Any]]:
        """Batches that still owe work: queued, running, or stopped with jobs left to do."""
        batches = []
        for batch in self.batches():
            if batch.get("state") in {"DONE", "CANCELLED"}:
                continue
            pending = [job for job in self.jobs(batch["batch_id"])
                       if job.get("status") not in finished_states]
            if pending or batch.get("state") in {"QUEUED", "RUNNING"}:
                batch["pending"] = pending
                batches.append(batch)
        return batches

    def variants_to_resume(self, batch_id: str) -> list[dict[str, float]]:
        """Parameters of the candidates in a batch that never finished."""
        return [dict(job.get("params") or {}) for job in self.jobs(batch_id)
                if job.get("status") not in finished_states]

    def close(self) -> None:
        return None


def summarise(batches: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Small payload for the page: how much work is waiting and where."""
    rows = list(batches)
    return {"count": len(rows),
            "batches": [{"batch_id": row.get("batch_id"), "case": row.get("case"),
                         "state": row.get("state"), "total": row.get("total"),
                         "done": row.get("done"),
                         "pending": len(row.get("pending") or [])} for row in rows]}
