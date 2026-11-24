# -*- coding: utf-8 -*-
"""Optional solve+export adapter for the approved-builder workflow.

``ApprovedBuilderExecutor`` intentionally stops after the guard-approved builder
has built and saved a project.  Many real antenna studies need one more step:
submit that saved project as a guarded run, wait for convergence, export native
S11/far-field data, then turn those files into an ``Evaluation``.

This module keeps that environment-specific step out of the core workflow.  A
project supplies a ``GuardedSolveConfig`` (guard paths, setup/sweep, fixed
parameters, optional spec factory/metric aliases) and injects
``GuardedSolveExecutor`` through ``WorkflowRunner(executor_factory=...)`` or
``run_approved_campaign(executor=...)``.

Nothing here bypasses the guard: the solve is submitted with
``submit_guarded_run``/the guard bridge and still goes through preflight,
managed-session checks and ledger recording.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .approved_builder import ApprovedBuilderConfig, ApprovedBuilderExecutor, metrics_from_artifacts
from .core import CaseSpec
from .optimize import Evaluation, evaluate_constraints


@dataclass
class GuardedSolveConfig:
    """Settings for the guard-backed solve/export stage.

    ``guard_dir``/``guard_python`` identify the guard checkout.  The daemon is
    optional: some deployments have ``submit_guarded_run`` drive the background
    runner themselves; in that case set ``run_daemon=False``.
    """

    guard_dir: str = r"C:\ProgramData\ansys-aedt-mcp"
    guard_python: str = ""
    design: str | None = None
    setup_name: str = "Setup1"
    sweep_name: str | None = None
    frequencies_ghz: tuple[float, ...] = ()
    cuts: tuple[str, ...] = ()
    fixed_params: dict[str, Any] = field(default_factory=dict)
    # Parameters recorded on the guarded run itself (not just the builder):
    # e.g. symmetry="none", design_name/setup_name, or recipe metadata used by a
    # project native check. These are merged over candidate.params.
    run_params: dict[str, Any] = field(default_factory=dict)
    metric_aliases: dict[str, str] = field(default_factory=dict)
    spec_factory: Callable[[str, str], dict[str, Any]] | None = None
    metric_hook: Callable[[Path, CaseSpec], dict[str, Any]] | None = None
    num_cores: int = 8
    num_tasks: int = 1
    timeout_s: float = 3600.0
    worker_timeout_s: float = 3000.0
    poll_interval_s: float = 10.0
    run_daemon: bool = True

    def python(self) -> Path:
        if self.guard_python:
            return Path(self.guard_python)
        return Path(self.guard_dir) / ".venv" / "Scripts" / "python.exe"


def _case_spec(project: str, design: str, case: CaseSpec, config: GuardedSolveConfig) -> dict[str, Any]:
    """Build the campaign spec used by ``submit_guarded_run``.

    A project can override this with ``spec_factory`` when its measurement
    recipe needs machine-specific fields.
    """
    metrics: list[dict[str, Any]] = []
    for constraint in getattr(case, "constraints", ()):
        item: dict[str, Any] = {
            "id": constraint.id,
            "kind": constraint.kind,
            "operator": getattr(constraint, "operator", "<="),
            "target": getattr(constraint, "target", 0.0),
            "unit": getattr(constraint, "unit", "dB"),
            "hard": True,
        }
        band = getattr(constraint, "band_ghz", None)
        if band:
            item["band_ghz"] = list(band)
        metrics.append(item)
    for objective in getattr(case, "objectives", ()):
        metrics.append({
            "id": objective.id,
            "kind": objective.kind,
            "operator": "<=",
            "target": 0.0,
            "unit": "dB",
            "hard": False,
        })
    evaluation = dict(getattr(case, "evaluation", None) or {})
    if config.frequencies_ghz:
        evaluation.setdefault("frequencies_ghz", list(config.frequencies_ghz))
    return {
        "schema_version": 1,
        "project": project,
        "design_type": getattr(case, "design_type", "aedt"),
        "frequency_ghz": list(getattr(case, "frequency_ghz", ())),
        "metrics": metrics,
        "evaluation": evaluation,
        "execution_policy": {
            "max_run_minutes": max(1, int(config.timeout_s // 60)),
            "require_ff_sphere": bool(getattr(case, "evaluation", {}).get("require_ff_sphere", True)),
            "blocking_timeout_s": config.timeout_s,
            "require_convergence": True,
        },
        "history": [],
    }


class GuardedSolveExecutor(ApprovedBuilderExecutor):
    """Build through an approved builder, then solve/export through the guard."""

    def __init__(
        self,
        case: CaseSpec,
        config: ApprovedBuilderConfig,
        solve: GuardedSolveConfig | None = None,
        bridge: Any = None,
    ):
        super().__init__(case, config, bridge=bridge)
        self.solve = solve or GuardedSolveConfig(design=config.design)

    def _builder_params(self, params: Mapping[str, Any]) -> dict[str, Any]:
        mapped = super()._builder_params(params)
        mapped.update(self.solve.fixed_params)
        if self.solve.design:
            mapped["design"] = self.solve.design
        return mapped

    @staticmethod
    def _call(fn, *args, **kwargs):
        import asyncio

        result = fn(*args, **kwargs)
        return asyncio.run(result) if asyncio.iscoroutine(result) else result

    def _run_guarded(self, project: str, design: str, attempt: Path,
                     run_params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        campaign = attempt / "guard_campaign"
        campaign.mkdir(parents=True, exist_ok=True)
        spec = (self.solve.spec_factory(project, design) if self.solve.spec_factory
                else _case_spec(project, design, self.case, self.solve))
        self.bridge.create_spec(str(campaign), spec)
        submitted = self.bridge.submit(
            campaign_dir=str(campaign),
            project_name=project,
            design_name=design,
            setup_name=self.solve.setup_name,
            sweep_name=self.solve.sweep_name,
            frequencies_ghz=list(self.solve.frequencies_ghz) or None,
            cuts=list(self.solve.cuts),
            params=dict(run_params or {}),
            pid=self.config.pid,
            port=self.config.port,
            num_cores=self.solve.num_cores,
            num_tasks=self.solve.num_tasks,
            timeout_s=self.solve.timeout_s,
            worker_timeout=self.solve.worker_timeout_s,
            poll_interval_s=self.solve.poll_interval_s,
        )
        run_id = str(submitted.get("run_id") or "")
        if not run_id:
            raise RuntimeError("guarded submit returned no run_id")

        process = None
        if self.solve.run_daemon:
            daemon = Path(self.solve.guard_dir) / "guard_daemon.py"
            if not daemon.is_file():
                raise RuntimeError("guard_daemon.py not found: %s" % daemon)
            process = subprocess.Popen(
                [str(self.solve.python()), str(daemon), "--campaign", str(campaign), "--once"],
                cwd=str(self.solve.guard_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        terminal = {"COMPLETED", "FAILED", "RECOVERY_REQUIRED", "TIMEOUT", "CANCELLED"}
        fallback_artifacts = campaign / "runs" / run_id / "artifacts"
        deadline = time.monotonic() + self.solve.timeout_s
        status: dict[str, Any] = {}
        while time.monotonic() < deadline:
            try:
                status = self._call(self.bridge.status, str(campaign), run_id)
            except Exception as exc:  # noqa: BLE001 - long solves can wedge gRPC
                # Full-model solves can finish and save while the AEDT gRPC
                # endpoint can no longer answer GetSetups/GetSolutionType.
                if (fallback_artifacts / "result.s1p").is_file():
                    status = {
                        "status": "COMPLETED",
                        "terminal": True,
                        "artifact_dir": str(fallback_artifacts),
                        "note": "artifacts present; status poll failed: %s" % (type(exc).__name__,),
                    }
                    break
                status = {"status": "POLL_ERROR", "error": "%s: %s" % (type(exc).__name__, exc)}
            if status.get("status") in terminal or status.get("terminal"):
                break
            time.sleep(self.solve.poll_interval_s)
        if process is not None:
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
        if not status.get("status") and (fallback_artifacts / "result.s1p").is_file():
            status = {"status": "COMPLETED", "terminal": True,
                      "artifact_dir": str(fallback_artifacts),
                      "note": "artifacts present after timeout"}
        artifact_dir = Path(status.get("artifact_dir") or fallback_artifacts)
        return {"run_id": run_id, "status": status, "artifact_dir": artifact_dir,
                "campaign_dir": str(campaign)}

    @staticmethod
    def _collect(attempt: Path, artifact_dir: Path) -> list[Path]:
        solved = attempt / "solved"
        solved.mkdir(parents=True, exist_ok=True)
        collected: list[Path] = []
        s1p = artifact_dir / "result.s1p"
        if s1p.is_file():
            target = solved / "result.s1p"
            shutil.copy2(s1p, target)
            collected.append(target)
        farfield = artifact_dir / "farfield"
        if farfield.is_dir():
            target_dir = solved / "farfield"
            target_dir.mkdir(parents=True, exist_ok=True)
            for csv_path in sorted(farfield.glob("*.csv")):
                target = target_dir / csv_path.name
                shutil.copy2(csv_path, target)
                collected.append(target)
        metrics_json = artifact_dir / "farfield_metrics.json"
        if metrics_json.is_file():
            target = solved / "farfield_metrics.json"
            shutil.copy2(metrics_json, target)
            collected.append(target)
        return collected

    def evaluate(self, candidate: Any) -> Evaluation:
        candidate_id = str(getattr(candidate, "candidate_id", "candidate"))
        started = time.monotonic()
        build = super().evaluate(candidate)
        if build.status not in ("MEASUREMENT_MISSING", "COMPLETED"):
            return build
        attempt = Path(build.origin.get("attempt_dir") or "")
        guard_result = build.origin.get("guard_result") or {}
        saved = guard_result.get("saved")
        if not saved:
            try:
                log = json.loads((attempt / "build_log.json").read_text(encoding="utf-8"))
                saved = log.get("saved")
            except (OSError, ValueError):
                saved = None
        if not saved or not Path(saved).is_file():
            build.status = "MODEL_FAILED"
            build.origin["error"] = "builder produced no saved project"
            return build
        design = self.solve.design or self.config.design or ""
        if not design:
            build.status = "MODEL_FAILED"
            build.origin["error"] = "solve config has no design name"
            return build
        run_params = {**dict(candidate.params), **self.solve.run_params}
        try:
            solved = self._run_guarded(str(Path(saved).resolve()), design, attempt,
                                       run_params=run_params)
            collected = self._collect(attempt, solved["artifact_dir"])
        except Exception as exc:  # noqa: BLE001
            build.status = "SOLVE_FAILED"
            build.origin.update({"error": "%s: %s" % (type(exc).__name__, exc),
                                 "attempt_dir": str(attempt)})
            return build

        artifact_result = {"artifacts": [str(path) for path in collected]}
        metrics, evidence = metrics_from_artifacts(self.case, artifact_result, attempt)
        for target, source in self.solve.metric_aliases.items():
            if metrics.get(target) is None and isinstance(metrics.get(source), (int, float)):
                metrics[target] = metrics[source]
        if self.solve.metric_hook:
            extra = self.solve.metric_hook(attempt, self.case) or {}
            for key, value in extra.items():
                if isinstance(value, (int, float)) and metrics.get(key) is None:
                    metrics[key] = value
        feasible, violations, total = evaluate_constraints(metrics, self.case)
        missing = [m.kind for m in (*self.case.constraints, *self.case.objectives)
                   if metrics.get(m.kind) is None]
        status = "COMPLETED" if not missing else "MEASUREMENT_MISSING"
        return Evaluation(
            candidate_id=candidate_id,
            family_id=self.case.family.family_id,
            params=dict(candidate.params),
            metrics=metrics,
            feasible=feasible and status == "COMPLETED",
            violations=violations,
            total_violation=total,
            run_id=solved["run_id"],
            status=status,
            origin={
                "attempt_dir": str(attempt),
                "solved_dir": str(attempt / "solved"),
                "guard_run_id": solved["run_id"],
                "guard_status": solved["status"].get("status"),
                "guard_campaign": solved["campaign_dir"],
                "evidence": evidence,
                "collected": [str(path) for path in collected],
                "wall_s": round(time.monotonic() - started, 1),
            },
        )
