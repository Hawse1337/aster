"""Campaign housekeeping: keep evidence, drop bulk, export results.

A campaign accumulates a project copy, a result directory per run (touchstone, far-field CSVs) and
two databases. One afternoon of measurements reached thirty gigabytes, which is why this module
exists: the numbers people quote live in the ledger and the queue database, while the bulk sits in
``runs/<id>``.

Rules encoded here, on purpose:

* **the ledger is never touched.** ``real_runs.jsonl`` and ``queue.sqlite`` are what the page and the
  recommendations read; deleting them would delete the evidence.
* **deletion is dry-run unless asked.** :func:`apply_retention` returns the plan and only removes
  files when ``apply=True``.
* **the newest runs are kept**, by directory name (they sort chronologically: ``YYYYMMDDTHHMMSS``).
"""
from __future__ import annotations

import csv
import io
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

__all__ = ["CampaignSummary", "summary", "retention_plan", "apply_retention", "export_results",
           "PROTECTED"]

PROTECTED = ("campaign.sqlite", "queue.sqlite", "real_runs.jsonl", "design_spec.json",
             "dry_runs.jsonl", "projects")
"""Files and directories housekeeping must never remove."""


@dataclass
class CampaignSummary:
    path: Path
    runs: list[str] = field(default_factory=list)
    bytes_total: int = 0
    bytes_runs: int = 0
    has_ledger: bool = False
    has_queue: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"path": str(self.path), "runs": len(self.runs),
                "mb_total": round(self.bytes_total / 1e6, 1),
                "mb_runs": round(self.bytes_runs / 1e6, 1),
                "has_ledger": self.has_ledger, "has_queue": self.has_queue,
                "oldest": self.runs[0] if self.runs else None,
                "newest": self.runs[-1] if self.runs else None}


def _directory_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                continue
    return total


def summary(campaign: str | Path) -> CampaignSummary:
    """What is in this campaign, and how much of it is the run bulk."""
    root = Path(campaign)
    run_dirs = sorted(child.name for child in (root / "runs").glob("*")
                      if child.is_dir()) if (root / "runs").is_dir() else []
    report = CampaignSummary(path=root, runs=run_dirs)
    report.bytes_total = _directory_size(root) if root.is_dir() else 0
    report.bytes_runs = _directory_size(root / "runs") if (root / "runs").is_dir() else 0
    report.has_ledger = (root / "real_runs.jsonl").is_file()
    report.has_queue = (root / "queue.sqlite").is_file()
    return report


def retention_plan(campaign: str | Path, keep_last: int = 5) -> list[Path]:
    """Run directories that would be removed, oldest first, keeping ``keep_last`` of them."""
    root = Path(campaign)
    runs_dir = root / "runs"
    if not runs_dir.is_dir():
        return []
    keep = max(0, int(keep_last))
    directories = sorted((child for child in runs_dir.iterdir() if child.is_dir()),
                         key=lambda path: path.name)
    return directories[: max(0, len(directories) - keep)]


def apply_retention(campaign: str | Path, keep_last: int = 5, *,
                    apply: bool = False) -> dict[str, Any]:
    """Remove the oldest run directories beyond ``keep_last``; ``apply=False`` only reports."""
    root = Path(campaign)
    doomed = retention_plan(root, keep_last)
    freed = sum(_directory_size(path) for path in doomed)
    removed: list[str] = []
    if apply:
        for path in doomed:
            if path.name in PROTECTED:
                continue
            try:
                shutil.rmtree(path)
                removed.append(path.name)
            except OSError:
                continue
    freed_bytes = (sum(_directory_size(root / "runs" / name) for name in removed)
                   if apply else freed)
    return {"campaign": str(root), "keep_last": int(keep_last), "planned": [p.name for p in doomed],
            "removed": removed, "applied": bool(apply), "bytes_freed": freed_bytes,
            "mb_freed": round(freed_bytes / 1e6, 2), "protected": list(PROTECTED)}


FIELDS = ["run_id", "case", "status", "feasible", "ring_outer", "ring_height", "tip_offset",
          "s11_bandwidth", "peak_gain", "ripple", "total_seconds", "error_code", "started_utc"]


def _rows(campaign: Path) -> list[dict[str, Any]]:
    """Runs from the ledger and the queue, newest first, flattened for a spreadsheet."""
    rows: dict[str, dict[str, Any]] = {}
    ledger = campaign / "real_runs.jsonl"
    if ledger.is_file():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            rows[str(payload.get("run_id"))] = payload
    queue = campaign / "queue.sqlite"
    if queue.is_file():
        try:
            from .runtime.store import JobStore

            for job in JobStore(queue).jobs():
                rows.setdefault(str(job.get("run_id")), job)
        except Exception:  # noqa: BLE001 - an unreadable queue must not stop an export
            pass

    flattened: list[dict[str, Any]] = []
    for run_id, payload in rows.items():
        params = payload.get("params") or {}
        metrics = payload.get("metrics") or {}
        row = {"run_id": run_id, "case": payload.get("case"),
               "status": payload.get("status"), "feasible": payload.get("feasible"),
               "s11_bandwidth": metrics.get("s11_bandwidth"),
               "peak_gain": metrics.get("peak_gain"), "ripple": metrics.get("ripple"),
               "total_seconds": payload.get("total_seconds"),
               "error_code": payload.get("error_code"),
               "started_utc": payload.get("started_utc") or payload.get("created_utc")}
        for name in ("ring_outer", "ring_height", "tip_offset"):
            row[name] = params.get(name)
        flattened.append(row)
    flattened.sort(key=lambda row: str(row.get("started_utc") or ""), reverse=True)
    return flattened


def export_results(campaign: str | Path, out_dir: str | Path, *,
                   rows: Sequence[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Write ``results.csv`` and ``results.json`` for a campaign; returns what was written."""
    root = Path(campaign)
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    data = list(rows) if rows is not None else _rows(root)
    csv_path = target / "results.csv"
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=FIELDS, extrasaction="ignore")
    writer.writeheader()
    for row in data:
        writer.writerow(row)
    csv_path.write_text(buffer.getvalue(), encoding="utf-8")
    json_path = target / "results.json"
    json_path.write_text(json.dumps({"campaign": str(root), "runs": data,
                                     "summary": summary(root).to_dict()},
                                    indent=2, ensure_ascii=False, default=str),
                         encoding="utf-8")
    return {"csv": str(csv_path), "json": str(json_path), "runs": len(data),
            "csv_bytes": csv_path.stat().st_size, "json_bytes": json_path.stat().st_size}


def filter_rows(rows: Iterable[dict[str, Any]], status: str | None = None) -> list[dict[str, Any]]:
    """Keep rows with the given status (case-insensitive), or everything when None."""
    if not status:
        return list(rows)
    wanted = status.upper()
    return [row for row in rows if str(row.get("status") or "").upper() == wanted]
