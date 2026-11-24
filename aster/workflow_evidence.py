"""Replay final evidence for a selected search geometry in an existing guarded campaign."""
import json
import math
import re
from pathlib import Path

from datetime import datetime, timezone

from .band_acceptance import BandAcceptanceSpec, assess_touchstone_requirement
from .design_history import load_study_history
from .learning_contract import case_contract, stored_contract
from .pattern_acceptance import AngularCoverageSpec, assess_angular_coverage


def attach_run(
    campaign_dir,
    case,
    *,
    guard_run_id: str,
    params: dict,
    recipe: dict,
    builder_sha256: str,
    run_id: str | None = None,
    status: str = "COMPLETED",
    metrics: dict | None = None,
    archive_file: str | None = None,
    candidate_id: str | None = None,
) -> dict:
    """Register one completed guarded run as formal replay evidence.

    Adapters/scripts previously wrote ``real_runs.jsonl`` and an archive by hand.
    This function owns that shape: a real job identity, the pre-solve
    ``case_contract`` recipe, builder SHA, guard run id, and an archive entry for
    the same params. It deliberately does not fabricate metrics or a passed
    verdict; ``replay_selected`` remains the authority.
    """
    campaign = Path(campaign_dir)
    campaign.mkdir(parents=True, exist_ok=True)
    if not isinstance(guard_run_id, str) or not guard_run_id.strip():
        raise ValueError("guard_run_id is required")
    contract = case_contract(case, measurement_recipe=dict(recipe or {}))
    run_id = run_id or ("attached-" + guard_run_id)
    job = {
        "case": case.case_id,
        "run_id": run_id,
        "guard_run_id": guard_run_id,
        "status": str(status or "COMPLETED").upper(),
        "params": dict(params or {}),
        "telemetry": {
            "learning_contract": contract,
            "builder_result": {"script_sha256": builder_sha256},
        },
        "metrics": dict(metrics or {}),
        "feasible": False,
        "cached": False,
        "source": "MEASURED",
        "mode": "real",
        "fidelity": "high",
        "updated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    ledger = campaign / "real_runs.jsonl"
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(job, ensure_ascii=False) + "\n")
    target_archive = Path(archive_file) if archive_file else (campaign / "attached_archive.json")
    target_archive.parent.mkdir(parents=True, exist_ok=True)
    target_archive.write_text(json.dumps({
        "optimizer": "attached",
        "entries": [{
            "params": dict(params or {}),
            "status": str(status or "COMPLETED").upper(),
            "candidate_id": candidate_id or guard_run_id,
        }],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "job": job,
        "contract": contract,
        "archive": str(target_archive),
        "ledger": str(ledger),
        "profile": {
            "recipe_id": contract["identity"],
            "builder_sha256": builder_sha256,
        },
    }


def verified_baseline(rows, run_id):
    return any(r["run_id"] == run_id and r.get("evidence_kind") == "guard_ledger"
               and r.get("status") == "COMPLETED" for r in rows)


def validate_verification(profile, case):
    allowed = {"recipe_id", "builder_sha256", "band", "coverage", "native_check"}
    if not isinstance(profile, dict) or set(profile) - allowed:
        raise ValueError("invalid verification_profile settings")
    if not isinstance(profile.get("recipe_id"), str) or not profile["recipe_id"].strip():
        raise ValueError("verification_profile needs the full-model measurement recipe identity")
    if "native_check" in profile and (not isinstance(profile["native_check"], str) or not profile["native_check"].strip()):
        raise ValueError("native_check needs a registered check name")
    if "builder_sha256" in profile:
        if not isinstance(profile["builder_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", profile["builder_sha256"]):
            raise ValueError("expected a lowercase builder SHA-256")
    band = dict(profile.get("band") or {})
    width = band.pop("required_width_ghz", None)
    band_spec = BandAcceptanceSpec(**band) if band else None
    coverage = AngularCoverageSpec(**profile["coverage"]) if profile.get("coverage") else None
    if not band_spec and not coverage and not profile.get("native_check"):
        raise ValueError("declare a band, angular coverage or project-native verification requirement")
    lo, hi = case.frequency_ghz
    if band_spec:
        a, b = band_spec.band_ghz
        if a < lo or b > hi or width is not None and not 0 < width <= b - a:
            raise ValueError("invalid verification band or window")
    if coverage and any(not lo <= f <= hi for f in coverage.frequencies_ghz):
        raise ValueError("coverage frequency outside case domain")
    return band_spec, width, coverage


def replay_selected(case, profile, campaign_dir, run_id, archive_file, native_checks):
    band, width, coverage = validate_verification(profile, case)
    rows = load_study_history(Path(campaign_dir), case)
    row = next((r for r in rows if r["run_id"] == run_id and r.get("evidence_kind") == "guard_ledger"
                and r.get("status") == "COMPLETED"), None)
    if row is None:
        raise ValueError("no completed guard/worker evidence for this run")
    if stored_contract(row).get("identity") != profile["recipe_id"]:
        raise ValueError("full-model verification recipe differs")
    if profile.get("builder_sha256") and row.get("builder_sha256") != profile["builder_sha256"]:
        raise ValueError("full-model verification builder differs")
    archive = json.loads(Path(archive_file).read_text(encoding="utf-8"))
    candidates = archive.get("entries", archive.get("runs", []))
    if not any(item.get("params") == row["params"] and item.get("status") == "COMPLETED"
               for item in candidates):
        raise ValueError("no completed search candidate matches this geometry")
    measured = row.get("metrics") or {}
    checks = {"case": row.get("feasible") is True, "objectives": all(
        isinstance(measured.get(o.kind), (int, float)) and math.isfinite(measured[o.kind]) for o in case.objectives)}
    details = {}
    if band:
        trace = row.get("touchstone")
        result = assess_touchstone_requirement(trace["path"], band, source="MEASURED",
                     required_width_ghz=width) if trace else None
        checks["band"] = bool(result and result["artifact"]["sha256"] == trace["sha256"] and result["measured_pass"])
        details["band"] = result
    if coverage:
        details["coverage"] = assess_angular_coverage(row.get("coverage_snapshots", []), coverage)
        checks["coverage"] = details["coverage"]["status"] == "SAMPLED_PASS"
    if "native_check" in profile:
        name = profile["native_check"]
        if name not in native_checks:
            raise ValueError(f"required native verification check is unavailable: {name}")
        verdict = native_checks[name](Path(campaign_dir).resolve(), row)
        if not isinstance(verdict, dict) or type(verdict.get("passed")) is not bool:
            raise ValueError(f"invalid native verification result: {name}")
        details["native"] = {"name": name, "result": verdict}
        checks["native"] = verdict["passed"]
    return {"passed": all(checks.values()), "checks": checks, "details": details,
            "params": row["params"], "recipe_id": profile["recipe_id"], "run_id": run_id,
            "campaign_dir": str(Path(campaign_dir).resolve()), "source": "full_model_replay"}
