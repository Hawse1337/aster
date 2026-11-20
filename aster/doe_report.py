"""Turn a finished DoE batch into numbers a human can act on.

The point of the baseline is not 'we ran ten more simulations': it is whether the seven-dimensional box
contains designs that meet the brief, which parameters move directionality, and how the measured spread
compares with the one hand-picked default. This module reads the queue database plus the ledger and
answers those questions from measurements only - no predicted values.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

__all__ = ["load_doe_rows", "summarise_doe", "write_report"]

METRICS = ("s11_bandwidth", "peak_gain", "directivity_horiz_db", "beam_theta", "ripple",
           "sll")
CONSTRAINTS = {
    "s11_bandwidth": (">=", 3.0),
    "peak_gain": (">=", 6.0),
    "directivity_horiz_db": (">=", 7.0),
    "beam_theta": ("window", (85.0, 95.0)),
    "ripple": ("<=", 3.0),
}


def _passes(value: float | None, rule: Any) -> bool:
    if value is None:
        return False
    operator, target = rule
    if operator == ">=":
        return float(value) >= float(target)
    if operator == "<=":
        return float(value) <= float(target)
    if operator == "window":
        low, high = target
        return float(low) <= float(value) <= float(high)
    raise ValueError("unknown rule %r" % (rule,))


def load_doe_rows(queue_path: str | Path) -> list[dict[str, Any]]:
    """Completed and failed rows of the batch, with parameters and metrics."""
    import sqlite3

    connection = sqlite3.connect(str(queue_path), timeout=10.0)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT run_id, status, params, metrics, violations, feasible, error_code, "
            "timings FROM jobs ORDER BY rowid").fetchall()
    finally:
        connection.close()
    result: list[dict[str, Any]] = []
    for row in rows:
        try:
            params = json.loads(row["params"] or "{}")
        except Exception:  # noqa: BLE001 - a malformed row is still evidence
            params = {}
        try:
            metrics = json.loads(row["metrics"] or "{}")
        except Exception:  # noqa: BLE001
            metrics = {}
        entry = {"run_id": row["run_id"], "status": row["status"], "params": params,
                 "metrics": metrics, "error_code": row["error_code"]}
        for key in METRICS:
            value = metrics.get(key)
            entry[key] = float(value) if isinstance(value, (int, float)) else None
        result.append(entry)
    # a design enqueued twice (retried after an environment failure) describes one design, not two:
    # identity is the normalised parameter set and the latest row is the one that counts
    latest: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for entry in result:
        key = json.dumps(entry["params"], sort_keys=True)
        if key not in latest:
            order.append(key)
        latest[key] = entry
    return [latest[key] for key in order]


def _spread(values: Iterable[float | None]) -> dict[str, float | None]:
    numbers = [float(value) for value in values if value is not None]
    if not numbers:
        return {"min": None, "median": None, "max": None, "n": 0}
    numbers.sort()
    middle = len(numbers) // 2
    median = (numbers[middle] if len(numbers) % 2 else
              (numbers[middle - 1] + numbers[middle]) / 2.0)
    return {"min": numbers[0], "median": median, "max": numbers[-1], "n": len(numbers)}


def _parameter_effect(rows: list[dict[str, Any]], metric: str) -> list[dict[str, Any]]:
    """Rank parameters by the gap between their best and worst halves (a coarse, honest signal)."""
    usable = [row for row in rows if row.get(metric) is not None and row.get("params")]
    if len(usable) < 4:
        return []
    effects = []
    for name in usable[0]["params"]:
        values = sorted(usable, key=lambda row: float(row["params"].get(name, 0.0)))
        half = max(1, len(values) // 2)
        low = [row[metric] for row in values[:half]]
        high = [row[metric] for row in values[-half:]]
        gap = (sum(high) / len(high)) - (sum(low) / len(low))
        effects.append({"parameter": name, "gap": round(gap, 4),
                        "low_mean": round(sum(low) / len(low), 4),
                        "high_mean": round(sum(high) / len(high), 4)})
    effects.sort(key=lambda item: abs(item["gap"]), reverse=True)
    return effects


def summarise_doe(rows: list[dict[str, Any]], default_metrics: dict[str, float] | None = None
                  ) -> dict[str, Any]:
    completed = [row for row in rows if row["status"] == "COMPLETED"]
    failed = [row for row in rows if row["status"] == "FAILED"]
    # queued/running designs belong to the plan but have no outcome yet; counting them as failures
    # would make a batch in flight look like a disaster
    pending = [row for row in rows if row not in completed and row not in failed]
    feasible = [row for row in completed
                if all(_passes(row.get(key), rule) for key, rule in CONSTRAINTS.items())]
    best = {}
    for key in METRICS:
        values = [(row[key], row) for row in completed if row.get(key) is not None]
        if values:
            best[key] = max(values, key=lambda item: item[0])[1]
    lowest_sll = None
    sll_rows = [(row.get("sll"), row) for row in completed if row.get("sll") is not None]
    if sll_rows:
        lowest_sll = min(sll_rows, key=lambda item: item[0])[1]
    report = {
        "planned": len(rows), "completed": len(completed), "failed": len(failed),
        "pending": len(pending),
        "feasible": len(feasible),
        "feasible_run_ids": [row["run_id"] for row in feasible],
        "failure_codes": sorted({row["error_code"] for row in failed if row["error_code"]}),
        "spread": {key: _spread(row.get(key) for row in completed) for key in METRICS},
        "best": {key: {"run_id": row["run_id"], "value": row[key],
                       "params": row["params"]}
                 for key, row in best.items()},
        "constraint_pass_rate": {
            key: (sum(1 for row in completed if _passes(row.get(key), rule)) / len(completed)
                  if completed else 0.0)
            for key, rule in CONSTRAINTS.items()},
        "lowest_sll": ({"run_id": lowest_sll["run_id"], "value": lowest_sll["sll"],
                          "params": lowest_sll["params"]} if lowest_sll else None),
        "parameter_effect": {key: _parameter_effect(completed, key)
                             for key in ("directivity_horiz_db", "peak_gain", "s11_bandwidth")},
    }
    if default_metrics:
        report["default_design"] = {key: default_metrics.get(key) for key in METRICS}
        report["best_beats_default"] = {
            key: bool(best.get(key) and default_metrics.get(key) is not None
                      and best[key][key] > float(default_metrics[key]))
            for key in METRICS}
    report["verdict"] = _verdict(report)
    return report


def _verdict(report: dict[str, Any]) -> str:
    if not report["completed"]:
        return "no completed design: nothing to conclude"
    parts = ["%d of %d completed" % (report["completed"], report["planned"])]
    if report["feasible"]:
        parts.append("%d meet every constraint" % report["feasible"])
    else:
        parts.append("none meet every constraint yet")
    direct = report["spread"].get("directivity_horiz_db") or {}
    if direct.get("max") is not None:
        parts.append("horizontal directivity spans %.2f-%.2f dBi"
                     % (direct["min"], direct["max"]))
    return "; ".join(parts)


def write_report(report: dict[str, Any], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return target
