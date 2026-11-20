"""Measured-data analysis helpers (S4 on real campaigns).

Reads campaign records (as produced by ``CampaignRunner``) and answers the
questions an agent actually asks: which candidates were measured, what changed
between them, and what first-order sensitivities the measurements support.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

METRIC_PRIORITY = ("s11", "peak_gain", "realized_gain_h", "ripple")


@dataclass
class MeasurementRow:
    candidate_id: str
    params: dict[str, Any]
    metrics: dict[str, float]
    feasible: bool
    run_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"candidate_id": self.candidate_id, "params": dict(self.params),
                "metrics": dict(self.metrics), "feasible": self.feasible,
                "run_id": self.run_id}


def measured_rows(records: Iterable[dict[str, Any]]) -> list[MeasurementRow]:
    rows: list[MeasurementRow] = []
    for record in records:
        metrics = {name: float(value) for name, value in (record.get("metrics") or {}).items()
                   if isinstance(value, (int, float))}
        if not metrics or record.get("status") != "COMPLETED":
            continue
        rows.append(MeasurementRow(
            candidate_id=str(record.get("candidate_id") or ""),
            params=dict(record.get("params") or {}),
            metrics=metrics,
            feasible=bool(record.get("feasible")),
            run_id=record.get("run_id"),
        ))
    return rows


def table(rows: list[MeasurementRow], metrics: Iterable[str] | None = None) -> str:
    """Plain-text table sorted by candidate id, only measured metrics."""
    names = list(metrics) if metrics else [
        name for name in METRIC_PRIORITY if any(name in row.metrics for row in rows)
    ]
    header = ["candidate"] + [param for param in sorted(rows[0].params)] + names + ["feasible"]
    lines = ["  ".join(f"{cell:>18}" for cell in header)]
    for row in sorted(rows, key=lambda item: item.candidate_id):
        cells = [row.candidate_id[:10]]
        cells += [f"{row.params[key]}" for key in sorted(row.params)]
        cells += [f"{row.metrics.get(name, float('nan')):.3f}" for name in names]
        cells.append("yes" if row.feasible else "no")
        lines.append("  ".join(f"{cell:>18}" for cell in cells))
    return "\n".join(lines)


def first_order_sensitivity(rows: list[MeasurementRow], *, metric: str,
                            parameter: str) -> dict[str, Any]:
    """Least-squares slope of ``metric`` against ``parameter`` across measurements.

    Two measured points are enough for one slope; more points give a least-squares
    fit. Only pairs whose other parameters are identical are used, so the slope
    is a clean one-parameter difference rather than a mixed direction.
    """
    pairs: list[tuple[float, float]] = []
    for left in rows:
        for right in rows:
            if left is right:
                continue
            if parameter not in left.params or parameter not in right.params:
                continue
            others = set(left.params) | set(right.params)
            others.discard(parameter)
            if any(left.params.get(k) != right.params.get(k) for k in others):
                continue
            if left.params[parameter] >= right.params[parameter]:
                continue
            if metric not in left.metrics or metric not in right.metrics:
                continue
            dx = float(right.params[parameter]) - float(left.params[parameter])
            dy = float(right.metrics[metric]) - float(left.metrics[metric])
            pairs.append((dx, dy))
    if not pairs:
        return {"metric": metric, "parameter": parameter, "slope": None, "pairs": 0,
                "note": "no pair of measurements differs only in this parameter"}
    total_xx = sum(dx * dx for dx, _ in pairs)
    total_xy = sum(dx * dy for dx, dy in pairs)
    slope = total_xy / total_xx if total_xx else None
    return {"metric": metric, "parameter": parameter,
            "slope": None if slope is None else float(slope),
            "pairs": len(pairs),
            "deltas": [{"dx": dx, "dy": dy} for dx, dy in pairs]}


def summarize_campaign(summary: dict[str, Any]) -> dict[str, Any]:
    rows = measured_rows(summary.get("records") or [])
    metrics = [name for name in METRIC_PRIORITY if any(name in row.metrics for row in rows)]
    sensitivities = []
    for metric in metrics:
        for parameter in (rows[0].params if rows else {}):
            result = first_order_sensitivity(rows, metric=metric, parameter=parameter)
            if result["slope"] is not None:
                sensitivities.append(result)
    return {
        "measured": len(rows),
        "feasible": sum(1 for row in rows if row.feasible),
        "rows": [row.to_dict() for row in rows],
        "table": table(rows) if rows else "",
        "sensitivities": sensitivities,
    }
