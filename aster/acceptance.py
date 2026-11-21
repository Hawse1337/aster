"""Replay match and ring-pattern acceptance on one saved HFSS run.

This command never starts AEDT or edits a campaign. The report evaluates saved
data; it does not infer successful solver completion from files being present.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from .band_acceptance import BandAcceptanceSpec, assess_touchstone_band
from .farfield import artifact_dir, view
from .pattern_acceptance import ASTRABB_RING_TARGET, assess_ring_pattern


def replay(campaign: Path, run_id: str, spec: BandAcceptanceSpec, *,
           pattern_frequencies_ghz: list[float] | None = None,
           astrabb_target: bool = False) -> dict[str, Any]:
    folder = artifact_dir(campaign, run_id)
    if not folder.is_dir():
        raise ValueError(f"No artifacts for run {run_id}")
    touchstone = folder / "result.s1p"
    initial = view(campaign, run_id)
    saved = initial["frequencies_ghz"]
    requested = saved if pattern_frequencies_ghz is None else pattern_frequencies_ghz
    if any(not math.isfinite(f) or f <= 0 for f in requested):
        raise ValueError("Pattern frequencies must be finite and positive")
    rows = []
    for frequency in sorted(set(requested)):
        if frequency < spec.band_ghz[0] or frequency > spec.band_ghz[1]:
            if pattern_frequencies_ghz is not None:
                raise ValueError("Pattern frequency lies outside the acceptance band")
            continue
        selected = initial if initial["frequency_ghz"] == frequency else view(campaign, run_id, frequency)
        result = selected["ring_pattern"]
        if result is None:
            result = assess_ring_pattern(None, source=selected["source"], quantity=selected["quantity"])
            result["reason"] = selected["detail"] or result["reason"]
        elif astrabb_target and result["metrics"]:
            if not selected["coordinate_system_recorded"] or selected["coordinate_system"] != "Global":
                result = {**assess_ring_pattern(None), "reason": "AstraBB target requires recorded Global axes"}
            else:
                result = assess_ring_pattern(selected["sphere"], source=result["source"],
                                             quantity=result["quantity"], target=ASTRABB_RING_TARGET)
        rows.append({"frequency_ghz": frequency, **result})
    missing = [row["frequency_ghz"] for row in rows if not row["metrics"]]
    if any(row["status"] == "FAIL" for row in rows):
        pattern_status = "FAIL"
    elif not rows or missing:
        pattern_status = "INSUFFICIENT_EVIDENCE"
    else:
        pattern_status = "SAMPLED_PASS" if astrabb_target else "SAMPLED_METRICS"

    return {"schema_version": 1, "campaign": str(campaign.resolve()), "run_id": run_id,
            "solver_completion_verified_by_replay": False,
            "s11": assess_touchstone_band(touchstone, spec, source="MEASURED") if touchstone.is_file() else None,
            "pattern": {"status": pattern_status, "coordinate_system": initial["coordinate_system"],
                        "target": "astrabb_equatorial_v2" if astrabb_target else None,
                        "available_frequencies_ghz": saved, "missing_frequencies_ghz": missing,
                        "support": "solved_frequencies_only", "rows": rows}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--run-id", required=True, help="Guard artifact run ID")
    parser.add_argument("--band", type=float, nargs=2, required=True, metavar=("LOW", "HIGH"))
    parser.add_argument("--threshold-db", type=float, default=-10.0)
    parser.add_argument("--max-gap-ghz", type=float, default=1.0)
    parser.add_argument("--guard-margin-db", type=float, default=0.0)
    parser.add_argument("--pattern-frequencies", type=float, nargs="+")
    parser.add_argument("--astrabb-target", action="store_true")
    args = parser.parse_args()
    try:
        spec = BandAcceptanceSpec(tuple(args.band), max_gap_ghz=args.max_gap_ghz,
                                  threshold_db=args.threshold_db, guard_margin_db=args.guard_margin_db)
        report = replay(args.campaign, args.run_id, spec,
                        pattern_frequencies_ghz=args.pattern_frequencies,
                        astrabb_target=args.astrabb_target)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=True, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
