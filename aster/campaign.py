"""Campaign runner: ties parameters, guarded runs, archive and decisions (S1–S6).

One call per candidate:

    validate rules -> apply parameters through the guarded broker ->
    submit a guarded run -> wait -> archive metrics -> record a decision

Everything is written to ``<campaign>/campaign_summary.json`` and the archive so
an interrupted session can explain what was measured and why.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .apply import ModelApplier
from .controller import ControllerContext, StrategyController, _context_dict
from .core import CaseSpec
from .executor import GuardConfig, GuardExecutor
from .optimize import Evaluation, ParetoArchive, hypervolume_2d
from .topology.triggers import should_search_topology


@dataclass
class CampaignRecord:
    candidate_id: str
    params: dict[str, Any]
    status: str
    feasible: bool
    run_id: str | None
    metrics: dict[str, float | None]
    violations: dict[str, float]
    applied: bool
    validated: bool
    error: str | None = None
    at_utc: str = ""
    origin: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "params": dict(self.params),
            "status": self.status,
            "feasible": self.feasible,
            "run_id": self.run_id,
            "metrics": dict(self.metrics),
            "violations": dict(self.violations),
            "applied": self.applied,
            "validated": self.validated,
            "error": self.error,
            "at_utc": self.at_utc,
            "origin": dict(self.origin),
        }


class CampaignRunner:
    def __init__(
        self,
        case: CaseSpec,
        applier: ModelApplier,
        config: GuardConfig,
        *,
        campaign_dir: str | Path,
        bridge: Any = None,
        controller: StrategyController | None = None,
        reference: tuple[float, float] | None = None,
        health_probe: Any = None,
        executor: Any = None,
    ):
        self.case = case
        self.applier = applier
        self.executor = executor
        self.config = config
        self.campaign_dir = Path(campaign_dir)
        self.bridge = bridge
        self.controller = controller or StrategyController()
        self.reference = reference
        self.health_probe = health_probe
        self.archive = ParetoArchive(case.objectives)
        self.records: list[CampaignRecord] = []
        self.hypervolumes: list[float] = []

    # ------------------------------------------------------------------ helpers
    def _prepare(self, candidate: Any) -> None:
        """GuardExecutor hook: apply parameters before the run is submitted."""
        result = self.applier.apply(candidate.params)
        candidate.metadata = getattr(candidate, "metadata", {})  # tolerate frozen dataclass
        self._last_apply = result
        if not result.ok or not result.validated:
            raise RuntimeError(f"model application failed: {result.error}")

    # -------------------------------------------------------------------- main
    def evaluate(self, params: dict[str, Any]) -> CampaignRecord:
        self._last_apply = None
        try:
            # Parameter validation is part of the recorded outcome: an impossible
            # candidate must show up as MODEL_FAILED, not crash the campaign.
            candidate = self.case.make_candidate(params, algorithm="campaign",
                                                 round=len(self.records))
            executor = self.executor or GuardExecutor(self.case, self.config, bridge=self.bridge)
            if self.executor is None:
                self.config.prepare = self._prepare
            entry = executor.evaluate(candidate)
            applied = self._last_apply is not None
            validated = bool(getattr(self._last_apply, "validated", False)) if applied else False
            record = CampaignRecord(
                candidate_id=candidate.candidate_id,
                params=dict(entry.params),
                status=entry.status,
                feasible=entry.feasible,
                run_id=entry.run_id,
                metrics=dict(entry.metrics),
                violations=dict(entry.violations),
                applied=applied,
                validated=validated,
                origin=dict(getattr(entry, "origin", None) or {}),
                at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
        except Exception as exc:  # noqa: BLE001 - a modelling failure must be recorded, not raised
            if self.health_probe is not None:
                healthy, detail = self._probe_environment()
                if not healthy:
                    raise EnvironmentUnhealthy(
                        "the AEDT session looks unhealthy after a failed candidate "
                        f"({detail}); restart AEDT before continuing: {exc}"
                    ) from exc
            failed_id = getattr(locals().get("candidate"), "candidate_id", None) or (
                "invalid-" + str(abs(hash(tuple(sorted(params.items())))) % (10 ** 12))
            )
            record = CampaignRecord(
                candidate_id=failed_id,
                params=dict(params),
                status="MODEL_FAILED",
                feasible=False,
                run_id=None,
                metrics={},
                violations={},
                applied=False,
                validated=False,
                error=str(exc)[:500],
                at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            entry = Evaluation(
                candidate_id=record.candidate_id,
                family_id=self.case.family.family_id,
                params=dict(params),
                metrics={},
                feasible=False,
                violations={},
                total_violation=1e3,
                run_id=None,
                status="MODEL_FAILED",
            )
        self._last_entry = entry
        self.archive.add(entry)
        self.records.append(record)
        if self.reference is not None and len(self.case.objectives) >= 2:
            self.hypervolumes.append(
                hypervolume_2d(self.archive.feasible(), self.case.objectives[0],
                               self.case.objectives[1], self.reference)
            )
        return record

    def _probe_environment(self) -> tuple[bool, str]:
        probe = self.health_probe
        if not callable(probe):
            return True, "no probe"
        try:
            outcome = probe()
        except Exception as exc:  # noqa: BLE001 - the probe itself can fail
            return False, f"probe raised {type(exc).__name__}: {str(exc)[:200]}"
        if isinstance(outcome, tuple):
            return bool(outcome[0]), str(outcome[1] if len(outcome) > 1 else "")
        return bool(outcome), "probe returned no detail"

    def measure(self, params: dict[str, Any]) -> Evaluation:
        """Evaluate (or reuse a recorded candidate) and return the Evaluation."""
        for record in self.records:
            if record.params == params and record.run_id and record.status == "COMPLETED":
                return Evaluation(
                    candidate_id=record.candidate_id,
                    family_id=self.case.family.family_id,
                    params=dict(record.params),
                    metrics=dict(record.metrics),
                    feasible=record.feasible,
                    violations=dict(record.violations),
                    total_violation=sum(record.violations.values()),
                    run_id=record.run_id,
                    status=record.status,
                )
        self.evaluate(params)
        return self._last_entry

    def sensitivity(self, *, metric_kinds: Sequence[str] | None = None,
                    params: Sequence[str] | None = None,
                    step_fraction: float = 0.1, budget: int | None = None,
                    evaluator: Any = None) -> Any:
        """Measure a Jacobian with an optional hard or already-shared budget.

        ``budget`` limits new runs, reusing completed records for free. A caller
        coordinating several actions may pass its shared BudgetedEvaluator.
        """
        from .physics.sensitivity import BudgetedEvaluator, compute_sensitivity  # noqa: PLC0415

        measured = [record for record in self.records if record.status == "COMPLETED" and record.metrics]
        base = dict(measured[-1].params) if measured else dict(
            self.case.family.param_space.default()
        )
        before_records = len(self.records)
        if evaluator is not None and budget is not None:
            raise ValueError("provide budget or a shared evaluator, not both")
        shared = evaluator is not None
        if evaluator is None:
            cache = {self.case.make_candidate(record.params).candidate_id: record for record in measured}
            evaluator = BudgetedEvaluator(self.case, self.evaluate, budget, cache)
        before_calls = getattr(evaluator, "calls", None)
        matrix = compute_sensitivity(self.case, evaluator, base,
                                     metric_kinds=metric_kinds, params=params,
                                     step_fraction=step_fraction)
        matrix.evaluations = (getattr(evaluator, "calls") - before_calls
                              if shared and before_calls is not None
                              else len(self.records) - before_records)
        return matrix

    # ---------------------------------------------------------------- reporting
    def context(self, *, stalled_rounds: int = 0) -> ControllerContext:
        latest = self.records[-1] if self.records else None
        # A failed or unmeasured run says nothing about feasibility: treat every
        # constraint as failing instead of claiming everything is satisfied.
        measured = (
            latest is not None
            and latest.status == "COMPLETED"
            and bool(latest.metrics)
        )
        if not measured:
            failing = [constraint.id for constraint in self.case.constraints]
        else:
            failing = sorted(latest.violations)
        passing = [c.id for c in self.case.constraints if c.id not in failing]
        return ControllerContext(
            failing_constraints=failing,
            passing_constraints=passing,
            stalled_rounds=stalled_rounds,
            hypervolume_history=list(self.hypervolumes),
            has_sensitivity=False,
            sensitivity_fixable=None,
            topology_worth_trying=False,
            budget_left=max(0, 50 - len(self.records)),
            surrogate_usable=False,
        )

    def decide(self, *, stalled_rounds: int = 0) -> dict[str, Any]:
        context = self.context(stalled_rounds=stalled_rounds)
        decision = self.controller.select(context)
        return decision.to_dict()

    def topology_signal(self) -> dict[str, Any]:
        ratios: dict[str, float] = {}
        for constraint in self.case.constraints:
            hits = sum(1 for record in self.records if constraint.id in record.violations)
            ratios[constraint.id] = hits / max(1, len(self.records))
        history = [{"feasible": sum(1 for r in self.records[: i + 1] if r.feasible)}
                   for i in range(len(self.records))]
        return should_search_topology(
            history=history,
            hypervolumes=self.hypervolumes,
            constraint_violation_ratios=ratios,
            sensitivity_fixable=None,
            budget_left=max(0, 50 - len(self.records)),
        )

    def summary(self) -> dict[str, Any]:
        by_id = {record.candidate_id: record for record in self.records}
        return {
            "case": self.case.case_id,
            "campaign_dir": str(self.campaign_dir),
            "candidates": len(self.records),
            "feasible": sum(1 for record in self.records if record.feasible),
            "records": [by_id[key].to_dict() for key in by_id],
            "hypervolumes": list(self.hypervolumes),
            "decisions": list(self.controller.log),
        }

    def save(self) -> Path:
        target = self.campaign_dir / "campaign_summary.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.summary(), indent=2, ensure_ascii=False, default=str),
                          encoding="utf-8")
        return target


class EnvironmentUnhealthy(RuntimeError):
    """The AEDT session cannot be trusted; continuing would only burn runs."""


class CampaignExecutor:
    """Expose a CampaignRunner as an optimiser executor (module A interface)."""

    def __init__(self, runner: CampaignRunner):
        self.runner = runner
        self.calls = 0

    def evaluate(self, candidate: Any) -> Evaluation:
        entry = self.runner.measure(dict(candidate.params))
        self.calls += 1
        return entry


def batch_params(case: CaseSpec, count: int, *, seed: int = 0,
                 algorithm: str = "lhs") -> list[dict[str, Any]]:
    from .core import latin_hypercube, sample_population  # noqa: PLC0415

    if algorithm == "random":
        return sample_population(case.family.param_space, count, seed=seed)
    return latin_hypercube(case.family.param_space, count, seed=seed)






