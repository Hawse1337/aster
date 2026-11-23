# -*- coding: utf-8 -*-
"""Standard optimisation workflow: plan -> baseline -> screening -> search -> gate.

The runner does not let an agent improvise the whole loop.  It owns the stage
state machine and calls aster's existing components:

* ``plan_doe`` / ``ParamSpace`` for screening candidates;
* ``ApprovedBuilderExecutor`` + ``run_approved_campaign`` for guard-backed
  evaluation;
* ``optimizer.nsga2`` / ``nsga3`` for multi-objective search;
* ``optimization_gate`` for completion evidence.

The plan is deliberately small and machine-checkable.  An agent may choose the
route/strategy, but cannot mark a stage complete without the required artifacts.
"""
from __future__ import annotations

import dataclasses
from contextlib import closing
from functools import wraps
import hashlib
import sqlite3
import json
from importlib import import_module
from itertools import islice
import math
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .approved_builder import (
    ApprovedBuilderConfig,
    ApprovedBuilderExecutor,
    run_approved_campaign,
)
from .core import CaseSpec
from .doe import plan_doe
from .workflow_space import effective_case
from .optimization_gate import (
    campaign_gate,
    new_manifest,
    record_run,
    write_manifest,
    write_json_atomic,
)

STAGES = ("definition", "baseline", "screening", "search", "acceptance", "delivery")
ROUTES = {"standard", "structural_search", "exploratory"}
OPTIMIZERS = {"nsga2", "nsga3", "none"}
SCREENING_STRATEGIES = {"lhs", "grid"}


class WorkflowError(ValueError):
    pass


class WorkflowGateError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic(path: Path, payload: Mapping[str, Any]) -> None:
    write_json_atomic(path, dict(payload))


def _serialized(method):
    """One writer per campaign, including the time spent outside Python in the solver."""
    @wraps(method)
    def invoke(self, *args, **kwargs):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.state_path.with_suffix(".lock.sqlite")
        with closing(sqlite3.connect(lock, timeout=0)) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                raise WorkflowGateError(f"cannot acquire workflow lock: {exc}") from exc
            self.state = self._load_state()
            if self.state.get("in_flight"):
                raise WorkflowGateError(f"interrupted {self.state['in_flight']} stage: reconcile its submitted runs before retry")
            return method(self, *args, **kwargs)
    return invoke


