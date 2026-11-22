# -*- coding: utf-8 -*-
"""Completion gate for optimisation campaigns.

A campaign is only "complete" when it carries the minimum evidence that it was
driven by the optimisation system: an aster manifest, real completed runs, a
Pareto front and an acceptance report.  This is deliberately separate from the
guard: the guard enforces safe execution; this module enforces method evidence.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MANIFEST_NAME = "optimization_manifest.json"
GATE_NAME = "optimization_gate.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def new_manifest(
    *,
    campaign_id: str,
    optimizer: str = "aster",
    case_id: str = "",
    builder_id: str = "",
    campaign_dir: str | Path = "",
    source: str = "aster",
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "optimizer": optimizer,
        "source": source,
        "case_id": case_id,
        "builder_id": builder_id,
        "campaign_dir": str(campaign_dir),
        "started_at": _now(),
        "updated_at": _now(),
        "status": "running",
        "runs": [],
        "pareto": [],
        "acceptance": None,
    }


def write_manifest(campaign_dir: str | Path, manifest: dict[str, Any]) -> Path:
    path = Path(campaign_dir) / MANIFEST_NAME
    manifest["updated_at"] = _now()
    _atomic(path, manifest)
    return path


def record_run(manifest: dict[str, Any], record: dict[str, Any]) -> None:
    manifest.setdefault("runs", []).append(record)
    manifest["updated_at"] = _now()


def campaign_gate(
    campaign_dir: str | Path,
    *,
    require_optimizer: bool = True,
    min_completed_runs: int = 1,
    require_pareto: bool = True,
    require_acceptance: bool = True,
) -> dict[str, Any]:
    campaign = Path(campaign_dir)
    manifest_path = campaign / MANIFEST_NAME
    checks: dict[str, bool] = {}
    missing: list[str] = []
    manifest: dict[str, Any] = {}
    if not manifest_path.is_file():
        result = {
            "passed": False,
            "campaign_dir": str(campaign),
            "checks": {"manifest_exists": False},
            "missing": ["optimization_manifest.json"],
            "manifest_path": str(manifest_path),
        }
        _atomic(campaign / GATE_NAME, result)
        return result
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    checks["manifest_exists"] = bool(manifest)
    if require_optimizer:
        checks["optimizer_is_aster"] = str(manifest.get("optimizer") or "").lower() == "aster"
        checks["source_is_optimizer"] = str(manifest.get("source") or "").lower() in {
            "aster",
            "optimizer",
        }
    runs = manifest.get("runs") or []
    completed = [
        row for row in runs
        if isinstance(row, dict) and str(row.get("status") or "").upper() == "COMPLETED"
    ]
    checks["completed_runs"] = len(completed) >= int(min_completed_runs)
    if require_pareto:
        checks["pareto_present"] = bool(manifest.get("pareto"))
    if require_acceptance:
        acceptance = manifest.get("acceptance")
        checks["acceptance_present"] = bool(acceptance) and (
            not isinstance(acceptance, dict) or bool(acceptance.get("passed"))
        )
    for name, passed in checks.items():
        if not passed:
            missing.append(name)
    result = {
        "passed": all(checks.values()),
        "campaign_dir": str(campaign),
        "campaign_id": manifest.get("campaign_id"),
        "optimizer": manifest.get("optimizer"),
        "checks": checks,
        "missing": missing,
        "manifest_path": str(manifest_path),
        "gate_path": str(campaign / GATE_NAME),
    }
    _atomic(campaign / GATE_NAME, result)
    return result


def read_gate(campaign_dir: str | Path) -> dict[str, Any] | None:
    path = Path(campaign_dir) / GATE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None
