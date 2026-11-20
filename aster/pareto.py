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

__all__ = ["archive_from_runs", "export_pareto"]


def archive_from_runs(case: CaseSpec, runs: Sequence[dict[str, Any]]) -> ParetoArchive:
    """Rebuild a Pareto archive from campaign run records."""
    archive = ParetoArchive(case.objectives)
    for row in runs:
        contract = stored_contract(row)
        measured_case = row.get("case") or row.get("case_name") or contract.get("case_id")
        if measured_case != case.case_id:
            continue
        if str(row.get("status")) != "COMPLETED":
            continue
        metrics = finite_metrics(row.get("metrics") or {})
        if not metrics:
            continue
        feasible, violations, total = evaluate_constraints(metrics, case)
        if any(metrics.get(objective.kind) is None for objective in case.objectives):
            feasible = False
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
        [{"round": index + 1, "evaluated": row.get("run_id")}
         for index, row in enumerate(r for r in runs if r.get("status") == "COMPLETED")]
    )
    meta = {"case": case.case_id, "runs": len(runs), "feasible": len(archive.feasible())}
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
