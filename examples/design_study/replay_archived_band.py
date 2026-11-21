"""Replay the three archived S11 sweeps with a patched ASTER or antopt source tree.

Example (PowerShell):
    python replay_archived_band.py aster_gpt_review_20261121.zip --source-tree path/to/aster-source --package aster --output archived-band.json

The input may also be the extracted evidence/final_24_32_acceptance.json.
The committed summary contains rounded S11 values and names the three variants.
Replaying them uses no AEDT installation or licence.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sys
from zipfile import ZipFile


SUMMARY_NAME = "final_24_32_acceptance.json"


def read_evidence(path: Path) -> bytes:
    if path.suffix.lower() != ".zip":
        return path.read_bytes()
    with ZipFile(path) as bundle:
        # Select the archive's evidence directory, independent of its wrapping folder.
        matches = [name for name in bundle.namelist()
                   if name == "evidence/" + SUMMARY_NAME
                   or (name.endswith("/evidence/" + SUMMARY_NAME) and name.count("/") == 2)]
        if len(matches) != 1:
            raise ValueError("expected one top-level evidence/" + SUMMARY_NAME)
        return bundle.read(matches[0])


def replay(raw: bytes, band_api) -> dict:
    archive = json.loads(raw.decode("utf-8-sig"))
    band = tuple(archive["band"])
    scenarios = [
        ("fixed", 1.0, None),
        ("window_4_ghz", 1.0, 4.0),
        ("window_7_ghz", 1.0, 7.0),
        ("window_7_ghz_dense", 0.25, 7.0),
    ]
    output = {"kind": "historical_archived_s11_replay", "new_solver_calls": 0,
              "source_file": SUMMARY_NAME, "source_sha256": hashlib.sha256(raw).hexdigest(),
              "archive_criterion": archive["criterion"], "band_ghz": list(band),
              "archive_target": archive.get("target"), "cases": {}}
    for case_name, record in archive["cases"].items():
        points = sorted((float(f), float(db)) for f, db in record["s11_db"].items())
        result = {"archived_project": record.get("project"), "sample_count": len(points),
                  "archived_peak_gain_dbi": record.get("peak_gain_dbi"), "scenarios": {}}
        for name, gap, width in scenarios:
            spec = band_api.BandAcceptanceSpec(band, max_gap_ghz=gap, threshold_db=-10.0)
            result["scenarios"][name] = band_api.assess_s11_requirement(
                points, spec, source="MEASURED", required_width_ghz=width, max_suggestions=3)
        output["cases"][case_name] = result
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="original review ZIP or extracted summary JSON")
    parser.add_argument("--package", choices=("aster", "antopt"), default="aster")
    parser.add_argument("--source-tree", type=Path, help="directory containing the selected package")
    parser.add_argument("--output", type=Path, help="write JSON here; default is stdout")
    args = parser.parse_args()
    if args.source_tree:
        if not (args.source_tree / args.package / "band_acceptance.py").is_file():
            parser.error("--source-tree must contain the chosen package and its band_acceptance.py")
        sys.path.insert(0, str(args.source_tree.resolve()))
    api = importlib.import_module(args.package + ".band_acceptance")
    if not hasattr(api, "assess_s11_requirement"):
        parser.error("install the audited package or specify its patched --source-tree")
    payload = replay(read_evidence(args.input), api)
    text = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.buffer.write(text.encode("utf-8"))


if __name__ == "__main__":
    main()
