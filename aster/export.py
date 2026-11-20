"""Pareto front export: the data and the process record GPT's module A asks for.

``ParetoArchive`` keeps the evaluated candidates; this module turns them into the artefacts a
reviewer or a downstream tool needs: one row per front member with parameters and every
metric, plus the per-generation progress so the optimisation run can be audited after the fact.

The CSV export is deliberately flat (one column per parameter and per metric) because that is
what spreadsheets and the GUI table expect, while the JSON export keeps the nested structure.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from .optimize import Evaluation, ParetoArchive

__all__ = ["front_rows", "write_front_csv", "write_front_json", "progress_rows"]


def _sorted_params(params: dict[str, Any]) -> dict[str, Any]:
    return {key: params[key] for key in sorted(params)}


def _sorted_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    return {key: metrics[key] for key in sorted(metrics)}


def front_rows(archive: ParetoArchive) -> list[dict[str, Any]]:
    """One flat row per feasible, non-dominated candidate (stable ordering)."""
    front = archive.first_front()
    rows: list[dict[str, Any]] = []
    for entry in sorted(front, key=lambda item: item.candidate_id):
        rows.append(
            {
                "candidate_id": entry.candidate_id,
                "family_id": entry.family_id,
                "run_id": entry.run_id,
                "status": entry.status,
                "feasible": entry.feasible,
                "total_violation": round(float(entry.total_violation), 6),
                "params": _sorted_params(dict(entry.params)),
                "metrics": _sorted_metrics(dict(entry.metrics)),
                "violations": _sorted_metrics(dict(entry.violations)),
            }
        )
    return rows


def _column_names(rows: Sequence[dict[str, Any]]) -> tuple[list[str], list[str]]:
    params: list[str] = sorted({key for row in rows for key in row["params"]})
    metrics: list[str] = sorted({key for row in rows for key in row["metrics"]})
    return params, metrics


def write_front_csv(path: str | Path, rows: Sequence[dict[str, Any]]) -> Path:
    """Write the front as a flat CSV: identity, parameters, then metrics."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    params, metrics = _column_names(rows)
    header = (["candidate_id", "family_id", "run_id", "status", "feasible",
               "total_violation"]
              + ["param:" + name for name in params]
              + ["metric:" + name for name in metrics])
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for row in rows:
            writer.writerow(
                [row["candidate_id"], row["family_id"], row["run_id"] or "",
                 row["status"], int(bool(row["feasible"])), row["total_violation"]]
                + [row["params"].get(name, "") for name in params]
                + [row["metrics"].get(name, "") for name in metrics]
            )
    return target


def write_front_json(path: str | Path, rows: Sequence[dict[str, Any]],
                     *, progress: Sequence[dict[str, Any]] | None = None,
                     meta: dict[str, Any] | None = None) -> Path:
    """Write the front with its progress record in one JSON document."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": dict(meta or {}),
        "front": list(rows),
        "progress": [dict(item) for item in (progress or [])],
        "front_size": len(rows),
    }
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return target


def progress_rows(history: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalise the optimisation history into a comparable per-round record.

    Different strategies report different keys; this keeps the ones the acceptance criterion
    cares about (evaluations, feasible count, front size, budget exhausted) and passes the rest
    through so nothing is silently dropped.
    """
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(history, start=1):
        row = dict(item)
        row.setdefault("round", index)
        rows.append(row)
    return rows