@dataclass
class OptimizationPlan:
    case_id: str
    route: str = "structural_search"
    stage: str = "definition"
    strategy: dict[str, str] = field(default_factory=lambda: {
        "screening": "lhs",
        "optimizer": "nsga2",
        "verification": "full_model",
    })
    budget: dict[str, int] = field(default_factory=lambda: {
        "screening_candidates": 8,
        "optimizer_evaluations": 24,
        "full_model_runs": 4,
    })
    parameter_space: dict[str, dict[str, Any]] = field(default_factory=dict)
    objectives: list[dict[str, Any]] = field(default_factory=list)
    constraints: list[dict[str, Any]] = field(default_factory=list)
    required_artifacts: dict[str, list[str]] = field(default_factory=lambda: {
        "baseline": ["baseline_run_id"],
        "screening": ["campaign_dir", "manifest", "gate"],
        "search": ["archive", "manifest", "gate"],
        "acceptance": ["acceptance_report"],
        "delivery": ["report"],
    })
    notes: str = ""
    schema_version: int = 1
    verification_profile: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OptimizationPlan":
        known = {field.name for field in dataclasses.fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            raise WorkflowError("unknown plan fields: %s" % ", ".join(unknown))
        return cls(**dict(data))

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def validate(self, case: CaseSpec | None = None) -> "OptimizationPlan":
        if self.schema_version != 1:
            raise WorkflowError("unsupported plan schema_version %r" % self.schema_version)
        if not self.case_id:
            raise WorkflowError("case_id is required")
        if self.route not in ROUTES:
            raise WorkflowError("route must be one of %s" % sorted(ROUTES))
        if self.stage not in STAGES:
            raise WorkflowError("stage must be one of %s" % list(STAGES))
        if not isinstance(self.strategy, dict):
            raise WorkflowError("strategy must be an object")
        if str(self.strategy.get("screening") or "") not in SCREENING_STRATEGIES:
            raise WorkflowError("strategy.screening must be one of %s" % sorted(SCREENING_STRATEGIES))
        if str(self.strategy.get("optimizer") or "") not in OPTIMIZERS:
            raise WorkflowError("strategy.optimizer must be one of %s" % sorted(OPTIMIZERS))
        if not isinstance(self.budget, dict) or set(self.budget) - {"screening_candidates", "optimizer_evaluations", "full_model_runs"}:
            raise WorkflowError("unknown or invalid plan budget")
        for key, value in self.budget.items():
            if type(value) is not int or value < 0:
                raise WorkflowError("budget.%s must be a non-negative integer" % key)
        if not isinstance(self.parameter_space, dict):
            raise WorkflowError("parameter_space must be an object")
        if not isinstance(self.objectives, list) or not isinstance(self.constraints, list):
            raise WorkflowError("objectives and constraints must be lists")
        if (not isinstance(self.required_artifacts, dict) or set(self.required_artifacts) - set(STAGES[1:])
                or any(not isinstance(names, list) or not all(isinstance(n, str) and n for n in names)
                       for names in self.required_artifacts.values())):
            raise WorkflowError("required_artifacts must map stages to lists of artifact names")
        if case is not None:
            if case.case_id != self.case_id:
                raise WorkflowError("plan case_id %r != case %r" % (self.case_id, case.case_id))
            try:
                effective_case(case, self)
                if self.verification_profile:
                    from .workflow_evidence import validate_verification
                    validate_verification(self.verification_profile, case)
            except (ValueError, TypeError) as exc:
                raise WorkflowError(str(exc)) from exc
        return self


def load_plan(path: str | Path, case: CaseSpec | None = None) -> OptimizationPlan:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WorkflowError("cannot read optimization plan: %s" % exc) from exc
    if not isinstance(data, dict):
        raise WorkflowError("optimization plan must be a JSON object")
    return OptimizationPlan.from_dict(data).validate(case)


def save_plan(path: str | Path, plan: OptimizationPlan) -> Path:
    target = Path(path)
    _atomic(target, plan.to_dict())
    return target


class WorkflowRunner:
    """Own the stage state machine for one case/campaign."""

    def __init__(
        self,
        case: CaseSpec,
        config: ApprovedBuilderConfig,
        plan: OptimizationPlan | Mapping[str, Any],
        *,
        bridge: Any = None,
        state_path: str | Path | None = None,
        native_checks: Mapping[str, Any] | None = None,
    ):
        self.config = config
        self.plan = plan if isinstance(plan, OptimizationPlan) else OptimizationPlan.from_dict(plan)
        self.plan.validate(case)
        self.case = effective_case(case, self.plan)
        self.bridge = bridge
        self.native_checks = dict(native_checks or {})
        self.state_path = Path(state_path or Path(config.campaign_dir) / "optimization_workflow.json")
        self.state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if self.state_path.is_file():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise WorkflowError("existing workflow state is unreadable") from exc
            if not isinstance(data, dict) or not isinstance(data.get("artifacts"), dict):
                raise WorkflowError("invalid existing workflow state")
            saved_plan = OptimizationPlan.from_dict(data.get("plan") or {})
            if json.dumps(saved_plan.to_dict(), sort_keys=True) != json.dumps(self.plan.to_dict(), sort_keys=True):
                raise WorkflowError("existing workflow uses a different plan; use a new campaign revision")
            if data.get("binding") is not None and data["binding"] != self._binding():
                raise WorkflowError("campaign configuration differs from the saved workflow")
            return data
        return {
            "plan": self.plan.to_dict(),
            "stage": "definition",
            "started_at": _now(),
            "updated_at": _now(),
            "artifacts": {},
            "history": [],
            "deviations": [],
        }

    def _save(self) -> None:
        self.state["binding"] = self._binding()
        self.state["plan"] = self.plan.to_dict()
        self.state["updated_at"] = _now()
        _atomic(self.state_path, self.state)

    def _binding(self):
        return {"campaign_id": self.config.campaign_id, "builder_id": self.config.builder_id,
                "project": str(Path(self.config.project_path).resolve()), "design": self.config.design,
                "param_map": self.config.param_map, "outdir": str(Path(self.config.outdir_root).resolve()),
                "spec": hashlib.sha256(json.dumps(self.case.to_spec(), sort_keys=True).encode()).hexdigest()}

    def _start_solve_stage(self, name):
        if self.state["artifacts"].get(name):
            raise WorkflowGateError(f"{name} has already run in this campaign")
        self.state["in_flight"] = name
        self._save()

    def _require(self, key: str) -> Any:
        value = (self.state.get("artifacts") or {}).get(key)
        if not value:
            raise WorkflowGateError("stage gate: missing artifact %r" % key)
        for name in self.plan.required_artifacts.get(key, []):
            if not self._has_artifact(key, name, value):
                raise WorkflowGateError(f"stage gate: missing {key} artifact {name}")
        return value

    @_serialized
    def record_baseline(self, run_id: str, artifacts: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if not isinstance(run_id, str) or not run_id.strip():
            raise WorkflowError("baseline run_id is required")
        if self.state["artifacts"].get("screening"):
            raise WorkflowGateError("cannot replace baseline after screening")
        self.state["artifacts"]["baseline"] = {
            "run_id": run_id,
            "artifacts": dict(artifacts or {}),
            "recorded_at": _now(),
        }
        self.state["stage"] = "baseline"
        self.state["history"].append({"stage": "baseline", "run_id": run_id, "at": _now()})
        self._save()
        return self.state["artifacts"]["baseline"]

    @_serialized
    def deviation(self, reason: str, *, stage: str | None = None) -> None:
        if not reason.strip():
            raise WorkflowError("deviation reason is required")
        self.state["deviations"].append({"stage": stage or self.state["stage"], "reason": reason, "at": _now()})
        self._save()

    def _grid_points(self, *, max_candidates: int = 64) -> list[dict[str, Any]]:
        """Small deterministic lattice from plan.parameter_space or case bounds."""
        params = list(self.case.family.param_space)
        sizes, steps = [], []
        for param in params:
            if param.kind == "choice":
                sizes.append(len(param.choices))
                steps.append(None)
                continue
            step = self.plan.parameter_space.get(param.name, {}).get("step")
            if step is None:
                step = 1 if param.kind == "int" else (param.integer_step or (param.high - param.low) / 2)
            sizes.append(1 + math.floor((param.high - param.low) / step + 1e-10))
            steps.append(step)
        # A short prefix of a Cartesian product leaves the leading dimensions
        # fixed. Address spaced lattice indices directly without constructing
        # all combinations (and retain the ordinary grid order when it fits).
        total = math.prod(sizes)
        take = min(total, max_candidates)
        axes = list(zip(params, sizes, steps))
        points = []
        for i in range(take):
            index = i * (total - 1) // max(1, take - 1)
            point = {}
            for param, size, step in reversed(axes):
                index, offset = divmod(index, size)
                value = (param.choices[offset] if step is None else
                         min(param.high, round(param.low + offset * step, 12)))
                point[param.name] = param.validate(value)
            points.append(point)
        return points

    @_serialized
    def run_screening(
        self,
        *,
        candidates: Iterable[Mapping[str, Any]] | None = None,
        seed: int | None = None,
    ) -> dict[str, Any]:
        self._require("baseline")
        count = self.plan.budget.get("screening_candidates", 8)
        if not count:
            raise WorkflowError("screening disabled by zero candidate budget")
        if candidates is None:
            strategy = self.plan.strategy.get("screening", "lhs")
            if strategy == "lhs":
                planned = plan_doe(
                    self.case.family.param_space,
                    self.case.case_id,
                    count=max(1, count),
                    seed=0 if seed is None else int(seed),
                )
                points = [dict(point) for point in planned.points]
            else:
                points = self._grid_points(max_candidates=max(1, count))
        else:
            points = []
            for point in islice(candidates, count + 1):
                if len(points) == count:
                    raise WorkflowError("explicit candidates exceed screening budget")
                clean = self.case.family.param_space.validate(dict(point))
                if clean != point:
                    raise WorkflowError("explicit candidate contains a non-canonical parameter")
                points.append(clean)
        if not points:
            raise WorkflowError("screening produced no candidates")
        screening_config = replace(
            self.config,
            campaign_dir=str(Path(self.config.campaign_dir) / "screening"),
            outdir_root=str(Path(self.config.outdir_root) / "screening"),
        )
        self._start_solve_stage("screening")
        result = run_approved_campaign(
            self.case,
            screening_config,
            budget=len(points),
            candidates=points,
            bridge=self.bridge,
            require_acceptance=False,
        )
        artifact = {
            "campaign_dir": str(screening_config.campaign_dir),
            "manifest": str(Path(screening_config.campaign_dir) / "optimization_manifest.json"),
            "gate": str(Path(screening_config.campaign_dir) / "optimization_gate.json"),
            "candidates": points,
            "result": {
                "gate": result.get("gate"),
                "status": (result.get("manifest") or {}).get("status"),
            },
            "recorded_at": _now(),
        }
        manifest = result.get("manifest") or {}
        # Re-evaluate promising geometries in the separate search campaign;
        # never credit screening observations as new search measurements.
        seeds = [r["params"] for r in manifest.get("pareto", []) if
                 r.get("status") == "COMPLETED" and r.get("feasible") is True and r.get("run_id")]
        if not seeds:
            measured = [r for r in manifest.get("runs", []) if r.get("status") == "COMPLETED"
                        and r.get("run_id") and all(isinstance(r["metrics"].get(m.kind), (float, int))
                        and math.isfinite(r["metrics"][m.kind]) for m in [*self.case.constraints, *self.case.objectives])]
            measured.sort(key=lambda r: sum(r.get("violations", {}).values()))
            seeds = [r["params"] for r in measured]
        artifact["search_seeds"] = seeds
        self.state.pop("in_flight", None)
        self.state["artifacts"]["screening"] = artifact
        self.state["stage"] = "screening"
        self.state["history"].append({"stage": "screening", "candidates": len(points), "at": _now()})
        self._save()
        return artifact

    @_serialized
    def run_search(
        self,
        *,
        pop_size: int | None = None,
        generations: int | None = None,
        seed: int | None = None,
    ) -> dict[str, Any]:
        screening = self._require("screening")
        optimizer = str(self.plan.strategy.get("optimizer") or "nsga2").lower()
        budget = self.plan.budget.get("optimizer_evaluations", 24)
        if not budget or optimizer == "none":
            raise WorkflowError("search disabled by plan optimizer or zero evaluation budget")
        if not self._process_gate("screening"):
            raise WorkflowGateError("screening has no verified completed measurement; inspect its outcome")
        population = 8 if pop_size is None else pop_size
        rounds = 4 if generations is None else generations
        if type(population) is not int or population < 2 or type(rounds) is not int or rounds < 1:
            raise WorkflowError("NSGA requires population >= 2 and generations >= 1")
        if optimizer not in {"nsga2", "nsga3"}:
            raise WorkflowError("search optimizer %r is not a runner optimizer" % optimizer)
        try:
            # The wrappers defer pymoo imports until called. Resolve the
            # selected backend before recording any possible solver submission.
            import_module(f"pymoo.algorithms.moo.{optimizer}")
            import_module("pymoo.optimize")
            from .optimizer.nsga2 import run_nsga2
            from .optimizer.nsga3 import run_nsga3
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise WorkflowError("pymoo optimizer modules are unavailable: %s" % exc) from exc
        search_config = replace(
            self.config,
            campaign_dir=str(Path(self.config.campaign_dir) / "search"),
            outdir_root=str(Path(self.config.outdir_root) / "search"),
        )
        executor = ApprovedBuilderExecutor(self.case, search_config, bridge=self.bridge)
        runner = run_nsga2 if optimizer == "nsga2" else run_nsga3
        try:
            self._start_solve_stage("search")
            result = runner(
                self.case,
                executor,
                pop_size=population,
                generations=rounds,
                initial_params=screening.get("search_seeds", []),
                seed=0 if seed is None else int(seed),
                budget=budget,
            )
        except RuntimeError as exc:
            raise WorkflowError(str(exc)) from exc
        campaign_dir = Path(search_config.campaign_dir)
        campaign_dir.mkdir(parents=True, exist_ok=True)
        manifest = new_manifest(
            campaign_id=search_config.campaign_id,
            optimizer="aster",
            case_id=self.case.case_id,
            builder_id=search_config.builder_id,
            campaign_dir=campaign_dir,
            source="aster",
        )
        entries = list(result.get("entries") or [])
        for index, entry in enumerate(entries):
            row = dataclasses.asdict(entry) if dataclasses.is_dataclass(entry) else dict(entry)
            record_run(manifest, row)
        front = [dataclasses.asdict(entry) for entry in self._front(entries)]
        manifest["pareto"] = front
        manifest["acceptance"] = {
            "passed": bool(any(getattr(entry, "feasible", False) for entry in entries)),
            "source": "local_constraints",
            "feasible_runs": sum(1 for entry in entries if getattr(entry, "feasible", False)),
            "total_runs": len(entries),
        }
        manifest["status"] = "completed" if manifest["acceptance"]["passed"] else "failed"
        write_manifest(campaign_dir, manifest)
        gate = campaign_gate(
            campaign_dir,
            require_optimizer=True,
            min_completed_runs=1,
            require_pareto=False,
            require_acceptance=False,
        )
        manifest["gate"] = gate
        write_manifest(campaign_dir, manifest)
        archive = campaign_dir / "nsga_archive.json"
        _atomic(archive, {
            "optimizer": optimizer,
            "evaluations": result.get("evaluations"),
            "feasible": result.get("feasible"),
            "budget_exhausted": result.get("budget_exhausted"),
            "entries": [dataclasses.asdict(entry) if dataclasses.is_dataclass(entry) else dict(entry) for entry in entries],
        })
        artifact = {
            "campaign_dir": str(campaign_dir),
            "archive": str(archive),
            "manifest": str(campaign_dir / "optimization_manifest.json"),
            "gate": str(campaign_dir / "optimization_gate.json"),
            "front_size": len(front),
            "evaluations": result.get("evaluations"),
            "recorded_at": _now(),
        }
        self.state.pop("in_flight", None)
        self.state["artifacts"]["search"] = artifact
        self.state["stage"] = "search"
        self.state["history"].append({"stage": "search", "optimizer": optimizer, "at": _now()})
        self._save()
        return artifact

    def _front(self, entries: Iterable[Any]) -> list[Any]:
        from .optimize import ParetoArchive

        archive = ParetoArchive(self.case.objectives)
        for entry in entries:
            archive.add(entry)
        return archive.feasible_front()

    def _replay(self, evidence):
        from .workflow_evidence import replay_selected
        if self.plan.strategy.get("optimizer") == "none":
            archive = self._require("screening")["manifest"]
        else:
            archive = self._require("search")["archive"]
        return replay_selected(self.case, self.plan.verification_profile, evidence["campaign_dir"],
                               evidence["run_id"], archive, self.native_checks)

    @_serialized
    def record_acceptance(self, report: Mapping[str, Any]) -> dict[str, Any]:
        self._require("screening" if self.plan.strategy.get("optimizer") == "none" else "search")
        if not isinstance(report, Mapping) or not report:
            raise WorkflowError("acceptance report is required")
        if "campaign_dir" in report and "run_id" in report:
            assessment = self._replay(report)
        elif report.get("passed") is True:
            raise WorkflowGateError("positive acceptance requires a declared full-model recipe and a guarded run")
        else:
            assessment = {"passed": False, "source": "diagnostic", "report": dict(report)}
        self.state["artifacts"].pop("delivery", None)
        self.state["artifacts"]["acceptance"] = assessment
        self.state["stage"] = "acceptance"
        self.state["history"].append({"stage": "acceptance", "at": _now()})
        self._save()
        return self.state["artifacts"]["acceptance"]

    @_serialized
    def record_delivery(self, report_path: str) -> dict[str, Any]:
        self._require("acceptance")
        if not Path(report_path).is_file():
            raise WorkflowError("delivery report does not exist: %s" % report_path)
        self.state["artifacts"]["delivery"] = {"report": str(report_path), "recorded_at": _now()}
        self.state["stage"] = "delivery"
        self.state["history"].append({"stage": "delivery", "report": str(report_path), "at": _now()})
        self._save()
        return self.state["artifacts"]["delivery"]

    def _has_artifact(self, stage, name, record):
        if name == "baseline_run_id" and stage == "baseline":
            return bool(record.get("run_id"))
        if name == "acceptance_report" and stage == "acceptance":
            return bool(record)
        value = record.get(name)
        if name in {"report", "manifest", "archive", "gate", "campaign_dir"}:
            if not isinstance(value, str) or not value:
                return False
            path = Path(value)
            return path.is_dir() if name == "campaign_dir" else path.is_file()
        return bool(value or record.get("artifacts", {}).get(name))

    def _process_gate(self, stage):
        record = self.state["artifacts"].get(stage) or {}
        try:
            gate = json.loads(Path(record["gate"]).read_text(encoding="utf-8"))
            manifest = json.loads(Path(record["manifest"]).read_text(encoding="utf-8"))
            return (gate["passed"] is True and gate["campaign_id"] == manifest["campaign_id"]
                    and manifest["case_id"] == self.case.case_id and manifest["builder_id"] == self.config.builder_id
                    and any(r.get("status") == "COMPLETED" for r in manifest["runs"]))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return False

    def gate(self) -> dict[str, Any]:
        self.state = self._load_state()
        artifacts = self.state["artifacts"]
        checks = {"plan": self.state_path.is_file()}
        for stage in STAGES[1:]:
            if stage == "search" and self.plan.strategy.get("optimizer") == "none":
                continue
            record = artifacts.get(stage) or {}
            checks[stage] = bool(record) and all(self._has_artifact(stage, name, record)
                for name in self.plan.required_artifacts.get(stage, []))
        for stage in ("screening", "search"):
            if artifacts.get(stage):
                checks[stage + "_gate"] = self._process_gate(stage)
        process_complete = all(checks.values()) and not self.state.get("in_flight")
        baseline = artifacts.get("baseline") or {}
        base_dir = (baseline.get("artifacts") or {}).get("campaign_dir")
        from .design_history import load_study_history
        from .workflow_evidence import verified_baseline
        assessment = artifacts.get("acceptance") or {}
        checks["baseline_measured"] = False
        checks["design_accepted"] = False
        evidence_error = None
        try:
            if base_dir:
                checks["baseline_measured"] = verified_baseline(load_study_history(base_dir, self.case), baseline["run_id"])
            if assessment.get("source") == "full_model_replay" and assessment.get("passed") is True:
                current = self._replay(assessment)
                checks["design_accepted"] = json.dumps(current, sort_keys=True) == json.dumps(assessment, sort_keys=True)
        except (OSError, sqlite3.Error, KeyError, TypeError, ValueError, WorkflowGateError) as exc:
            evidence_error = str(exc)
        return {
            "passed": bool(process_complete and all(checks.values())),
            "workflow_complete": bool(process_complete),
            "evidence_error": evidence_error,
            "in_flight": self.state.get("in_flight"),
            "checks": checks,
            "stage": self.state["stage"],
            "state_path": str(self.state_path),
            "deviations": list(self.state.get("deviations") or []),
        }
