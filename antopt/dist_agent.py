"""Worker side of the distributed protocol: claim over HTTP, run locally, report back.

The agent never touches the coordinator's database. It keeps its own campaign directory (the
coordinator's ``config_hash`` names the design, so the same parameters are comparable), runs one task
through the local ``RealRunManager`` discipline, and posts the outcome. A crashed agent simply stops
heartbeating and the lease expires - see ``antopt/distributed.py``.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

__all__ = ["CoordinatorClient", "AgentReport", "run_agent_once", "run_agent_loop"]


@dataclass
class AgentReport:
    task_id: str
    run_id: str
    status: str
    metrics: dict[str, Any]
    params: dict[str, Any]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "run_id": self.run_id, "metrics": dict(self.metrics),
                "params": dict(self.params), "error": self.error}


class CoordinatorClient:
    """Minimal HTTP client; ``timeout`` keeps a frozen coordinator from hanging the agent."""

    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        request = urllib.request.Request(
            self.base_url + path, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")

    def _get(self, path: str) -> dict[str, Any]:
        with urllib.request.urlopen(self.base_url + path, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8") or "{}")

    def claim(self, worker: str, lease_seconds: float = 600.0) -> dict[str, Any] | None:
        _code, body = self._post("/api/dist/claim",
                                 {"worker": worker, "lease_seconds": lease_seconds})
        return body.get("task")

    def heartbeat(self, task_id: str, worker: str, lease_seconds: float = 600.0) -> bool:
        _code, body = self._post("/api/dist/heartbeat",
                                 {"task_id": task_id, "worker": worker,
                                  "lease_seconds": lease_seconds})
        return bool(body.get("extended"))

    def finish(self, task_id: str, worker: str, report: AgentReport) -> dict[str, Any]:
        payload = {"task_id": task_id, "worker": worker,
                   "result": report.to_dict(), "error": report.error}
        _code, body = self._post("/api/dist/finish", payload)
        return body

    def status(self) -> dict[str, Any]:
        return self._get("/api/dist/status")


def run_agent_once(client: CoordinatorClient, worker: str, runner: Callable[[str, dict[str, Any]],
                                                                           AgentReport],
                   lease_seconds: float = 600.0) -> AgentReport | None:
    """Claim one task and run it; returns None when the queue is empty."""
    task = client.claim(worker, lease_seconds=lease_seconds)
    if not task:
        return None
    case = str(task.get("case") or "lens28_ref")
    params = dict(task.get("params") or {})
    try:
        report = runner(case, params)
    except Exception as exc:  # noqa: BLE001 - a local failure is reported, not raised at the network
        report = AgentReport(task_id=str(task["task_id"]), run_id="", status="FAILED",
                             metrics={}, params=params,
                             error="%s: %s" % (type(exc).__name__, str(exc)[:300]))
    report.task_id = str(task["task_id"])
    client.finish(report.task_id, worker, report)
    return report


def run_agent_loop(client: CoordinatorClient, worker: str,
                   runner: Callable[[str, dict[str, Any]], AgentReport],
                   *, idle_seconds: float = 5.0, lease_seconds: float = 600.0,
                   max_tasks: int | None = None, stop_after_idle: int | None = None) -> dict[str, Any]:
    """Poll for tasks until ``max_tasks`` or ``stop_after_idle`` consecutive empty polls."""
    done = 0
    failed = 0
    idle = 0
    while max_tasks is None or done < max_tasks:
        report = run_agent_once(client, worker, runner, lease_seconds=lease_seconds)
        if report is None:
            idle += 1
            if stop_after_idle is not None and idle >= stop_after_idle:
                break
            if idle_seconds:
                time.sleep(idle_seconds)
            continue
        idle = 0
        done += 1
        if report.status != "COMPLETED":
            failed += 1
    return {"done": done, "failed": failed}
