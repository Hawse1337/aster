# -*- coding: utf-8 -*-
"""Approved-builder adapter for the aster optimisation stack.

This is the "aster -> guard" bridge that does not use arbitrary code:

* the optimiser proposes a candidate;
* this adapter calls the guard's ``run_approved_builder`` with the candidate
  path/params and provenance metadata;
* the guard returns a working project and artifact list;
* the adapter reads the measured S11 / optional far-field metrics from those
  artifacts and returns an ``Evaluation`` to the campaign loop.

It deliberately does not infer metrics from a model. If an artifact is missing,
the candidate is recorded as failed rather than guessed.
"""
from __future__ import annotations

import json
import inspect
import math
import tempfile
import time
from itertools import islice
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from .core import CaseSpec
from .doe import plan_doe
from .optimization_gate import (
    campaign_gate,
    new_manifest,
    record_run,
    write_manifest,
)
from .optimize import Evaluation, evaluate_constraints
from .touchstone import read_touchstone


@dataclass
class ApprovedBuilderConfig:
    campaign_id: str
    campaign_dir: str
    builder_id: str
    project_path: str
    outdir_root: str
    pid: int | None = None
    port: int | None = None
    design: str | None = None
    timeout_s: float = 1800.0
    param_map: dict[str, str] = field(default_factory=dict)


class _NoopApplier:
    """CampaignRunner only uses the applier in its legacy GuardExecutor path."""

    def __getattr__(self, _name: str):
        return lambda *args, **kwargs: None


def _all_artifacts(result: Mapping[str, Any], outdir: Path) -> list[Path]:
    found: list[Path] = []
    for item in result.get("artifacts") or []:
        try:
            path = Path(str(item))
        except (TypeError, ValueError):
            continue
        if path.is_file() and path.resolve().is_relative_to(outdir.resolve()):
            found.append(path)
    if outdir.is_dir():
        for path in outdir.rglob("*"):
            try:
                if path.is_file() and path.resolve().is_relative_to(outdir.resolve()):
                    found.append(path)
            except OSError:
                continue
    # Stable and deduplicated, touchstone first.
    unique = {str(path.resolve()): path for path in found if path.is_file()}
    return sorted(unique.values(), key=lambda p: (p.suffix.lower() != ".s1p", str(p)))


def _pick_s1p(artifacts: Iterable[Path]) -> Path | None:
    paths = [path for path in artifacts if path.suffix.lower() in {".s1p", ".s2p", ".s3p"}]
    if not paths:
        return None
    return sorted(paths, key=lambda p: (p.name.lower() != "result.s1p", len(str(p))))[0]


def _required_frequencies(case: CaseSpec) -> list[float]:
    evaluation = case.evaluation or {}
    raw = evaluation.get("frequencies_ghz")
    if isinstance(raw, (list, tuple)) and raw:
        return [float(value) for value in raw]
    low, high = case.frequency_ghz
    return [float(low) + index for index in range(int(round(float(high) - float(low))) + 1)]


def _s11_metric(case: CaseSpec, touchstone: Path) -> tuple[float | None, dict[str, Any]]:
    try:
        points = read_touchstone(touchstone)
    except Exception as exc:  # noqa: BLE001
        return None, {"error": "touchstone parse failed: %s" % exc, "path": str(touchstone)}
    by_freq = {round(float(freq), 9): float(db) for freq, db in points}
    required = _required_frequencies(case)
    samples: list[tuple[float, float]] = []
    missing: list[float] = []
    for frequency in required:
        value = by_freq.get(round(float(frequency), 9))
        if value is None:
            missing.append(float(frequency))
        else:
            samples.append((float(frequency), value))
    details = {
        "path": str(touchstone),
        "required_frequencies": required,
        "samples": samples,
        "missing_frequencies": missing,
        "worst_db": max((db for _freq, db in samples), default=None),
    }
    if missing or not samples:
        return None, details
    return details["worst_db"], details


def _farfield_metrics(artifacts: Iterable[Path]) -> tuple[dict[str, float | None], dict[str, Any]]:
    paths = [path for path in artifacts if path.name.lower() == "farfield_metrics.json"]
    for path in sorted(paths, key=lambda p: len(str(p))):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        e = data.get("E") or {}
        h = data.get("H") or {}
        peak = None
        candidates = [
            e.get("E_peak_dbi"), h.get("max"), h.get("peak_dbi"),
        ]
        numeric = [float(value) for value in candidates if isinstance(value, (int, float))]
        if numeric:
            peak = max(numeric)
        ripple = h.get("ripple_db")
        metrics = {
            "peak_gain": peak,
            "ripple": float(ripple) if isinstance(ripple, (int, float)) else None,
            "e_sll_db": float(e["E_sll_db"]) if isinstance(e.get("E_sll_db"), (int, float)) else None,
        }
        return metrics, {"path": str(path), "source": data}
    return {}, {}


