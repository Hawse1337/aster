"""Multi-machine coordination: one scheduler, leased tasks, results merged by identity.

Design constraints, and why:

* **Only the coordinator writes the SQLite.** Sharing one database file over SMB is how a network
  hiccup corrupts a campaign; here workers only ever talk HTTP, and each keeps its own local
  campaign directory and project copy.
* **A task is leased, not handed out.** A worker that dies mid-run simply stops heartbeating; the
  lease expires and the task is offered again. That is the same idea HTCondor uses (yadof's
  ``distributed`` mode relies on it) without requiring a cluster scheduler.
* **Results are merged by identity**, not by arrival order: ``(config_hash, params)`` names a design,
  so a duplicate submission (retry, double finish, two workers racing an expired lease) is idempotent
  and cannot inflate the campaign.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

__all__ = ["Task", "DistributedScheduler", "config_hash"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS dist_tasks (
    task_id TEXT PRIMARY KEY,
    case_name TEXT NOT NULL,
    params TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    state TEXT NOT NULL,
    lease_owner TEXT,
    lease_until REAL,
    attempts INTEGER NOT NULL DEFAULT 0,
    result TEXT,
    error TEXT,
    created_utc TEXT NOT NULL,
    updated_utc TEXT NOT NULL,
    UNIQUE (config_hash, params)
);
CREATE INDEX IF NOT EXISTS dist_state ON dist_tasks (state, lease_until);
"""


def config_hash(case_name: str, params: dict[str, Any]) -> str:
    """Stable identity of a design inside a campaign: same case, same values, same hash."""
    payload = json.dumps({"case": case_name, "params": params}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _now() -> float:
    return time.time()


def _utc(stamp: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp if stamp is not None else _now()))


@dataclass
class Task:
    task_id: str
    case: str
    params: dict[str, Any]
    config_hash: str
    state: str
    attempts: int = 0
    lease_owner: str | None = None
    lease_until: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    created_utc: str = ""
    updated_utc: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "case": self.case, "params": dict(self.params),
                "config_hash": self.config_hash, "state": self.state, "attempts": self.attempts,
                "lease_owner": self.lease_owner,
                "lease_seconds_left": (max(0.0, round(self.lease_until - _now(), 1))
                                       if self.lease_until else None),
                "result": self.result, "error": self.error,
                "created_utc": self.created_utc, "updated_utc": self.updated_utc}


