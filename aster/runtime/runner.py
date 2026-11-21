"""Background real-run manager for the GUI (G2).

A real run takes tens of minutes, so the HTTP request must return immediately: the
manager starts a thread, the page polls ``/api/status`` and the manager writes the
finished record to ``real_runs.jsonl`` so the history survives a restart.

Every AEDT-specific step is injected (``pid_provider``, ``bridge_factory``,
``executor_factory``), so the lifecycle is unit-testable without a solver.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..failures import classify

STAGES = ("QUEUED", "CONNECTING", "MODEL", "SOLVING", "POSTPROCESS", "DONE")

ACF_PATH = Path(os.environ.get("ASTER_ACF_PATH", ""))
DEFAULT_BASELINE = {
    "tip_offset": 1.0, "disc_factor": 1.0, "disc_radial": 1.0, "ring_inner": 1.2,
    "ring_outer": 3.0, "ring_height": 0.6, "ring_shift": 0.0, "ring2_outer": 0.0,
    "ring2_height": 0.4, "ring2_shift": -0.4, "feed_radius": 0.4, "feed_len": 1.6,
    "feed_z0": -2.8,
}


class RealRunError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__("%s: %s" % (code, detail) if detail else code)
        self.code = code
        self.detail = detail


@dataclass
class RealJob:
    run_id: str
    case: str
    params: dict[str, float]
    status: str = "QUEUED"
    stage: str = "QUEUED"
    metrics: dict[str, float] = field(default_factory=dict)
    violations: dict[str, float] = field(default_factory=dict)
    feasible: bool = False
    error_code: str | None = None
    error_detail: str = ""
    failure: dict[str, Any] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    telemetry: dict[str, Any] = field(default_factory=dict)
    total_seconds: float = 0.0
    guard_run_id: str | None = None
    started_utc: str = ""
    updated_utc: str = ""
    farfield_warning: str = ""
    pattern_reports: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def default_pid_provider() -> int:
    """Return the single AEDT PID that exposes a gRPC server."""
    import psutil

    matches = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name != "ansysedt.exe":
                continue
            cmd = proc.info.get("cmdline") or []
            if "-grpcsrv" in cmd:
                matches.append(int(proc.info["pid"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if not matches:
        raise RealRunError("NO_AEDT", "no ansysedt.exe with -grpcsrv is running")
    if len(matches) > 1:
        raise RealRunError("AMBIGUOUS_AEDT", "several AEDT instances: %s" % matches)
    return matches[0]


@dataclass
class RealRunManager:
    campaign_dir: Path
    project_source: Path | None = None
    design_name: str = "f24g05"
    setup_name: str = "P4"
    sweep_name: str = "ANT_Band"
    band_ghz: tuple[float, float] = (24.0, 32.0)
    farfield_ghz: float = 28.0
    max_minutes: int = 50
    sweep_step_ghz: float = 1.0
    max_delta_s: float = 0.02
    solution_ghz: float | None = None
    """Adaptive mesh frequency. For a wide band the mesh must be adapted at the top of it."""
    passes: int | None = None
    pid_provider: Callable[[], int] = default_pid_provider
    bridge_factory: Callable[[int], Any] | None = None
    executor_factory: Callable[..., Any] | None = None
    jobs: dict[str, RealJob] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    threads: list[threading.Thread] = field(default_factory=list)
    case_name: str = "lens28_ref"
    aedt_version: str | None = None
    reuse_project: bool = False
    batches: dict[str, dict] = field(default_factory=dict)
    attempts: dict[str, list] = field(default_factory=dict)
    _telemetry: dict[str, Any] = field(default_factory=dict)
    store_instance: Any = None
    stall_watch_factory: Callable[[], Any] | None = None
    stall_monitor_factory: Callable[[Any], Any] | None = None
    stall_retries: int = 1
    recovery_grace_s: float = 10.0
    """How long to wait for a killed attempt to stop before RECOVERY_REQUIRED."""
    ensure_aedt: Callable[[], bool] | None = None
    """Called before each attempt: a stall kills AEDT, so the next job needs it back."""
    status_stale_s: float = 600.0
    stage_timeout_s: float = 900.0
    solve_poll_s: float = 1.0
    solve_budget_s: float = 2700.0
    """Hard wall-clock limit for one attempt.

    The last resort, and the only signal that survived the real failures: the guard reports SOLVING
    and never updates anything else, and the machine-wide CPU signal goes blind when other work keeps
    the box busy. A solve that exceeds this is killed, marked STALLED, and retried once.
    """
    resource_sampler: Callable[[int], Any] | None = None
    """Optional sampler(pid) -> ResourceSample; injected by tests, defaults to the real one."""
    sweep_step_ghz: float = 1.0
    """Frequency step of the measurement sweep: bandwidth resolution is bounded by it."""
    sweep_type: str = "Discrete"
    """Discrete (every point solved) or Interpolating (solver chooses the points)."""
    save_fields: bool = False
    """Save fields at each sweep point (needed for far fields away from the adaptive frequency)."""
    verify_frequencies_ghz: tuple[float, ...] = ()
    """Extra single-point discrete sweeps that cross-check an interpolating sweep at chosen frequencies."""
    bridge_kind: str = "auto"
    kill_on_stall: Callable[[], Any] | None = None
    keep_pattern_reports: bool = True
    """Rebuild and keep the 3D / E / H / EH-overlay reports in the solved project after a run.

    The pipeline still exports the four PNGs into the run artifacts; this additionally leaves the
    report objects in the campaign project copy (saved) so the user can open Results and replot.
    The cap-facing behaviour is unchanged: when the project is replaced by a later fresh copy the
    PNGs and persist_result.json remain the durable record.
    """
    pattern_reports_dir: Path | None = None
    """Optional root for the persisted PNG/report manifests (default: campaign/pattern_reports)."""

    approved_builder: str | None = None
    builder_outdir: Path | None = None

    def __post_init__(self) -> None:
        self.campaign_dir = Path(self.campaign_dir)
        # a fresh manager starts with no bridge, no sweep and no report
        self._shared_bridge = None
        self._sweep_done = False
        self._sweep_projects: set[str] = set()
        self.sweep_report = None
        if self.store_instance is None:
            from .store import JobStore

            self.store_instance = JobStore(self.campaign_dir / "queue.sqlite")

    # -------------------------------------------------------------- lifecycle
    def contract_for(self, case_name: str) -> dict[str, Any]:
        """Identity of the actual solver recipe, frozen before work is queued."""
        import hashlib
        from ..learning_contract import case_contract
        from ..parametric.registry import get_case

        from ..registry_bootstrap import ensure_registry
        ensure_registry()
        registration = get_case(case_name)
        source = Path(self.project_source) if self.project_source is not None else None
        recipe = {"frequency_ghz": list(self.band_ghz),
                  "farfield_frequencies_ghz": [self.farfield_ghz],
                  "design_name": self.design_name, "setup_name": self.setup_name,
                  "sweep_name": self.sweep_name, "sweep_step_ghz": self.sweep_step_ghz,
                  "sweep_type": self.sweep_type, "save_fields": self.save_fields,
                  "max_delta_s": self.max_delta_s, "passes": self.passes,
                  "solution_ghz": self.solution_ghz,
                  "actual_aedt_version": self.aedt_version or "unknown",
                  "project_source_hash": hashlib.sha256(source.read_bytes()).hexdigest()
                  if source is not None and source.is_file() else None}
        if self.approved_builder:
            spec = registration.build_case()
            recipe = {"builder": self.approved_builder, "project_source_hash": recipe["project_source_hash"],
                      "design": self.design_name, "setup": self.setup_name, "sweep": self.sweep_name,
                      "frequencies_ghz": spec.evaluation.get("frequencies_ghz"),
                      "aedt_version": self.aedt_version or "unknown"}
        return case_contract(registration.build_case(), registration.build_model(), recipe)

    def _bind_contract(self, job: RealJob) -> None:
        from ..learning_contract import compatibility, stored_contract

        expected = self.contract_for(job.case)
        existing = stored_contract(job.to_dict())
        if existing:
            if existing.get("measurement_recipe_binding") == "execution":
                ok = all(existing.get(key) == expected.get(key)
                         for key in ("case_id", "family_id", "structure_hash"))
                reason = "different_case_or_structure"
            else:
                ok, reason = compatibility(job.to_dict(), expected)
            if not ok:
                raise RealRunError("LEARNING_CONTRACT_MISMATCH", reason)
        job.telemetry["learning_contract"] = expected

    def start(self, case_name: str, params: dict[str, float]) -> RealJob:
        import uuid
        run_id = "real-" + time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
        job = RealJob(run_id=run_id, case=case_name, params=dict(params),
                      started_utc=_now(), updated_utc=_now())
        self._bind_contract(job)
        with self.lock:
            self.jobs[run_id] = job
        thread = threading.Thread(target=self._run_job, args=(job,), daemon=True)
        self.threads.append(thread)
        thread.start()
        return job

    def start_batch(self, case_name: str, variants: list[dict[str, float]],
                    batch_id: str | None = None) -> dict:
        """Queue several candidates in one session and run them one after another."""
        batch_id = batch_id or ("batch-" + time.strftime("%Y%m%dT%H%M%S", time.gmtime()))
        record = {"batch_id": batch_id, "case": case_name, "state": "QUEUED",
                  "total": len(variants), "done": 0, "run_ids": [],
                  "created_utc": _now()}
        # Freeze and persist every member before launch, so acquisition sees the
        # whole pending batch rather than only the candidate currently solving.
        import uuid
        jobs = []
        for params in variants:
            job = RealJob(run_id="real-" + time.strftime("%Y%m%dT%H%M%S", time.gmtime())
                          + "-" + uuid.uuid4().hex[:8], case=case_name,
                          params=dict(params), started_utc=_now(), updated_utc=_now())
            self._bind_contract(job)
            jobs.append(job)
            record["run_ids"].append(job.run_id)
        with self.lock:
            self.batches[batch_id] = record
            self.jobs.update({job.run_id: job for job in jobs})
        for job in jobs:
            self._persist(job)
        thread = threading.Thread(target=self._run_batch,
                                  args=(batch_id, case_name, variants), daemon=True)
        self.threads.append(thread)
        thread.start()
        return record

    def batch_status(self, batch_id: str) -> dict | None:
        with self.lock:
            record = self.batches.get(batch_id)
            return dict(record) if record else None

    def prepare_in_caller_thread(self, case_name: str, pid: int) -> dict[str, Any] | None:
        """Run the one-shot copy and sweep setup before any worker thread starts.

        The MCP server keeps its AEDT session state per event loop, and a snippet issued from a
        worker thread reached a different loop and had the guard try to start AEDT on port 0.
        Doing this part here keeps the session on the loop that owns it.
        """
        if self.approved_builder:
            return None  # The baseline already contains the analysis setup.
        if self.project_source is None:
            return None
        source = Path(self.project_source)
        if not source.is_file():
            return None
        projects = self.campaign_dir / "projects"
        projects.mkdir(parents=True, exist_ok=True)
        target = projects / ("%s.aedt" % case_name)
        if not target.is_file() or not self.reuse_project:
            import shutil

            shutil.copy2(source, target)
        bridge = self._bridge(pid, None)
        self._ensure_band_sweep(bridge, target)
        self._sweep_done = True
        self._sweep_projects.add(str(target.resolve()).casefold())
        return self.sweep_report

    def _run_batch(self, batch_id: str, case_name: str, variants: list) -> None:
        record = self.batches[batch_id]
        record["state"] = "RUNNING"
        for run_id in record["run_ids"]:
            job = self.jobs[run_id]
            self._run_job(job)
            with self.lock:
                record["done"] += 1
                if job.status == "FAILED":
                    record["last_error"] = job.error_code
        record["state"] = "DONE"

    def status(self, run_id: str) -> RealJob | None:
        with self.lock:
            job = self.jobs.get(run_id)
            if job is not None:
                return job
        for row in self.history():
            if row.get("run_id") == run_id:
                return RealJob(**row)
        return None

    def history(self) -> list[dict[str, Any]]:
        path = self.campaign_dir / "real_runs.jsonl"
        rows: list[dict[str, Any]] = []
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        # the ledger is an event log now (one row per stage change), so the newest row per run wins
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            latest[str(row.get("run_id"))] = row
        rows = list(latest.values())
        rows.sort(key=lambda item: item.get("started_utc", ""), reverse=True)
        return rows

    def s11(self, run_id: str) -> list[tuple[float, float]]:
        from ..touchstone import read_touchstone

        candidates = [run_id]
        job = self.status(run_id)
        if job is not None and getattr(job, "guard_run_id", None):
            candidates.insert(0, str(job.guard_run_id))
        for candidate in candidates:
            artifact = self.campaign_dir / "runs" / candidate / "artifacts" / "result.s1p"
            if artifact.is_file():
                return read_touchstone(artifact)
        return []

    def farfield(self, run_id: str, frequency_ghz: float | None = None) -> dict[str, Any]:
        from ..farfield import view

        job = self.status(run_id)
        guard_id = getattr(job, "guard_run_id", None) if job is not None else None
        result = view(self.campaign_dir, str(guard_id or run_id), frequency_ghz)
        result["run_id"] = run_id
        return result

    # ------------------------------------------------------------------ worker
    def _ensure_band_sweep(self, bridge: Any, target: Path) -> None:
        """Create the discrete band sweep on a freshly copied project, exactly once.

        The source project carries whatever sweep it happened to save, and the guard refuses an
        export that does not cover the band. Inserting the sweep on every candidate is what
        failed before (InsertFrequencySweep on an already-configured setup), so this runs only
        right after the copy.

        A failure is recorded on the manager instead of aborting: the guard's own incomplete
        export error is more informative than a shadow error from here.
        """
        from ..executor import setup_config_code

        code = setup_config_code(
            project_path=target,
            design_name=self.design_name,
            setup_name=self.setup_name,
            passes=int(self.passes or 8),
            max_delta_s=float(self.max_delta_s),
            band_ghz=self.band_ghz,
            sweep_name=self.sweep_name,
            step_ghz=float(self.sweep_step_ghz),
        )
        # AEDT occasionally refuses the first sweep insertion right after a project copy (the
        # same transient the guard retries elsewhere), so retry briefly before giving up
        payload: Any = None
        attempts: list[str] = []
        for attempt in range(3):
            try:
                info = bridge.execute_code(code,
                                          pid=self.pid_provider())
                payload = info.get("result") if isinstance(info, dict) else info
                attempts.append("ok")
                break
            except Exception as exc:  # noqa: BLE001 - retried, then recorded
                attempts.append(type(exc).__name__)
                payload = {"error": "%s: %s" % (type(exc).__name__, str(exc)[:300])}
                if attempt < 2:
                    import time as _time

                    _time.sleep(2.0)
        # only surface the attempts when something actually went wrong, so a clean run keeps
        # the plain {"sweeps": [...]} payload the rest of the code expects
        if isinstance(payload, dict) and attempts != ["ok"]:
            payload = dict(payload, attempts=attempts)
        with self.lock:
            self.sweep_report = payload

    def _update(self, job: RealJob, **changes: Any) -> None:
        """Apply changes, timing how long the job stays in each stage.

        Without this the only timings anyone had were wall-clock totals guessed from log files, which
        is why 'it took forty minutes' never said whether the solver or the export was responsible.
        """
        with self.lock:
            stage = changes.get("stage")
            if stage and stage != job.stage:
                now = time.monotonic()
                started = getattr(job, "_stage_started", None)
                if started is not None and job.stage in STAGES:
                    job.timings[job.stage] = round(now - started, 2)
                job._stage_started = now
            for key, value in changes.items():
                setattr(job, key, value)
            job.updated_utc = _now()
            stage_changed = bool(changes.get("stage"))
        if stage_changed:
            # a twenty-five minute solve must be visible while it runs, not only when it ends
            self._persist(job)

    def _run_job(self, job: RealJob) -> None:
        try:
            if self.approved_builder and job.case != self.case_name:
                raise RealRunError("BUILDER_CASE_MISMATCH", job.case)
            self._update(job, stage="CONNECTING", status="RUNNING")
            # a previous stall may have killed AEDT; bring it back before asking for a pid,
            # otherwise every later job in the batch fails instantly with NO_AEDT
            if self.ensure_aedt is not None and not self.approved_builder:
                try:
                    ready = bool(self.ensure_aedt())
                except Exception:  # noqa: BLE001 - treated as "not ready"
                    ready = False
                if not ready:
                    raise RealRunError("NO_AEDT", "AEDT is not running and could not be started")
            attempts = max(1, int(self.stall_retries) + 1)
            bridge = None
            pid = None
            entry = None
            for attempt_number in range(1, attempts + 1):
                attempt = self._begin_attempt(job, attempt_number)
                # every attempt gets its own pid, bridge, project copy and executor: reusing the
                # previous ones is how a retry ended up queued behind a blocked call. AEDT may have
                # been killed by the previous attempt, so bring it back before asking for a pid.
                if self.ensure_aedt is not None and not self.approved_builder:
                    try:
                        ready = bool(self.ensure_aedt())
                    except Exception:  # noqa: BLE001 - treated as "not ready"
                        ready = False
                    if not ready:
                        raise RealRunError("NO_AEDT",
                                           "AEDT is not running and could not be started")
                pid = self.pid_provider()
                attempt.owned_pid = int(pid)
                bridge = self._bridge(pid, job)
                attempt.bridge = bridge
                connect = getattr(bridge, "connect", None)
                if callable(connect):
                    self._connect_bridge(bridge, pid)
                self._update(job, stage="MODEL")
                project, executor = self._prepare(bridge, pid, job)
                attempt.executor = executor
                self._update(job, stage="SOLVING")
                if self.approved_builder:
                    # The guard owns the solver lifecycle, timeout and evidence on this session.
                    entry = executor.evaluate(self._candidate(job))
                    break
                outcome = self._evaluate_attempt(job, executor, attempt)
                if outcome.result is not None:
                    entry = outcome.result
                    break
                if outcome.terminal:
                    raise RealRunError("RECOVERY_REQUIRED", outcome.reason)
                self._update(job, stage="STALLED", status="RUNNING", error_code="STALLED",
                             error_detail=outcome.reason)
                if attempt_number < attempts:
                    self._update(job, stage="RETRYING", status="RUNNING")
                    continue
                raise RealRunError(
                    "STALLED",
                    "the solve stalled %d time(s); retry budget exhausted" % attempt_number)
            if entry is None:
                raise RealRunError("STALLED", "no attempt produced a result")
            self._update(job, stage="SOLVING")
            guard_status = str(getattr(entry, "status", "") or "").upper()
            if guard_status != "COMPLETED":
                code, detail = self._failure_reason(bridge, getattr(entry, "run_id", None))
                raise RealRunError(code, detail)
            metrics = {k: float(v) for k, v in (entry.metrics or {}).items()
                       if isinstance(v, (int, float))}
            guard_run_id = getattr(entry, "run_id", None) or job.run_id
            self._update(job, stage="POSTPROCESS", guard_run_id=guard_run_id)
            if self.approved_builder:
                job.pattern_reports = {"source": "guard_artifacts", "guard_run_id": guard_run_id}
            else:
                from ..farfield import capture

                # Read already-solved fields before the next candidate can change the project.
                # Visualisation has its own artifact and does not alter accepted KPI results.
                try:
                    snapshot = capture(bridge, campaign=self.campaign_dir, run_id=guard_run_id,
                                       project=project, design=self.design_name,
                                       setup=(getattr(getattr(executor, "config", None),
                                                      "farfield_setup_name", None) or self.setup_name), pid=pid)
                    job.farfield_warning = snapshot.get("detail", "")
                except Exception as exc:  # noqa: BLE001 - optional artifact I/O failure
                    job.farfield_warning = str(exc)[:500]
                if self.keep_pattern_reports:
                    # The capture above creates and deletes the reports to keep PyAEDT quiet; this
                    # rebuilds the same four reports and KEEPS them (plus the PNGs) so the user can
                    # open Results of the solved project and replot 3D / E / H / EH overlay.
                    try:
                        from ..farfield import persist_pattern_reports

                        job.pattern_reports = persist_pattern_reports(
                            bridge, campaign=self.campaign_dir, run_id=guard_run_id,
                            project=project, design=self.design_name,
                            setup=(getattr(getattr(executor, "config", None),
                                           "farfield_setup_name", None) or self.setup_name),
                            pid=pid,
                            output_dir=((Path(self.pattern_reports_dir) / guard_run_id)
                                        if self.pattern_reports_dir is not None else None))
                    except Exception as exc:  # noqa: BLE001 - optional artifact
                        job.pattern_reports = {"ok": False, "error": str(exc)[:500]}
            # keep the job id stable for the page; the guard id locates artifacts
            self._finish(job, stage="DONE", status="COMPLETED",
                         metrics=metrics, violations=dict(entry.violations or {}),
                         feasible=bool(entry.feasible), guard_run_id=guard_run_id)
        except RealRunError as exc:
            if exc.code == "APPROVAL_REQUIRED":
                self._finish(job, stage="APPROVAL_REQUIRED", status="APPROVAL_REQUIRED",
                             error_code=exc.code, error_detail=exc.detail)
                return
            self._finish(job, stage="DONE", status="FAILED",
                         error_code=exc.code, error_detail=exc.detail,
                         failure=classify(exc.code, exc.detail).to_dict())
        except Exception as exc:  # noqa: BLE001 - surface any failure to the page
            code = type(exc).__name__
            self._finish(job, stage="DONE", status="FAILED",
                         error_code=code, error_detail=str(exc)[:300],
                         failure=classify(code, str(exc)).to_dict())

    # -------------------------------------------------------- observability
    def observability(self) -> dict[str, Any]:
        """How long the stages take and how often runs fail, from the persisted queue.

        Answers the two questions a user actually asks: "why is this slow?" (stage medians) and
        "how often does it work?" (status counts).
        """
        from ..observability import summarise

        store = getattr(self, "store_instance", None)
        rows = store.jobs() if store is not None else []
        return summarise(rows)

    # -------------------------------------------------------------- resume
    def resumable(self) -> list[dict[str, Any]]:
        """Batches that still owe work, according to the queue database."""
        store = getattr(self, "store_instance", None)
        if store is None:
            return []
        return store.resumable()

    def resume_batch(self, batch_id: str) -> dict[str, Any] | None:
        """Re-queue the candidates of a batch that never finished.

        Returns the new batch record, or None when the queue has nothing left for that id.
        """
        store = getattr(self, "store_instance", None)
        if store is None:
            return None
        variants = store.variants_to_resume(batch_id)
        if not variants:
            return None
        case = None
        for row in store.batches():
            if row.get("batch_id") == batch_id:
                case = row.get("case") or row.get("case_name")
                break
        record = self.start_batch(case or getattr(self, "case_name", "lens28_var"), variants)
        record["resumed_from"] = batch_id
        return record

    def _finish(self, job: RealJob, **changes: Any) -> None:
        """Persist the terminal state, then publish it.

        The order matters on Windows: a caller that sees FAILED may immediately clean up the
        campaign directory, and if the queue database is still being written that cleanup fails with
        a sharing violation. Writing first makes the terminal state mean "recorded".
        """
        # close the stage that is still open before publishing: the terminal status arrives as one
        # of `changes`, and the time the last stage took would otherwise never be recorded
        with self.lock:
            now = time.monotonic()
            started = getattr(job, "_stage_started", None)
            if started is not None and job.stage in STAGES:
                job.timings[job.stage] = round(now - started, 2)
            job._stage_started = now
        from ..progress import classify  # noqa: PLC0415

        telemetry = self._telemetry.get(job.run_id)
        if telemetry is not None:
            payload = telemetry.to_dict()
            payload["verdict"] = classify(telemetry)
            job.telemetry = {**job.telemetry, **payload}
        record = job.to_dict()
        record.update(changes)
        record["total_seconds"] = round(sum(float(value) for value in job.timings.values()), 2)
        record["updated_utc"] = _now()
        self._persist(record)
        with self.lock:
            for key, value in changes.items():
                setattr(job, key, value)
            job.updated_utc = record["updated_utc"]
            job.total_seconds = record["total_seconds"]

    # ------------------------------------------------------------ attempts
    def _begin_attempt(self, job: RealJob, number: int) -> "Attempt":
        attempt = Attempt(number=number, started=time.monotonic())
        with self.lock:
            job.attempt_id = attempt.attempt_id
            job.attempt_number = number
        self.attempts.setdefault(job.run_id, []).append(attempt)
        return attempt

    def _evaluate_attempt(self, job: RealJob, executor: Any, attempt: "Attempt") -> "Outcome":
        """Run one attempt on its own thread and watch it; never starts another attempt until this
        one is confirmed finished (or the run enters RECOVERY_REQUIRED).

        The thread is daemonised on purpose: if the guard call never returns after its solver is
        killed, the process must still be able to shut down. What protects correctness is not the
        thread dying, it is that we refuse to start a second attempt while the first is alive.
        """
        from queue import Empty, Queue

        from ..stall import StallWatch
        from ..status_watch import EARLY_STAGES, EARLY_STATUSES, StatusWatch

        box: Queue = Queue()

        def run() -> None:
            try:
                box.put(("result", executor.evaluate(self._candidate(job))))
            except BaseException as exc:  # noqa: BLE001 - handed back to the caller
                box.put(("error", exc))

        thread = threading.Thread(target=run, name="attempt-%d" % attempt.number, daemon=True)
        attempt.thread = thread
        thread.start()

        from ..progress import Telemetry

        telemetry = Telemetry()
        telemetry.touch()
        self._telemetry[job.run_id] = telemetry
        cpu_watch = (self.stall_watch_factory() if self.stall_watch_factory else StallWatch())
        status_provider = self._guard_status_provider(job, executor)
        status_watch = (StatusWatch(status_provider=status_provider,
                                    stale_after_s=float(self.status_stale_s))
                        if status_provider is not None else None)
        # only the pre-solve stages: SOLVING lasts as long as the solve takes
        stage_watch = StatusWatch(status_provider=lambda: job.stage,
                                  stale_after_s=float(self.stage_timeout_s),
                                  active_statuses=EARLY_STAGES)
        detail = ""
        while True:
            if time.monotonic() - attempt.started > float(self.solve_budget_s):
                detail = "solve exceeded its %.0f s budget" % self.solve_budget_s
                break
            # heartbeat: this loop is alive. resources: only for the pid we own.
            telemetry.touch()
            try:
                sampler = self.resource_sampler
                if sampler is None:
                    from ..progress import sample_process_tree  # noqa: PLC0415
                    sampler = sample_process_tree
                telemetry.add_resource(sampler(attempt.owned_pid)
                                       if attempt.owned_pid is not None else None)
            except Exception:  # noqa: BLE001 - sampling must never break a run
                pass
            try:
                kind, value = box.get(timeout=float(self.solve_poll_s))
            except Empty:
                kind, value = "", None
            if kind == "result":
                return Outcome(result=value)
            if kind == "error":
                raise value
            if status_watch is not None and status_watch.sample():
                detail = "guard status stuck at %s for %.0f s" % (
                    status_watch.status, self.status_stale_s)
                break
            if stage_watch.sample():
                detail = "job stuck in stage %s for %.0f s" % (job.stage, self.stage_timeout_s)
                break
            if cpu_watch.sample():
                delta = (cpu_watch.samples[-1] - cpu_watch.samples[0]) \
                    if len(cpu_watch.samples) > 1 else 0.0
                detail = "no solver progress in %.0f s (cpu delta %.1f s)" % (
                    cpu_watch.window_s, delta)
                break

        return self._abort_attempt(job, attempt, detail)

    def _abort_attempt(self, job: RealJob, attempt: "Attempt", detail: str) -> "Outcome":
        """Kill only what this attempt owns, then wait for the attempt to actually stop.

        If it does not stop within the grace period we do not know whether the solver is still
        running, so the run ends in RECOVERY_REQUIRED instead of stacking a second attempt on top.
        """
        telemetry = self._telemetry.get(job.run_id)
        if telemetry is not None:
            telemetry.add_budget("recovery", time.monotonic() - attempt.started)
        self._kill_owned(attempt)
        if attempt.thread is not None:
            attempt.thread.join(timeout=float(self.recovery_grace_s))
        if attempt.thread is not None and attempt.thread.is_alive():
            attempt.abandoned = True
            return Outcome(terminal=True,
                           reason=("attempt %d did not stop within %.0f s after its solver was "
                                   "killed: %s" % (attempt.number, self.recovery_grace_s, detail)))
        self._close_attempt_bridge(attempt)
        return Outcome(reason=detail)

    def _kill_owned(self, attempt: "Attempt") -> None:
        """Terminate this attempt's AEDT/HF3D process tree - never by process name."""
        if attempt.owned_pid is None:
            return
        token = int(attempt.owned_pid)
        try:
            if self.kill_on_stall is not None:
                self.kill_on_stall(token)
            else:
                from ..stall import kill_process_tree
                kill_process_tree(token)
        except Exception:  # noqa: BLE001 - a failed kill becomes the recovery decision above
            pass
        self._log_line("killed owned pid %s for attempt %d" % (token, attempt.number))

    def _close_attempt_bridge(self, attempt: "Attempt") -> None:
        bridge = attempt.bridge
        if bridge is None:
            return
        for name in ("disconnect", "close"):
            closer = getattr(bridge, name, None)
            if callable(closer):
                try:
                    closer()
                except Exception:  # noqa: BLE001 - releasing is best effort
                    pass
                break

    def _log_line(self, message: str) -> None:
        sink = getattr(self, "log", None)
        if callable(sink):
            try:
                sink(message)
            except Exception:  # noqa: BLE001
                pass

    def _evaluate_with_stall_watch(self, job: RealJob, executor: Any) -> Any:
        """Compatibility wrapper: one attempt on the new lifecycle (kept for callers/tests).

        The retry loop now lives in :meth:`_run_job`, because a retry has to redo the whole
        prepare path, not just re-submit evaluate.
        """
        attempt = self._begin_attempt(job, 1)
        attempt.owned_pid = None
        outcome = self._evaluate_attempt(job, executor, attempt)
        if outcome.result is not None:
            return outcome.result
        if outcome.terminal:
            raise RealRunError("RECOVERY_REQUIRED", outcome.reason)
        self._update(job, stage="STALLED", status="RUNNING", error_code="STALLED",
                     error_detail=outcome.reason)
        raise RealRunError("STALLED", outcome.reason)

    def _guard_status_provider(self, job: RealJob, executor: Any) -> Any:
        """Read the live guard status, or None when no bridge or run id is available yet."""
        from ..status_watch import guard_status_reader

        bridge = getattr(self, "_shared_bridge", None)
        if bridge is None:
            return None

        def read() -> str:
            run_id = getattr(executor, "current_run_id", None)
            if not run_id:
                return ""
            return guard_status_reader(bridge, self.campaign_dir, str(run_id))()

        return read

    def _status_monitor(self, job: RealJob, executor: Any = None) -> Any:
        """Watch the guard's own status while the solve runs, when a bridge is available.

        The run id does not exist until the executor has submitted, so it is read lazily on every
        sample instead of being captured when the monitor starts.
        """
        from ..status_watch import StatusMonitor, StatusWatch, guard_status_reader

        bridge = getattr(self, "_shared_bridge", None)
        if bridge is None:
            return None

        def reader() -> str:
            run_id = getattr(executor, "current_run_id", None) if executor is not None else None
            if not run_id:
                return ""
            return guard_status_reader(bridge, self.campaign_dir, str(run_id))()
        watch = StatusWatch(status_provider=reader,
                            stale_after_s=float(self.status_stale_s),
                            active_statuses=EARLY_STATUSES)
        # the callback has to do something: evaluate() is blocking, so a flag alone would only be
        # read after the solve returned - which, for a stuck run, is never
        return StatusMonitor(watch,
                             interval_s=max(5.0, float(self.status_stale_s) / 10.0),
                             on_stall=lambda status: self._on_stall())

    def _stage_monitor(self, job: RealJob) -> Any:
        """A last-resort timeout: no stage may take longer than `stage_timeout_s`.

        The CPU sampler watches the machine and the status sampler watches the guard; neither sees a
        worker hung in CONNECTING, which is how one real verification run sat for 394 seconds before
        failing with a transport error.
        """
        from ..status_watch import StatusMonitor, StatusWatch

        watch = StatusWatch(status_provider=lambda: job.stage,
                            stale_after_s=float(self.stage_timeout_s))
        return StatusMonitor(watch,
                             interval_s=max(5.0, float(self.stage_timeout_s) / 10.0),
                             on_stall=lambda status: self._on_stall())

    def _on_stall(self) -> None:
        """Kill the solver so the retry starts from a clean desktop."""
        killer = self.kill_on_stall
        if killer is None:
            try:
                from ..stall import kill_solvers  # noqa: PLC0415

                killer = kill_solvers
            except Exception:  # noqa: BLE001
                return
        try:
            killer()
        except Exception:  # noqa: BLE001 - killing is best effort
            pass

    def _failure_reason(self, bridge: Any, run_id: str | None) -> tuple[str, str]:
        """Pull the guard's error code (e.g. E_NOT_CONVERGED) out of the stage log."""
        if not run_id:
            return "RUN_FAILED", "the guard reported a failure without a run id"
        try:
            status = bridge.status(str(self.campaign_dir), run_id) or {}
        except Exception as exc:  # noqa: BLE001
            return "RUN_FAILED", str(exc)[:200]
        import re

        for entry in reversed(status.get("stage_log") or []):
            error = str(entry.get("error") or "")
            if not error:
                continue
            found = re.search(r"E_[A-Z0-9_]+", error)
            return (found.group(0) if found else "RUN_FAILED"), error[:300]
        return "RUN_FAILED", "guard status: %s" % status.get("status")

    @staticmethod
    def _connect_bridge(bridge: Any, pid: int) -> None:
        try:
            result = bridge.connect(pid=pid)
        except Exception as exc:
            raise RealRunError("AEDT_CONNECT_FAILED", "connection failed: %s" % exc) from exc
        if result is False or (isinstance(result, dict) and result.get("connected") is False):
            raise RealRunError("AEDT_CONNECT_FAILED", "connection rejected: %s" % result)

    def _bridge(self, pid: int, job: RealJob) -> Any:
        if self.bridge_factory is not None:
            return self.bridge_factory(pid)
        # one connected bridge for the whole manager: a fresh instance per job keeps its own
        # MCP server state, so the snippets run against a session nobody opened and the guard
        # ends up trying to start a new AEDT on port 0
        if getattr(self, "_shared_bridge", None) is None:
            # a frozen application cannot import the guard's backend (PyAEDT lives in the venv),
            # so the packaged build talks to the server over stdio instead of importing it
            bridge = self._make_bridge()
            self._connect_bridge(bridge, pid)
            self._shared_bridge = bridge
        return self._shared_bridge

    def _make_bridge(self) -> Any:
        from ..version import frozen

        if frozen() and self.bridge_kind != "in_process":
            from ..bridge import OpenedStdioBridge

            return OpenedStdioBridge()
        from ..bridge import InProcessBridge

        return InProcessBridge()

    def _prepare_approved(self, bridge, pid: int, job: RealJob, source: Path, registration):
        from ..executor import GuardConfig, GuardExecutor

        model = registration.build_model()
        for check in (model.validate_geometry, model.validate_em_setup):
            verdict = check(dict(job.params))
            if not verdict.valid:
                raise RealRunError("MODEL_FAILED", "; ".join(verdict.messages))
        outdir = Path(self.builder_outdir or self.campaign_dir / "candidates").resolve()
        previous = job.telemetry.get("builder_approval") or {}
        if previous and (previous.get("outdir") != str(outdir) or previous.get("pid") != pid):
            raise RealRunError("APPROVAL_REQUIRED", "retry using the original output directory and PID")
        existing = {path.stem.casefold() for path in outdir.glob("*.aedt")}
        response = bridge.run_approved_builder(
            builder_id=self.approved_builder, project_path=str(source.resolve()), outdir=str(outdir),
            params={**job.params, "design": self.design_name}, pid=pid)
        if response.get("approval_required") is True and response.get("refused") is True:
            from hashlib import sha256
            from tempfile import NamedTemporaryFile

            directory = self.campaign_dir / "approval_requests"
            directory.mkdir(parents=True, exist_ok=True)
            request_file = directory / (sha256(job.run_id.encode()).hexdigest() + ".json")
            with NamedTemporaryFile("w", dir=directory, delete=False, encoding="utf-8") as handle:
                json.dump(response, handle, ensure_ascii=False, indent=2)
            Path(handle.name).replace(request_file)
            job.telemetry["builder_approval"] = {
                **response, "request_file": str(request_file.resolve()), "outdir": str(outdir),
                "pid": pid, "submitted": False}
            raise RealRunError("APPROVAL_REQUIRED", str(response.get("reason") or "review required"))
        payload = response.get("result")
        if (response.get("refused") or response.get("executed") is not True
                or not isinstance(payload, dict) or payload.get("validated") is not True
                or payload.get("design") != self.design_name or not isinstance(payload.get("saved"), str)):
            raise RealRunError("MODEL_FAILED", str(response.get("reason") or "invalid builder result"))
        target = Path(payload["saved"]).resolve()
        if (target.parent != outdir or target.suffix.lower() != ".aedt" or not target.is_file()
                or target.samefile(source) or target.stem.casefold() == source.stem.casefold()
                or target.stem.casefold() in existing):
            raise RealRunError("MODEL_FAILED", "builder did not return an isolated candidate project")
        case = registration.build_case()
        bridge.create_spec(str(self.campaign_dir), case.to_spec())
        config = GuardConfig(campaign_dir=str(self.campaign_dir), project_name=str(target),
                             design_name=self.design_name, setup_name=self.setup_name,
                             sweep_name=self.sweep_name,
                             frequencies_ghz=case.evaluation.get("frequencies_ghz"),
                             pid=pid, timeout_s=float(self.solve_budget_s))
        job.telemetry.pop("builder_approval", None)
        job.telemetry["builder_result"] = {
            "builder": self.approved_builder, "script_sha256": response.get("script_sha256"),
            "authorization": response.get("authorization"), "request_id": response.get("request_id"),
            "saved": str(target), "design": self.design_name}

        return target, GuardExecutor(case, config, bridge=bridge)

    def _prepare(self, bridge: Any, pid: int, job: RealJob) -> tuple[Path, Any]:
        # Validate identity and complete parameter coverage before touching AEDT.
        from ..parametric.registry import get_case
        self._bind_contract(job)
        registration = get_case(job.case)
        model = registration.build_model()
        try:
            job.params = model.space.validate(dict(job.params))
        except Exception as exc:
            raise RealRunError("INVALID_PARAMS", str(exc)) from exc
        if self.project_source is None:
            raise RealRunError("NO_PROJECT", "project_source is not configured")
        source = Path(self.project_source)
        if not source.is_file():
            raise RealRunError("NO_PROJECT", str(source))
        if self.approved_builder:
            if self.case_name != job.case:
                raise RealRunError("BUILDER_CASE_MISMATCH", job.case)
            return self._prepare_approved(bridge, pid, job, source, registration)
        projects = self.campaign_dir / "projects"
        projects.mkdir(parents=True, exist_ok=True)
        target = projects / ("%s.aedt" % job.case)
        # the destructive-operation model needs a pristine copy per run; the variable-driven
        # model only rebuilds its own bodies, so one copy can serve the whole session
        import shutil

        fresh_copy = not (self.reuse_project and target.is_file())
        if fresh_copy:
            shutil.copy2(source, target)
        # A different queued case has a different project. Preparing one project
        # must never suppress sweep preparation for a second project's first use.
        sweep_key = str(target.resolve()).casefold()
        if fresh_copy or sweep_key not in self._sweep_projects:
            self._ensure_band_sweep(bridge, target)
            self._sweep_done = True
            self._sweep_projects.add(sweep_key)
        # the guard resolves projects inside its search dirs; the campaign copy has
        # to be visible there or the worker refuses to touch it
        import os

        search = os.environ.get("AEDT_GUARD_SEARCH_DIRS", "")
        entries = [item for item in search.split(os.pathsep) if item]
        if str(projects) not in entries:
            entries.append(str(projects))
        os.environ["AEDT_GUARD_SEARCH_DIRS"] = os.pathsep.join(entries)
        bridge.clear_stale_lock(str(target))
        if self.executor_factory is not None:
            return target, self.executor_factory(bridge=bridge, pid=pid,
                                                 project=target, job=job)
        from ..executor import GuardConfig, GuardExecutor
        from ..apply import ModelApplier
        from ..parametric.registry import get_case

        registration = get_case(job.case)
        case = registration.build_case()
        model = registration.build_model()
        case.frequency_ghz = tuple(self.band_ghz)
        case.evaluation = {**case.evaluation, "frequencies_ghz": [self.farfield_ghz]}
        campaign = self.campaign_dir
        campaign.mkdir(parents=True, exist_ok=True)
        spec = case.to_spec()
        spec["frequency_ghz"] = list(self.band_ghz)
        spec.setdefault("evaluation", {})["frequencies_ghz"] = [self.farfield_ghz]
        spec.setdefault("execution_policy", {})["max_run_minutes"] = self.max_minutes
        bridge.create_spec(str(campaign), spec)

        applier = ModelApplier(model, bridge,
                               project_path=str(target), design_name=self.design_name,
                               pid=pid)
        applied = applier.apply(job.params)
        if not (applied.ok and applied.validated):
            raise RealRunError("MODEL_FAILED", str(applied.error))
        # the caller-thread preparation already configured the reused copy; doing it again from
        # the worker thread hits the MCP session that thread does not own
        if not self._sweep_done:
            self._configure_setup(bridge, pid, target, job)
        config = GuardConfig(
            campaign_dir=str(self.campaign_dir), project_name=target.stem,
            design_name=self.design_name, setup_name=self.setup_name,
            cuts=["theta_phi0", "phi_theta90"], frequencies_ghz=[self.farfield_ghz],
            sweep_name=self.sweep_name, num_cores=16, pid=pid,
            poll_interval_s=10.0, timeout_s=float(self.max_minutes) * 60.0,
            farfield_setup_name=self.setup_name, extra_setups=[],
        )
        return target, GuardExecutor(case, config, bridge=bridge)

    def _configure_setup(self, bridge: Any, pid: int, target: Path, job: RealJob) -> None:
        """Set the pass budget and a discrete 24-30 GHz sweep on the copied design.

        The guard does not manage adaptive passes, and the measured recipe needs
        eight passes for a single-parameter tweak and twelve for a structural one.
        """
        from ..executor import recommended_passes

        passes = self.passes or recommended_passes(job.params, DEFAULT_BASELINE)
        acf = str(ACF_PATH) if ACF_PATH.is_file() else ""
        start, stop = self.band_ghz
        code = (
            "if \"_aster_app\" not in globals():\n"
            "    from ansys.aedt.core import Hfss as _AsterHfss\n"
            "    _aster_app = _AsterHfss(project=r\"%s\", design=r\"%s\",\n"
            "        new_desktop=False, close_on_exit=False, non_graphical=False,\n"
            "        port=aedt_port)\n" % (target, self.design_name)
            + "app = _aster_app\n"
            + ("try:\n"
               "    odesktop.SetRegistryFromFile(r\"%s\")\n"
               "    app.set_registry_key(r\"Desktop/ActiveDSOConfigurations/HFSS\",\n"
               "        r\"b0_16core\")\n"
               "except Exception:\n"
               "    pass\n" % acf if acf else "")
            + "setup = app.get_setup(\"%s\")\n" % self.setup_name
            + "setup.props[\"MaximumPasses\"] = %d\n" % passes
            + "setup.props[\"MaxDeltaS\"] = %r\n" % float(self.max_delta_s)
            + ("setup.props[\"Frequency\"] = %r\n" % ("%gGHz" % self.solution_ghz)
               if self.solution_ghz else "")
            + "setup.update()\n"
            + "for sweep in list(setup.sweeps):\n"
              "    setup.delete_sweep(sweep.name)\n"
            + "setup.create_linear_step_sweep(unit=\"GHz\", start_frequency=%r,\n"
              "    stop_frequency=%r, step_size=%r, name=\"%s\",\n"
              "    sweep_type=%r, save_fields=%r)\n"
              % (float(start), float(stop), float(self.sweep_step_ghz), self.sweep_name,
                 str(self.sweep_type), bool(self.save_fields))
            + "".join(
                "setup.create_single_point_sweep(unit=\"GHz\", freq=%r, name=\"ANT_Verify_%s\", "
                "save_single_field=True, save_fields=True, save_rad_fields=True)\n"
                % (float(freq), format(float(freq), ".12g").replace(".", "p"))
                for freq in self.verify_frequencies_ghz)
            + "result = {\"passes\": %d, \"sweeps\": [s.name for s in setup.sweeps]}\n"
              % passes
        )
        outcome = bridge.execute_code(code, pid=pid)
        result = (outcome or {}).get("result") or {}
        if outcome.get("error"):
            raise RealRunError("SETUP_FAILED", str(outcome.get("error"))[:300])
        if "error" in str(result)[:200] and not result.get("passes"):
            raise RealRunError("SETUP_FAILED", str(result)[:300])

    def _candidate(self, job: RealJob) -> Any:
        from types import SimpleNamespace
        from ..parametric.registry import get_case

        try:
            family_id = get_case(job.case).build_case().family.family_id
        except KeyError:
            family_id = job.case
        return SimpleNamespace(candidate_id=job.run_id,
                               family_id=family_id,
                               params=dict(job.params),
                               origin={"algorithm": "gui-real"})

    def _persist(self, payload: RealJob | dict[str, Any]) -> None:
        record = payload.to_dict() if isinstance(payload, RealJob) else dict(payload)
        with self.lock:
            self.campaign_dir.mkdir(parents=True, exist_ok=True)
            with (self.campaign_dir / "real_runs.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        store = getattr(self, "store_instance", None)
        if store is not None:
            try:
                store.save_job(record, batch_id=self._batch_of(record.get("run_id", "")))
            except Exception:  # noqa: BLE001 - persistence must never fail a run
                pass

    def _batch_of(self, run_id: str) -> str | None:
        for batch_id, record in self.batches.items():
            if run_id in (record.get("run_ids") or []):
                return batch_id
        return None


@dataclass
class Attempt:
    """One solve attempt: its own thread, its own AEDT pid, its own bridge and executor."""

    number: int
    started: float
    attempt_id: str = field(default_factory=lambda: "attempt-%d" % int(time.time() * 1000))
    owned_pid: int | None = None
    thread: Any = None
    bridge: Any = None
    executor: Any = None
    abandoned: bool = False


@dataclass
class Outcome:
    """What one attempt produced: a result, a stall to retry, or a recovery block."""

    result: Any = None
    reason: str = ""
    terminal: bool = False