def _result_metrics(result: Mapping[str, Any], case: CaseSpec) -> dict[str, Any]:
    """Accept explicit metrics only when a trusted builder/export produced them."""
    candidates: list[Mapping[str, Any]] = []
    inner = result.get("result")
    if isinstance(inner, Mapping):
        candidates.append(inner)
        if isinstance(inner.get("metrics"), Mapping):
            candidates.append(inner["metrics"])
    if isinstance(result.get("metrics"), Mapping):
        candidates.append(result["metrics"])
    kinds = {m.kind for m in (*case.constraints, *case.objectives)}
    measured = {}
    for payload in candidates:
        metrics = {
            kind: float(payload[kind])
            for kind in kinds
            if type(payload.get(kind)) in (int, float) and math.isfinite(payload[kind])
        }
        measured.update(metrics)
    return measured


def metrics_from_artifacts(
    case: CaseSpec,
    result: Mapping[str, Any],
    outdir: Path,
) -> tuple[dict[str, float | None], dict[str, Any]]:
    artifacts = _all_artifacts(result, outdir)
    metrics: dict[str, float | None] = {}
    evidence: dict[str, Any] = {"artifacts": [str(path) for path in artifacts[:50]]}
    s1p = _pick_s1p(artifacts)
    if s1p is not None:
        s11, s11_details = _s11_metric(case, s1p)
        metrics["s11"] = s11
        evidence["s11"] = s11_details
    farfield, farfield_details = _farfield_metrics(artifacts)
    for key, value in farfield.items():
        metrics.setdefault(key, value)
    if farfield_details:
        evidence["farfield"] = farfield_details
    explicit = _result_metrics(result, case)
    for key, value in explicit.items():
        metrics.setdefault(key, value)
    for requirement in (*case.constraints, *case.objectives):
        value = metrics.get(requirement.kind)
        if type(value) not in (int, float) or not math.isfinite(value):
            metrics[requirement.kind] = None
    return metrics, evidence


class ApprovedBuilderExecutor:
    """An aster ``Executor`` that evaluates candidates through the guard builder."""

    def __init__(self, case: CaseSpec, config: ApprovedBuilderConfig, bridge: Any = None):
        self.case = case
        self.config = config
        if (config.pid is None) == (config.port is None):
            raise ValueError("ApprovedBuilderConfig needs exactly one explicit AEDT pid or grpc port")
        if bridge is None:
            from .bridge import InProcessBridge  # noqa: PLC0415

            bridge = InProcessBridge()
        self.bridge = bridge

    def _builder_params(self, params: Mapping[str, Any]) -> dict[str, Any]:
        mapped = {
            self.config.param_map.get(name, name): value
            for name, value in params.items()
        }
        if self.config.design:
            mapped["design"] = self.config.design
        return mapped

    def evaluate(self, candidate: Any) -> Evaluation:
        candidate_id = str(getattr(candidate, "candidate_id", "candidate"))
        started = time.monotonic()
        candidate_dir = Path(self.config.outdir_root) / candidate_id
        candidate_dir.mkdir(parents=True, exist_ok=True)
        outdir = Path(tempfile.mkdtemp(prefix="attempt-", dir=candidate_dir))
        provenance = {
            "campaign_id": self.config.campaign_id,
            "campaign_dir": self.config.campaign_dir,
            "candidate_id": candidate_id,
            "purpose": "candidate",
            "source": "aster",
            "optimizer": "aster",
        }
        params = self._builder_params(dict(candidate.params))
        try:
            build = self.bridge.run_approved_builder
            arguments = dict(builder_id=self.config.builder_id, project_path=self.config.project_path,
                             outdir=str(outdir), params=params, pid=self.config.pid,
                             port=self.config.port, timeout=self.config.timeout_s)
            signature = inspect.signature(build)
            if "provenance" in signature.parameters or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()):
                arguments["provenance"] = provenance
            result = build(**arguments)
        except Exception as exc:  # noqa: BLE001
            return Evaluation(
                candidate_id=candidate_id,
                family_id=self.case.family.family_id,
                params=dict(candidate.params),
                metrics={},
                feasible=False,
                violations={},
                total_violation=1e3,
                run_id=None,
                status="MODEL_FAILED",
                origin={"provenance": provenance, "error": "%s: %s" % (type(exc).__name__, exc)},
            )
        pending = isinstance(result, Mapping) and (result.get("approval_required") or
                   str(result.get("status", "")).upper() == "APPROVAL_REQUIRED")
        if not isinstance(result, Mapping) or pending or result.get("refused") or result.get("error"):
            return Evaluation(
                candidate_id=candidate_id,
                family_id=self.case.family.family_id,
                params=dict(candidate.params),
                metrics={},
                feasible=False,
                violations={},
                total_violation=1e3,
                run_id=str(result.get("run_id") or "") if isinstance(result, Mapping) else None,
                status="APPROVAL_REQUIRED" if pending else "MODEL_FAILED",
                origin={"provenance": provenance, "guard_result": result},
            )
        metrics, evidence = metrics_from_artifacts(self.case, result, outdir)
        feasible, violations, total = evaluate_constraints(metrics, self.case)
        # If required measurements were missing, evaluate_constraints marks them
        # infeasible; never fabricate a pass from an absent artifact.
        missing = [m.kind for m in (*self.case.constraints, *self.case.objectives) if metrics.get(m.kind) is None]
        status = "COMPLETED" if not missing else "MEASUREMENT_MISSING"
        if status != "COMPLETED":
            feasible = False
        return Evaluation(
            candidate_id=candidate_id,
            family_id=self.case.family.family_id,
            params=dict(candidate.params),
            metrics=metrics,
            feasible=feasible,
            violations=violations,
            total_violation=total,
            run_id=str(result["run_id"]) if result.get("run_id") else None,
            status=status,
            origin={
                "provenance": provenance,
                "attempt_dir": str(outdir),
                "guard_result": {
                    key: result.get(key)
                    for key in ("saved", "artifacts", "artifact_warning", "launched_session")
                },
                "evidence": evidence,
                "wall_s": round(time.monotonic() - started, 2),
            },
        )


