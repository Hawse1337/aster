"""Pareto export over the campaign history, shared by the GUI endpoint and scripts.

The archive is rebuilt from the measured runs (never from predictions), so the exported front
only ever contains candidates that a real guarded run produced.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from .core import CaseSpec
from .export import front_rows, progress_rows, write_front_csv, write_front_json
from .optimize import Evaluation, ParetoArchive, evaluate_constraints
from .learning_contract import finite_metrics, stored_contract
from .recommend import completed_runs

__all__ = ["archive_from_runs", "export_pareto"]


def archive_from_runs(case: CaseSpec, runs: Sequence[dict[str, Any]]) -> ParetoArchive:
    """Rebuild a Pareto archive from campaign run records."""
    archive = ParetoArchive(case.objectives)
    kinds = {metric["id"]: metric["kind"] for metric in case.to_spec()["metrics"]}
    for row in completed_runs(runs):
        contract = stored_contract(row)
        measured_case = row.get("case") or row.get("case_name") or contract.get("case_id")
        if measured_case != case.case_id:
            continue
        run_id = row.get("run_id")
        if not run_id:
            continue
        # The GUI sends scalar metric kinds. The guard can supply metric IDs
        # with per-band pass flags, whose failure a favourable scalar can hide.
        values = {}
        checks = {}
        for name, value in (row.get("metrics") or {}).items():
            if isinstance(value, dict):
                if "passed" in value:
                    checks[name] = value["passed"]
                value = value.get("value")
            values[kinds.get(name, name)] = value
        metrics = finite_metrics(values)
        if not any(value is not None for value in metrics.values()):
            continue
        _, violations, _ = evaluate_constraints(metrics, case)
        recorded = finite_metrics(row.get("violations") or {})
        for name, severity in recorded.items():
            if severity is not None and severity > 0:
                violations[name] = max(severity, violations.get(name, 0.0))
        # Treat each available verdict independently. A later positive verdict
        # cannot cancel an explicit failure from a different evidence source.
        for verdicts in (checks, row.get("metric_checks"), row.get("constraint_pass")):
            if isinstance(verdicts, dict):
                for constraint in case.constraints:
                    if any(verdicts.get(key) in (False, 0)
                           for key in (constraint.id, constraint.kind)):
                        violations.setdefault(constraint.id, 1000.0)
        feasible = (not violations and row.get("feasible") not in (False, 0)
                    and row.get("constraint_pass") not in (False, 0)
                    and all(metrics.get(o.kind) is not None for o in case.objectives))
        total = sum(violations.values())
        archive.add(
            Evaluation(
                candidate_id=str(row.get("run_id")),
                family_id=str(row.get("family_id") or case.case_id),
                params=dict(row.get("params") or {}),
                metrics=metrics,
                feasible=feasible,
                violations=violations,
                total_violation=total,
                run_id=row.get("run_id"),
                status=str(row.get("status")),
            )
        )
    return archive


def export_pareto(
    case: CaseSpec,
    runs: Sequence[dict[str, Any]],
    target: str | Path,
    *,
    fmt: str = "csv",
) -> dict[str, Any]:
    """Write the front to ``target`` and return a summary of what was written."""
    archive = archive_from_runs(case, runs)
    rows = front_rows(archive)
    progress = progress_rows(
        [{"round": index + 1, "evaluated": entry.run_id}
         for index, entry in enumerate(archive.entries.values())]
    )
    meta = {"case": case.case_id, "runs": len(archive), "feasible": len(archive.feasible())}
    if fmt == "json":
        path = write_front_json(target, rows, progress=progress, meta=meta)
    else:
        path = write_front_csv(target, rows)
    return {
        "path": str(path),
        "format": "json" if fmt == "json" else "csv",
        "front_size": len(rows),
        "evaluated": len(archive),
        "feasible": len(archive.feasible()),
    }