class DistributedScheduler:
    """Owns the task table. Thread-safe; every operation is one short transaction."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._session() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15.0)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _session(self):
        with self._lock:
            connection = self._connect()
            try:
                yield connection
                connection.commit()
            finally:
                connection.close()

    # -- enqueue / claim ------------------------------------------------
    def enqueue(self, case_name: str, variants: Sequence[dict[str, Any]]) -> dict[str, Any]:
        """Add candidates; a design already known (same hash) is not added twice."""
        added: list[str] = []
        duplicates: list[str] = []
        with self._session() as conn:
            for params in variants:
                identity = config_hash(case_name, params)
                task_id = "task-%s" % identity
                existing = conn.execute("SELECT task_id FROM dist_tasks WHERE task_id = ?",
                                        (task_id,)).fetchone()
                if existing is not None:
                    duplicates.append(task_id)
                    continue
                conn.execute(
                    "INSERT INTO dist_tasks (task_id, case_name, params, config_hash, state, "
                    "created_utc, updated_utc) VALUES (?, ?, ?, ?, 'QUEUED', ?, ?)",
                    (task_id, case_name, json.dumps(params, sort_keys=True), identity,
                     _utc(), _utc()))
                added.append(task_id)
        return {"added": added, "duplicates": duplicates, "claimed": None}

    def claim(self, worker: str, lease_seconds: float = 600.0) -> dict[str, Any] | None:
        """Take one queued task (or an expired lease) for ``lease_seconds``."""
        with self._session() as conn:
            now = _now()
            row = conn.execute(
                "SELECT * FROM dist_tasks WHERE state = 'QUEUED' "
                "OR (state = 'LEASED' AND lease_until < ?) ORDER BY created_utc LIMIT 1",
                (now,)).fetchone()
            if row is None:
                return None
            task_id = row["task_id"]
            updated = conn.execute(
                "UPDATE dist_tasks SET state = 'LEASED', lease_owner = ?, lease_until = ?, "
                "attempts = attempts + 1, updated_utc = ? "
                "WHERE task_id = ? AND (state = 'QUEUED' OR (state = 'LEASED' AND lease_until < ?))",
                (worker, now + float(lease_seconds), _utc(), task_id, now))
            if updated.rowcount != 1:
                return None
            fresh = conn.execute("SELECT * FROM dist_tasks WHERE task_id = ?", (task_id,)).fetchone()
        return self._task(fresh) if fresh is not None else None

    def heartbeat(self, task_id: str, worker: str, extend_seconds: float = 600.0) -> bool:
        with self._session() as conn:
            result = conn.execute(
                "UPDATE dist_tasks SET lease_until = ?, updated_utc = ? "
                "WHERE task_id = ? AND state = 'LEASED' AND lease_owner = ?",
                (_now() + float(extend_seconds), _utc(), task_id, worker))
        return result.rowcount == 1

    def finish(self, task_id: str, worker: str, *, result: dict[str, Any] | None = None,
               error: str | None = None) -> dict[str, Any]:
        """Record an outcome. Idempotent: finishing a finished task reports the stored state."""
        with self._session() as conn:
            row = conn.execute("SELECT * FROM dist_tasks WHERE task_id = ?", (task_id,)).fetchone()
            if row is None:
                return {"ok": False, "error": "UNKNOWN_TASK", "detail": task_id}
            if row["state"] in {"DONE", "FAILED"}:
                return {"ok": True, "duplicate": True, "state": row["state"],
                        "task_id": task_id}
            if row["lease_owner"] not in (None, worker):
                return {"ok": False, "error": "LEASE_MISMATCH",
                        "detail": "task is leased to %s" % row["lease_owner"]}
            state = "FAILED" if error else "DONE"
            conn.execute(
                "UPDATE dist_tasks SET state = ?, result = ?, error = ?, lease_owner = NULL, "
                "lease_until = NULL, updated_utc = ? WHERE task_id = ?",
                (state, json.dumps(result or {}), error, _utc(), task_id))
        return {"ok": True, "duplicate": False, "state": state, "task_id": task_id}

    def release(self, task_id: str, worker: str) -> bool:
        """Give a leased task back without an outcome (worker is shutting down cleanly)."""
        with self._session() as conn:
            result = conn.execute(
                "UPDATE dist_tasks SET state = 'QUEUED', lease_owner = NULL, lease_until = NULL, "
                "updated_utc = ? WHERE task_id = ? AND state = 'LEASED' AND lease_owner = ?",
                (_utc(), task_id, worker))
        return result.rowcount == 1

    def reclaim_expired(self) -> int:
        with self._session() as conn:
            result = conn.execute(
                "UPDATE dist_tasks SET state = 'QUEUED', lease_owner = NULL, lease_until = NULL, "
                "updated_utc = ? WHERE state = 'LEASED' AND lease_until < ?", (_utc(), _now()))
        return result.rowcount

    # -- reads ----------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self._session() as conn:
            rows = [self._task(row) for row in conn.execute(
                "SELECT * FROM dist_tasks ORDER BY created_utc")]
        counts: dict[str, int] = {}
        for task in rows:
            counts[task.state] = counts.get(task.state, 0) + 1
        return {"tasks": [task.to_dict() for task in rows], "by_state": counts,
                "total": len(rows)}

    def results_by_config(self) -> dict[str, list[dict[str, Any]]]:
        """Finished results grouped by config hash: what a merge on the coordinator reads."""
        merged: dict[str, list[dict[str, Any]]] = {}
        for task in self.snapshot()["tasks"]:
            if task["state"] != "DONE":
                continue
            merged.setdefault(task["config_hash"], []).append(task)
        return merged

    @staticmethod
    def _task(row: sqlite3.Row) -> Task:
        return Task(task_id=row["task_id"], case=row["case_name"],
                    params=json.loads(row["params"] or "{}"),
                    config_hash=row["config_hash"], state=row["state"],
                    attempts=row["attempts"], lease_owner=row["lease_owner"],
                    lease_until=row["lease_until"],
                    result=json.loads(row["result"]) if row["result"] else None,
                    error=row["error"], created_utc=row["created_utc"],
                    updated_utc=row["updated_utc"])