def run_approved_campaign(
    case: CaseSpec,
    config: ApprovedBuilderConfig,
    *,
    budget: int = 2,
    seed: int = 0,
    candidates: Iterable[Mapping[str, Any]] | None = None,
    bridge: Any = None,
    require_acceptance: bool = True,
) -> dict[str, Any]:
    """Run a small approved-builder campaign and write the completion gate.

    This uses aster's CaseSpec, DOE, ParetoArchive and completion-gate contract;
    it does not use PyAEDT or arbitrary code. ``candidates`` can be supplied by
    a higher-level strategy; otherwise a deterministic stratified DOE is used.
    """
    from .campaign import CampaignRunner  # noqa: PLC0415
    from .executor import GuardConfig  # noqa: PLC0415

    if type(budget) is not int or budget < 0:
        raise ValueError("budget must be a nonnegative integer")
    if candidates is not None:
        params_list = [dict(point) for point in islice(candidates, budget + 1)]
        if len(params_list) > budget:
            raise ValueError("candidate list exceeds budget")
    elif budget:
        plan = plan_doe(case.family.param_space, case.case_id, count=budget, seed=seed)
        params_list = [dict(point) for point in plan.points]
    else:
        params_list = []
    campaign_dir = Path(config.campaign_dir)
    campaign_dir.mkdir(parents=True, exist_ok=True)
    executor = ApprovedBuilderExecutor(case, config, bridge=bridge)
    guard_config = GuardConfig(
        campaign_dir=str(campaign_dir),
        project_name=config.project_path,
        design_name=config.design or "",
        pid=config.pid,
        port=config.port,
    )
    runner = CampaignRunner(
        case,
        _NoopApplier(),
        guard_config,
        campaign_dir=campaign_dir,
        bridge=bridge,
        executor=executor,
    )
    manifest = new_manifest(
        campaign_id=config.campaign_id,
        optimizer="aster",
        case_id=case.case_id,
        builder_id=config.builder_id,
        campaign_dir=campaign_dir,
        source="aster",
    )
    write_manifest(campaign_dir, manifest)
    for params in params_list:
        record = runner.evaluate(params)
        row = record.to_dict()
        record_run(manifest, row)
        write_manifest(campaign_dir, manifest)
    front = [asdict(entry) for entry in runner.archive.feasible_front()]
    manifest["pareto"] = front
    feasible = [row for row in manifest["runs"] if row.get("feasible")]
    manifest["acceptance"] = {
        "passed": bool(feasible),
        "source": "local_constraints",
        "feasible_runs": len(feasible),
        "total_runs": len(manifest["runs"]),
        "note": "adapter-level acceptance; independent replay may still be required",
    }
    manifest["status"] = "completed" if feasible else "failed"
    write_manifest(campaign_dir, manifest)
    gate = campaign_gate(
        campaign_dir,
        require_optimizer=True,
        min_completed_runs=1,
        require_pareto=require_acceptance,
        require_acceptance=require_acceptance,
    )
    manifest["gate"] = gate
    write_manifest(campaign_dir, manifest)
    return {"campaign_dir": str(campaign_dir), "manifest": manifest, "gate": gate}
