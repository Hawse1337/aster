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
import json
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
from .optimization_gate import (
    campaign_gate,
    new_manifest,
    record_run,
    write_manifest,
)

STAGES = ("definition", "baseline", "screening", "search", "acceptance", "delivery")
ROUTES = {"standard", "structural_search", "exploratory"}
OPTIMIZERS = {"nsga2", "nsga3", "lhs", "grid", "none"}
SCREENING_STRATEGIES = {"lhs", "grid"}


class WorkflowError(ValueError):
    pass


class WorkflowGateError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


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
        for key, value in self.budget.items():
            if not isinstance(value, int) or value < 0:
                raise WorkflowError("budget.%s must be a non-negative integer" % key)
        if self.parameter_space and not isinstance(self.parameter_space, dict):
            raise WorkflowError("parameter_space must be an object")
        if case is not None:
            if case.case_id != self.case_id:
                raise WorkflowError("plan case_id %r != case %r" % (self.case_id, case.case_id))
            names = {param.name for param in case.family.param_space}
            declared = set(self.parameter_space)
            if declared and declared != names:
                raise WorkflowError(
                    "plan parameter_space does not match case parameters: missing=%s extra=%s"
                    % (sorted(names - declared), sorted(declared - names))
                )
            expected_objectives = {objective.kind for objective in case.objectives}
            declared_objectives = {str(item.get("kind")) for item in self.objectives if isinstance(item, dict)}
            if declared_objectives and not declared_objectives.issubset(expected_objectives):
                raise WorkflowError("plan objectives contain unknown kinds")
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
    ):
        self.case = case
        self.config = config
        self.plan = plan if isinstance(plan, OptimizationPlan) else OptimizationPlan.from_dict(plan)
        self.plan.validate(case)
        self.bridge = bridge
        self.state_path = Path(state_path or Path(config.campaign_dir) / "optimization_workflow.json")
        self.state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if self.state_path.is_file():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except (OSError, ValueError):
                pass
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
        self.state["plan"] = self.plan.to_dict()
        self.state["updated_at"] = _now()
        _atomic(self.state_path, self.state)

    def _require(self, key: str) -> Any:
        value = (self.state.get("artifacts") or {}).get(key)
        if not value:
            raise WorkflowGateError("stage gate: missing artifact %r" % key)
        return value

    def record_baseline(self, run_id: str, artifacts: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if not isinstance(run_id, str) or not run_id.strip():
            raise WorkflowError("baseline run_id is required")
        self.state["artifacts"]["baseline"] = {
            "run_id": run_id,
            "artifacts": dict(artifacts or {}),
            "recorded_at": _now(),
        }
        self.state["stage"] = "baseline"
        self.state["history"].append({"stage": "baseline", "run_id": run_id, "at": _now()})
        self._save()
        return self.state["artifacts"]["baseline"]

    def deviation(self, reason: str, *, stage: str | None = None) -> None:
        if not reason.strip():
            raise WorkflowError("deviation reason is required")
        self.state["deviations"].append({"stage": stage or self.state["stage"], "reason": reason, "at": _now()})
        self._save()

    def _grid_points(self, *, max_candidates: int = 64) -> list[dict[str, Any]]:
        """Small deterministic lattice from plan.parameter_space or case bounds."""
        specs: dict[str, Mapping[str, Any]] = {
            param.name: {
                "low": param.low,
                "high": param.high,
                "step": getattr(param, "integer_step", None),
                "default": param.default,
            }
            for param in self.case.family.param_space
        }
        for name, spec in (self.plan.parameter_space or {}).items():
            if name in specs and isinstance(spec, Mapping):
                specs[name] = {**specs[name], **dict(spec)}
        axes: list[list[Any]] = []
        for param in self.case.family.param_space:
            spec = specs.get(param.name) or {}
            low = spec.get("low", param.low)
            high = spec.get("high", param.high)
            step = spec.get("step") or getattr(param, "integer_step", None)
            default = spec.get("default", param.default)
            if low is None or high is None:
                axes.append([default])
                continue
            step_value = float(step) if step else max((float(high) - float(low)) / 2.0, 1e-9)
            values: list[Any] = []
            cursor = float(low)
            while cursor <= float(high) + 1e-9 and len(values) < 64:
                values.append(round(cursor, 9))
                cursor += step_value
            axes.append(values or [default])
        points: list[dict[str, Any]] = []
        def _walk(index: int, current: dict[str, Any]) -> None:
            if len(points) >= max_candidates:
                return
            if index == len(self.case.family.param_space.params):
                points.append(dict(current))
                return
            param = self.case.family.param_space.params[index]
            for value in axes[index]:
                current[param.name] = value
                _walk(index + 1, current)
        _walk(0, {})
        return points or [dict(self.case.family.param_space.default())]

    def run_screening(
        self,
        *,
        candidates: Iterable[Mapping[str, Any]] | None = None,
        seed: int | None = None,
    ) -> dict[str, Any]:
        self._require("baseline")
        count = int(self.plan.budget.get("screening_candidates") or 8)
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
            points = [dict(point) for point in candidates]
        if not points:
            raise WorkflowError("screening produced no candidates")
        screening_config = replace(
            self.config,
            campaign_dir=str(Path(self.config.campaign_dir) / "screening"),
            outdir_root=str(Path(self.config.outdir_root) / "screening"),
        )
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
        self.state["artifacts"]["screening"] = artifact
        self.state["stage"] = "screening"
        self.state["history"].append({"stage": "screening", "candidates": len(points), "at": _now()})
        self._save()
        return artifact

    def run_search(
        self,
        *,
        pop_size: int | None = None,
        generations: int | None = None,
        seed: int | None = None,
    ) -> dict[str, Any]:
        self._require("screening")
        optimizer = str(self.plan.strategy.get("optimizer") or "nsga2").lower()
        budget = int(self.plan.budget.get("optimizer_evaluations") or 24)
        if optimizer not in {"nsga2", "nsga3"}:
            raise WorkflowError("search optimizer %r is not a runner optimizer" % optimizer)
        try:
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
            result = runner(
                self.case,
                executor,
                pop_size=int(pop_size or 8),
                generations=int(generations or 4),
                seed=0 if seed is None else int(seed),
                budget=budget,
            )
        except RuntimeError as exc:
            raise WorkflowError(str(exc)) from exc
        campaign_dir = Path(search_config.campaign_dir)
        campaign_dir.mkdir(parents=True, exist_ok=True)
        manifest = new_manifest(
            campaign_id="%s-search" % self.plan.case_id,
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
            require_pareto=True,
            require_acceptance=True,
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

    def record_acceptance(self, report: Mapping[str, Any]) -> dict[str, Any]:
        self._require("search")
        if not isinstance(report, Mapping) or not report:
            raise WorkflowError("acceptance report is required")
        self.state["artifacts"]["acceptance"] = dict(report)
        self.state["stage"] = "acceptance"
        self.state["history"].append({"stage": "acceptance", "at": _now()})
        self._save()
        return self.state["artifacts"]["acceptance"]

    def record_delivery(self, report_path: str) -> dict[str, Any]:
        self._require("acceptance")
        if not Path(report_path).is_file():
            raise WorkflowError("delivery report does not exist: %s" % report_path)
        self.state["artifacts"]["delivery"] = {"report": str(report_path), "recorded_at": _now()}
        self.state["stage"] = "delivery"
        self.state["history"].append({"stage": "delivery", "report": str(report_path), "at": _now()})
        self._save()
        return self.state["artifacts"]["delivery"]

    def gate(self) -> dict[str, Any]:
        checks = {
            "plan": self.state_path.is_file(),
            "baseline": bool(self.state["artifacts"].get("baseline")),
            "screening": bool(self.state["artifacts"].get("screening")),
            "search": bool(self.state["artifacts"].get("search")),
            "acceptance": bool(self.state["artifacts"].get("acceptance")),
            "delivery": bool(self.state["artifacts"].get("delivery")),
        }
        if checks["screening"]:
            checks["screening_gate"] = bool(
                (self.state["artifacts"]["screening"].get("result") or {}).get("gate", {}).get("passed")
            )
        if checks["search"]:
            checks["search_gate"] = bool(
                (self.state["artifacts"]["search"].get("gate") or {}).get("passed")
            )
        return {
            "passed": all(checks.values()),
            "checks": checks,
            "stage": self.state["stage"],
            "state_path": str(self.state_path),
            "deviations": list(self.state.get("deviations") or []),
        }
