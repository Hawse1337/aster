"""Executors: how a candidate becomes measured metrics.

Two implementations:

* ``SyntheticExecutor`` — analytic problem, used by the offline acceptance tests;
* ``GuardExecutor`` — the only real path: it never imports PyAEDT, it submits
  through the MCP guarded-run tools, so every measurement carries the guard's
  evidence chain (preflight, token, save/solution check, convergence gate).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol, Sequence

from .core import Candidate, CaseSpec
from .optimize import Evaluation, evaluate_constraints

TERMINAL = {"COMPLETED", "FAILED", "BUDGET_EXCEEDED", "RECOVERY_REQUIRED"}


# Structural parameters change the cavity or the feed, so the adaptive mesh needs
# more passes than a single-parameter tweak. Measured evidence: a ring-2.2 sweep
# converges at 8 passes, while ring + disc thickness or ring + feed step failed
# with E_NOT_CONVERGED at 8 and needed 12.
STRUCTURAL_PARAMETERS = frozenset({
    "disc_factor", "disc_radial", "ring_height", "ring_shift",
    "ring2_outer", "ring2_height", "ring2_shift",
    "feed_radius", "feed_len", "feed_z0",
})


def setup_config_code(
    *,
    project_path: Any,
    design_name: str,
    setup_name: str,
    passes: int,
    max_delta_s: float = 0.02,
    band_ghz: tuple[float, float] = (24.0, 32.0),
    sweep_name: str = "ANT_Band",
    step_ghz: float = 1.0,
) -> str:
    """Code that configures a freshly copied project: passes, MaxDeltaS and the band sweep.

    Kept as a pure string builder so the GUI manager, the yadof session and the standalone
    scripts execute the same instructions - the differences between them are exactly where the
    InsertFrequencySweep failures came from. The solver settings come first: a sweep created on a
    setup that is re-tuned afterwards can be re-solved with the old pass budget.
    """
    low, high = band_ghz
    lines = [
        "from ansys.aedt.core import Hfss",
        "app = Hfss(project=%r, design=%r, new_desktop=False, close_on_exit=False, "
        "non_graphical=False, port=aedt_port)" % (str(project_path), design_name),
        "setup = app.get_setup(%r)" % setup_name,
        "setup.props['MaximumPasses'] = int(%d)" % int(passes),
        "setup.props['MaxDeltaS'] = float(%r)" % float(max_delta_s),
        "setup.update()",
        "for sweep in list(setup.sweeps):",
        "    setup.delete_sweep(sweep.name)",
        "setup.create_linear_step_sweep(unit='GHz', start_frequency=%r, "
        "stop_frequency=%r, step_size=%r, name=%r, sweep_type='Discrete', "
        "save_fields=False)" % (float(low), float(high), float(step_ghz), sweep_name),
        "result = {'passes': int(%d), 'sweeps': [s.name for s in setup.sweeps]}"
        % int(passes),
    ]
    return "\n".join(lines) + "\n"


def recommended_passes(params: dict[str, Any], baseline: dict[str, Any] | None = None,
                       *, single_step: int = 8, structural_step: int = 12) -> int:
    """Pass budget for a candidate: 8 for a knob tweak, 12 for a structural change."""
    if not baseline:
        return single_step
    for name, value in params.items():
        reference = baseline.get(name)
        if reference is None:
            # a structural parameter the baseline does not mention is treated as
            # changed (conservative: budget the structural pass count)
            if name in STRUCTURAL_PARAMETERS:
                return structural_step
            continue
        try:
            changed = abs(float(value) - float(reference)) > 1e-9
        except (TypeError, ValueError):
            changed = value != reference
        if changed and name in STRUCTURAL_PARAMETERS:
            return structural_step
    return single_step



class Executor(Protocol):
    def evaluate(self, candidate: Candidate) -> Evaluation: ...


class SyntheticExecutor:
    def __init__(self, case: CaseSpec, problem: Any):
        self.case = case
        self.problem = problem

    def evaluate(self, candidate: Candidate) -> Evaluation:
        metrics = self.problem.metrics(candidate.params)
        feasible, violations, total = evaluate_constraints(metrics, self.case)
        return Evaluation(
            candidate_id=candidate.candidate_id,
            family_id=candidate.family_id,
            params=dict(candidate.params),
            metrics=metrics,
            feasible=feasible,
            violations=violations,
            total_violation=total,
            status="COMPLETED",
            origin=dict(candidate.origin),
        )


@dataclass
class GuardConfig:
    campaign_dir: str
    project_name: str
    design_name: str
    setup_name: str = "Setup1"
    cuts: Sequence[str] = ("theta_phi0", "phi_theta90")
    frequencies_ghz: Sequence[float] | None = None
    sweep_name: str | None = None
    farfield_setup_name: str | None = None
    extra_setups: Sequence[str] = ()
    pid: int | None = None
    port: int | None = None
    num_cores: int | None = None
    poll_interval_s: float = 5.0
    timeout_s: float = 1800.0
    prepare: Callable[[Candidate], Any] | None = None


class GuardExecutor:
    current_run_id: str | None = None
    """The run being waited on, if any; the stall watcher reads the guard status through it."""
    """Submits candidates through a guard bridge (never imports PyAEDT)."""

    def __init__(self, case: CaseSpec, config: GuardConfig, bridge: Any = None):
        self.case = case
        self.config = config
        if (config.pid is None) == (config.port is None):
            raise ValueError("GuardConfig needs exactly one explicit AEDT pid or grpc port")
        if bridge is None:
            from .bridge import InProcessBridge  # noqa: PLC0415

            bridge = InProcessBridge()
        self.bridge = bridge
        self._kind_by_id = {
            metric["id"]: metric["kind"] for metric in case.to_spec()["metrics"]
        }

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _with_radiation_efficiency(
        metrics: dict[str, float | None],
    ) -> dict[str, float | None]:
        """Add the radiation efficiency when the far-field export allows computing it."""
        from .physics.radiation import (EFFICIENCY_METRIC,  # noqa: PLC0415
                                        radiation_efficiency_metric)

        if metrics.get(EFFICIENCY_METRIC) is not None:
            return metrics
        value = radiation_efficiency_metric(metrics)
        if value is None:
            return metrics
        enriched = dict(metrics)
        enriched[EFFICIENCY_METRIC] = value
        return enriched

    def _metrics_by_kind(self, metrics: dict[str, Any]) -> dict[str, float | None]:
        kind_values: dict[str, float | None] = {}
        for metric_id, payload in (metrics or {}).items():
            kind = self._kind_by_id.get(metric_id, metric_id)
            value = payload.get("value") if isinstance(payload, dict) else payload
            kind_values[kind] = None if value is None else float(value)
        return kind_values

    def _wait(self, run_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + float(self.config.timeout_s) + 600.0
        status: dict[str, Any] = {}
        while time.monotonic() < deadline:
            status = self.bridge.status(self.config.campaign_dir, run_id)
            if status.get("terminal"):
                return status
            time.sleep(max(0.01, float(self.config.poll_interval_s)))
        return status

    # ---------------------------------------------------------------- evaluate
    def evaluate(self, candidate: Candidate) -> Evaluation:
        if self.config.prepare is not None:
            self.config.prepare(candidate)
        submitted = self.bridge.submit(
            campaign_dir=self.config.campaign_dir,
            project_name=self.config.project_name,
            design_name=self.config.design_name,
            setup_name=self.config.setup_name,
            params=dict(candidate.params),
            cuts=list(self.config.cuts),
            frequencies_ghz=list(self.config.frequencies_ghz) if self.config.frequencies_ghz else None,
            sweep_name=self.config.sweep_name,
            farfield_setup_name=self.config.farfield_setup_name,
            extra_setups=list(self.config.extra_setups) or None,
            pid=self.config.pid,
            port=self.config.port,
            num_cores=self.config.num_cores,
            poll_interval_s=self.config.poll_interval_s,
            timeout_s=self.config.timeout_s,
        )
        if submitted.get("cached") and submitted.get("status") == "COMPLETED":
            metrics = self._metrics_by_kind(submitted.get("metrics") or {})
            status = "COMPLETED"
            run_id = submitted.get("run_id")
            checks = submitted.get("metric_checks")
            if checks is None:
                # Older servers only include scalar values in cache hits.
                final = self.bridge.status(self.config.campaign_dir, run_id) if run_id else {}
                details = final.get("metrics", {}) if (
                    final.get("run_id") == run_id and final.get("status") == "COMPLETED") else {}
                checks = {name: row.get("passed") for name, row in details.items() if isinstance(row, dict)}
        else:
            run_id = submitted.get("run_id")
            # expose the id while the run is still going: the stall watcher polls the guard status,
            # and until now the id only existed once the run had finished
            self.current_run_id = run_id
            try:
                final = self._wait(run_id) if run_id else {}
            finally:
                self.current_run_id = None
            status = str(final.get("status") or "FAILED")
            details = final.get("metrics") or {}
            metrics = self._metrics_by_kind(details)
            checks = {name: row.get("passed") for name, row in details.items() if isinstance(row, dict)}
        metrics = self._with_radiation_efficiency(metrics)
        _, violations, _ = evaluate_constraints(metrics, self.case)
        if status == "COMPLETED":
            for constraint in self.case.constraints:
                # A summary can satisfy the target while another required
                # frequency failed. Both the guard and local checks must pass.
                if checks.get(constraint.id) not in (True, 1):
                    violations.setdefault(constraint.id, 1000.0)
        return Evaluation(
            candidate_id=candidate.candidate_id,
            family_id=candidate.family_id,
            params=dict(candidate.params),
            metrics=metrics,
            feasible=status == "COMPLETED" and not violations,
            violations=violations,
            total_violation=sum(violations.values()),
            run_id=run_id,
            status=status,
            origin=dict(candidate.origin),
        )


def run_campaign(
    case: CaseSpec,
    executor: Executor,
    *,
    budget: int,
    batch: int = 4,
    seed: int = 0,
    strategy: Callable[..., list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Minimal loop: propose → evaluate → archive. Strategies plug in later."""
    from .core import latin_hypercube
    from .optimize import ParetoArchive

    if type(budget) is not int or budget < 0:
        raise ValueError("budget must be a nonnegative integer")
    if type(batch) is not int or batch < 1:
        raise ValueError("batch must be a positive integer")
    archive = ParetoArchive(case.objectives)
    evaluated = 0
    round_index = 0
    history: list[dict[str, Any]] = []
    sampler = strategy or (lambda case, n, seed: latin_hypercube(case.family.param_space, n, seed=seed))
    while evaluated < budget:
        count = min(batch, budget - evaluated)
        params_list = sampler(case, count, seed + round_index)
        if not 1 <= len(params_list) <= count:
            raise ValueError(f"strategy must return 1 to {count} candidates this round")
        for params in params_list:
            candidate = case.make_candidate(params, algorithm="lhs", round=round_index)
            entry = executor.evaluate(candidate)
            archive.add(entry)
            evaluated += 1
        history.append(
            {
                "round": round_index,
                "evaluated": evaluated,
                "feasible": len(archive.feasible()),
                "front": len(archive.feasible_front()),
                "best_gain": (
                    archive.best_by("realized_gain_h", maximize=True).metrics.get("realized_gain_h")
                    if archive.best_by("realized_gain_h", maximize=True)
                    else None
                ),
            }
        )
        round_index += 1
    summary = archive.improvement_summary()
    summary.update({"case": case.case_id, "history": history, "archive": archive})
    return summary


