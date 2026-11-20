"""Headless batch worker: execute the persisted queue without the page.

    python -m antopt.worker --campaign DIR --case lens28_var --real --once
    python -m antopt.worker --campaign DIR --case lens28_var --real --watch 60

Why this exists: the page is a control surface, and running a twenty-five minute solve inside the
same process as an HTTP server means a crash, a mis-click or a restart costs the run. The worker
takes QUEUED rows from the same queue database, claims them atomically, runs them through the very
same ``RealRunManager`` (no second implementation of the solver discipline), and writes a file log so
a supervisor can restart it.

Exit codes: 0 when the queue drained cleanly, 1 when a job failed, 2 for a usage or configuration
problem.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import config
from .runtime.runner import RealJob, RealRunError, RealRunManager
from .runtime.store import JobStore
from .version import describe

__all__ = ["main", "run_once", "drain"]

FINISHED = {"COMPLETED", "FAILED"}


def _log(handle, message: str) -> None:
    line = "%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%S"), message)
    print(line, flush=True)
    if handle is not None:
        handle.write(line + "\n")
        handle.flush()


def _manager(args: argparse.Namespace, env: config.Environment) -> RealRunManager:
    # the worker runs without the GUI, so nothing has imported the case modules yet;
    # RealRunManager._prepare resolves the case by name and would fail with "unknown case"
    from .registry_bootstrap import ensure_registry  # noqa: PLC0415

    ensure_registry()
    project = env.reference_project
    if project is None or not Path(project).is_file():
        raise SystemExit("no reference project configured; run `AntennaOptimizer.exe --wizard`")
    campaign = Path(args.campaign)

    def ensure_aedt() -> bool:
        from .aedt_host import aedt_reachable, running_aedt, start_aedt
        from .stall import kill_solvers

        pids = running_aedt()
        if pids:
            usable = next((pid for pid in pids if aedt_reachable(pid)), None)
            if usable is not None:
                return True
            kill_solvers()
            time.sleep(5)
        return start_aedt(env.aedt_exe or Path("ansysedt.exe"), args.grpc,
                          non_graphical=bool(getattr(args, "aedt_ng", False)))

    manager = RealRunManager(
        campaign_dir=campaign,
        project_source=Path(project),
        case_name=args.case,
        reuse_project=True,
        ensure_aedt=ensure_aedt,
        solve_budget_s=float(args.solve_budget),
        status_stale_s=float(args.stall_seconds),
        passes=int(args.passes),
        sweep_step_ghz=float(getattr(args, "sweep_step", 1.0)),
        sweep_type=str(getattr(args, "sweep_type", "Discrete")),
        save_fields=bool(getattr(args, "save_fields", False)),
        verify_frequencies_ghz=config.parse_frequency_list(
            getattr(args, "verify_frequencies", "")),
    )
    return manager


def run_once(manager: RealRunManager, store: JobStore, worker: str, handle=None) -> str | None:
    """Claim and execute one queued row; returns the run id, or None when the queue is empty."""
    row = store.claim_next(worker)
    if row is None:
        return None
    run_id = str(row["run_id"])
    _log(handle, "claimed %s (case %s)" % (run_id, row.get("case") or row.get("case_name")))
    job = RealJob(run_id=run_id, case=str(row.get("case") or row.get("case_name")),
                  params=dict(row.get("params") or {}), started_utc=row.get("started_utc") or "",
                  updated_utc=row.get("updated_utc") or "",
                  telemetry=dict(row.get("telemetry") or {}))
    try:
        manager._run_job(job)
    except RealRunError as exc:  # the manager already persisted the translated failure
        _log(handle, "run %s failed: %s (%s)" % (run_id, exc.code, exc.detail))
        return run_id
    final = manager.status(run_id)
    status = getattr(final, "status", "UNKNOWN")
    _log(handle, "run %s finished: %s" % (run_id, status))
    return run_id


def drain(manager: RealRunManager, store: JobStore, worker: str, limit: int | None = None,
          handle=None) -> dict:
    """Run until the queue is empty; returns a small summary."""
    done = 0
    failed = 0
    while limit is None or done < limit:
        run_id = run_once(manager, store, worker, handle=handle)
        if run_id is None:
            break
        done += 1
        row = next((item for item in store.jobs() if item["run_id"] == run_id), {})
        if str(row.get("status") or "").upper() == "FAILED":
            failed += 1
    return {"done": done, "failed": failed}


def agent_main(args: argparse.Namespace, env: config.Environment) -> int:
    """Run as a worker node for a coordinator: claim over HTTP, execute locally, report back."""
    from .dist_agent import AgentReport, CoordinatorClient, run_agent_loop

    if not args.server:
        print("--agent needs --server URL", file=sys.stderr)
        return 2
    client = CoordinatorClient(args.server)
    manager = _manager(args, env)
    handle = open(args.log, "a", encoding="utf-8") if args.log else None

    def runner(case_name: str, params: dict) -> AgentReport:
        job = manager.start(case_name, params)
        deadline = time.monotonic() + float(args.solve_budget) * 1.5
        while time.monotonic() < deadline:
            current = manager.status(job.run_id)
            if current is not None and current.status in {"COMPLETED", "FAILED"}:
                return AgentReport(task_id="", run_id=job.run_id, status=current.status,
                                   metrics=dict(current.metrics or {}),
                                   params=dict(params),
                                   error=(current.error_detail or None
                                          if current.status == "FAILED" else None))
            time.sleep(1.0)
        return AgentReport(task_id="", run_id=job.run_id, status="FAILED", metrics={},
                           params=dict(params), error="agent gave up waiting for the local run")

    try:
        summary = run_agent_loop(client, args.worker_name or ("agent-%d" % id(manager)),
                                 runner, idle_seconds=float(args.poll_seconds),
                                 lease_seconds=float(args.lease_seconds),
                                 max_tasks=args.max_tasks,
                                 stop_after_idle=args.stop_after_idle)
        _log(handle, "agent finished: %s" % summary)
        return 0 if summary["failed"] == 0 else 1
    finally:
        if handle is not None:
            handle.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="antopt.worker",
                                     description="run the persisted queue without the page")
    parser.add_argument("--campaign", required=True, help="campaign directory")
    parser.add_argument("--case", default="lens28_var")
    parser.add_argument("--real", action="store_true", help="required; the worker is for real runs")
    parser.add_argument("--once", action="store_true", help="drain the queue and exit")
    parser.add_argument("--watch", type=float, default=0.0,
                        help="keep polling for new queued rows every N seconds")
    parser.add_argument("--limit", type=int, default=None, help="stop after N jobs")
    parser.add_argument("--passes", type=int, default=8)
    parser.add_argument("--solve-budget", type=float, default=2700.0)
    parser.add_argument("--stall-seconds", type=float, default=600.0)
    parser.add_argument("--sweep-step", type=float, default=1.0,
                        help="frequency step of the measurement sweep, GHz (default 1.0)")
    parser.add_argument("--sweep-type", choices=("Discrete", "Interpolating"), default="Discrete")
    parser.add_argument("--save-fields", action="store_true")
    parser.add_argument("--verify-frequencies", default="",
                        help="comma-separated GHz points measured again as discrete sweeps")
    parser.add_argument("--aedt-ng", action="store_true",
                        help="launch AEDT non-graphically (-ng)")
    parser.add_argument("--grpc", type=int, default=59390)
    parser.add_argument("--log", default=None, help="append the worker log to this file")
    parser.add_argument("--agent", action="store_true",
                        help="run as a node of a distributed campaign instead of the local queue")
    parser.add_argument("--server", default=None, help="coordinator base URL for --agent")
    parser.add_argument("--worker-name", default=None, help="name shown in the coordinator")
    parser.add_argument("--lease-seconds", type=float, default=1800.0)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--max-tasks", type=int, default=None)
    parser.add_argument("--stop-after-idle", type=int, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.real:
        parser.error("the worker is for real runs; pass --real")
    if args.agent and not args.server:
        parser.error("--agent needs --server URL")
    if not args.once and args.watch <= 0:
        parser.error("choose --once or --watch SECONDS")
    env = config.load()
    if not env.aedt_exe or not Path(env.aedt_exe).is_file():
        print("no AEDT configured; run `AntennaOptimizer.exe --wizard`", file=sys.stderr)
        return 2
    if args.agent:
        return agent_main(args, env)
    handle = open(args.log, "a", encoding="utf-8") if args.log else None
    try:
        store = JobStore(Path(args.campaign) / "queue.sqlite")
        _log(handle, "worker started: %s, queue %s" % (describe(), store.path))
        manager = _manager(args, env)
        worker = "worker-%d" % id(manager)
        total = {"done": 0, "failed": 0}
        if args.once:
            summary = drain(manager, store, worker, limit=args.limit, handle=handle)
            total["done"] += summary["done"]
            total["failed"] += summary["failed"]
        else:
            while True:
                summary = drain(manager, store, worker, limit=args.limit, handle=handle)
                total["done"] += summary["done"]
                total["failed"] += summary["failed"]
                if args.limit is not None and total["done"] >= args.limit:
                    break
                _log(handle, "queue empty; polling again in %.0f s" % args.watch)
                time.sleep(max(1.0, float(args.watch)))
        _log(handle, "worker finished: %s" % json.dumps(total))
        return 0 if total["failed"] == 0 else 1
    finally:
        if handle is not None:
            handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
