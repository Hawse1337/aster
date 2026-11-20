"""Turn the queue's job records into the two answers a user wants.

"why is this slow?" and "how often does it work?" — both are computable from the persisted jobs, so
the aggregation lives here rather than inside the run manager, and the page can show it whether or
not a manager is attached (a restarted application has a queue but no manager yet).
"""
from __future__ import annotations

from typing import Any, Iterable

__all__ = ["summarise", "median", "FAILED"]


FAILED = ("FAILED", "STALLED")


def median(values: list[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[middle], 2)
    return round((ordered[middle - 1] + ordered[middle]) / 2.0, 2)


def summarise(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Status counts, success rate, per-stage medians and the slowest runs."""
    jobs = list(rows)
    by_status: dict[str, int] = {}
    stage_samples: dict[str, list[float]] = {}
    for row in jobs:
        status = str(row.get("status") or "UNKNOWN")
        by_status[status] = by_status.get(status, 0) + 1
        for stage, seconds in (row.get("timings") or {}).items():
            try:
                stage_samples.setdefault(str(stage), []).append(float(seconds))
            except (TypeError, ValueError):
                continue
    total = len(jobs)
    completed = by_status.get("COMPLETED", 0)
    slowest = sorted((row for row in jobs if row.get("total_seconds")),
                     key=lambda row: float(row["total_seconds"]), reverse=True)[:5]
    return {
        "runs": total,
        "by_status": by_status,
        "success_rate": round(completed / total, 3) if total else 0.0,
        "stage_median_seconds": {stage: median(values)
                                 for stage, values in sorted(stage_samples.items())},
        "stage_samples": {stage: len(values) for stage, values in sorted(stage_samples.items())},
        "slowest": [{"run_id": row.get("run_id"), "status": row.get("status"),
                     "total_seconds": row.get("total_seconds")} for row in slowest],
    }
