"""Read a design-study history from the GUI event log and the guard's SQLite ledger.

The study run_id is the guard ID. Artefacts are resolved within the corresponding
runs/<id>/artifacts directory, including after a campaign is copied. result.s1p
is fingerprinted at import time. The guard schema does not store an export hash.
Native realized-gain JSON (including farfield_views.json if it actually names
RealizedGainTotal), or CSV with <name>.csv.meta.json, is accepted from artifacts/
or artifacts/farfield/. Solver display GainTotal and plain cut CSVs cannot
establish absolute realized gain.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path, PureWindowsPath
import re
import sqlite3

from .band_acceptance import _read_s1p_evidence
from .farfield import artifact_dir, load_coverage_snapshots
from .learning_contract import finite_metrics, stored_contract
from .optimize import evaluate_constraints


_DIAGNOSTIC = {"FAILED", "MODEL_FAILED", "TIMEOUT", "STALLED", "BUDGET_EXCEEDED",
               "RECOVERY_REQUIRED"}
_WORKFLOW_FIELDS = ("started_utc", "updated_utc", "total_seconds", "error_code", "error_detail",
                    "stage", "failure", "timings", "cache_hit", "cached")


def _events(path):
    latest = {}
    solve_started = {}
    if not path.is_file():
        return latest
    with path.open("rb") as source:
        for n, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line.decode("utf-8-sig"))
            except (UnicodeError, ValueError) as exc:
                if not line.endswith(b"\n"):
                    break  # An active append may have only written part of the final event.
                raise ValueError(f"{path.name} contains an invalid event at line {n}") from exc
            if isinstance(row, dict) and isinstance(row.get("run_id"), str) and row["run_id"]:
                jid = row["run_id"]
                if row.get("stage") in {"CONNECTING", "MODEL", "RETRYING"}:
                    solve_started.pop(jid, None)
                if row.get("stage") == "SOLVING" and row.get("updated_utc"):
                    solve_started.setdefault(jid, row["updated_utc"])
                row["_solve_started"] = solve_started.get(jid)
                latest[jid] = row
    return latest


def is_real_run(row):
    return (not row.get("is_prediction") and not row.get("dry_run")
            and str(row.get("mode", "real")).lower() == "real"
            and str(row.get("source", "measured")).lower() == "measured"
            and not str(row.get("run_id", "")).lower().startswith("dry-")
            and str(row.get("fidelity", "high")).lower() == "high")


def read_study_reservations(campaign, case, recipe_id):
    """Read queue occupancy only. Older queues without recipe telemetry cannot identify a reservation."""
    path = (Path(campaign) / "queue.sqlite").resolve()
    if not path.is_file():
        return []
    results = []
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            cols = {item[1] for item in db.execute("PRAGMA table_info(jobs)")}
            if not {"run_id", "case_name", "status", "params", "telemetry"} <= cols:
                return []
            entries = db.execute("SELECT run_id,status,params,telemetry FROM jobs "
                                 "WHERE case_name=? ORDER BY run_id", (case.case_id,))
            for rid, status, params_json, telemetry_json in entries:
                try:
                    params = json.loads(params_json)
                    telemetry = json.loads(telemetry_json or "{}")
                    if not isinstance(telemetry, dict) or not isinstance(params, dict):
                        continue
                    contract = telemetry.get("learning_contract")
                    if (not isinstance(contract, dict) or contract.get("identity") != recipe_id
                            or contract.get("case_id") != case.case_id
                            or contract.get("family_id") != case.family.family_id
                            or contract.get("measurement_recipe_binding") == "execution"
                            or case.family.param_space.validate(params) != params):
                        continue
                except (ValueError, KeyError, TypeError):
                    continue
                action = telemetry.get("study_action")
                aid = revision = None
                if action is not None:
                    if (not isinstance(action, dict) or action.get("case_id") != case.case_id
                            or action.get("recipe_id") != recipe_id or not isinstance(action.get("id"), str)
                            or not re.fullmatch("[0-9a-f]{64}", action["id"]) or rid != "study-" + action["id"]):
                        continue
                    aid, revision = action["id"], action.get("builder_sha256")
                # Prior terminal study IDs are also permanently reserved by JobStore.enqueue.
                if aid or status in {"QUEUED", "PREPARING", "RUNNING", "SOLVING",
                                     "APPROVAL_REQUIRED", "RECOVERY_REQUIRED", "COMPLETED"}:
                    results.append(dict(run_id=rid, status=status, params=params, action_id=aid,
                                        builder_sha256=revision, case=case.case_id, source="QUEUE"))
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"cannot read design queue: {exc}") from exc
    return results


def _identity(job, case):
    contract = stored_contract(job)
    if (not is_real_run(job) or job.get("case") != case.case_id or contract.get("case_id") != case.case_id
            or any(job.get(key, case.case_id) != case.case_id for key in ("case_id", "case_name"))
            or contract.get("family_id") != case.family.family_id
            or not isinstance(contract.get("identity"), str) or not contract["identity"].strip()
            or contract.get("measurement_recipe_binding") == "execution"
            or job.get("recipe_id", contract["identity"]) != contract["identity"]):
        return None
    params = job.get("params")
    if not isinstance(params, dict):
        return None
    try:
        if case.family.param_space.validate(params) != params:
            return None
    except (TypeError, ValueError, KeyError):
        return None
    return contract


def _time(value):
    try:
        value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return value.astimezone(timezone.utc) if value.tzinfo else None
    except ValueError:
        return None


def _builder_digest(job):
    # A cache hit ran today's builder but reused a previous solve.
    result = (job.get("telemetry") or {}).get("builder_result")
    if job.get("cache_hit") or job.get("cached") or not isinstance(result, dict):
        return None
    digest = result.get("script_sha256")
    return digest.lower() if isinstance(digest, str) and re.fullmatch(r"[0-9a-fA-F]{64}", digest) else None


def _workflow(job, contract):
    row = {k: job[k] for k in _WORKFLOW_FIELDS if k in job}
    if (digest := _builder_digest(job)) is not None:
        row["builder_sha256"] = digest
    telemetry = job.get("telemetry") or {}
    action = telemetry.get("study_action") or {}
    if (action.get("case_id") == contract["case_id"] and action.get("recipe_id") == contract["identity"]
            and action.get("kind") in {"geometry", "interaction", "tolerance", "frequency", "coverage"}
            and job["run_id"] == "study-" + str(action.get("id"))):
        row["action_kind"] = action["kind"]
        row["study_action_id"] = action["id"]
        if "action_seconds" in job:
            row["action_seconds"] = job["action_seconds"]
    return row


def _attach_artifacts(row, folder):
    warnings = []
    path = folder / "result.s1p"
    if path.is_file() and path.resolve().parent == folder:
        try:
            points, invalid, manifest = _read_s1p_evidence(path)
            if not points or invalid or manifest["omitted_rows"] or any(f <= 0 for f, _ in points):
                raise ValueError("invalid or missing frequency data rows")
            row["touchstone"] = {"path": str(path), "run_id": row["run_id"],
                                 "sha256": manifest["sha256"], "hash_basis": "bytes_at_import"}
        except (ValueError, OSError) as exc:
            warnings.append(f"result.s1p: {exc}")
    snapshots = []
    for directory in (folder, folder / "farfield"):
        if not directory.is_dir() or directory.resolve() != directory:
            continue
        for file in sorted(directory.iterdir()):
            if not file.is_file() or file.resolve().parent != directory:
                continue
            try:
                if file.suffix.lower() == ".json" and not file.name.endswith(".meta.json"):
                    meta = json.loads(file.read_bytes())
                elif file.suffix.lower() == ".csv":
                    sidecar = file.with_name(file.name + ".meta.json")
                    if not sidecar.is_file() or sidecar.resolve().parent != directory:
                        continue
                    meta = json.loads(sidecar.read_bytes())
                else:
                    continue
                if not isinstance(meta, dict) or meta.get("run_id") != row["run_id"]:
                    continue
                if meta.get("quantity", "" if file.suffix.lower() == ".json" else
                            "dB(RealizedGainTotal)") != "dB(RealizedGainTotal)":
                    continue
                records = load_coverage_snapshots(file)
                if all(r["run_id"] == row["run_id"] for r in records):
                    snapshots.extend(records)
            except (ValueError, TypeError, KeyError, OSError) as exc:
                warnings.append(f"{file.name}: {exc}")
    if snapshots:
        row["coverage_snapshots"] = snapshots
    if warnings:
        row["artifact_warnings"] = warnings


def _solve_origin(job, record):
    start, finish = _time(job.get("_solve_started")), _time(job.get("updated_utc"))
    created, ended = _time(record["created_utc"]), _time(record["updated_utc"])
    if job.get("cache_hit") or job.get("cached") or (start and ended and ended < start):
        return "cached"
    # The GUI clock is truncated to seconds. A solve already finished in
    # the first recorded second does not prove this job started it.
    interval = (start and finish and created and ended and start <= created <= ended
                and start + timedelta(seconds=1) <= ended < finish + timedelta(seconds=1))
    if interval or job.get("cached") is False or job.get("cache_hit") is False:
        return "original"
    return "unverified"


def _guard_row(db, campaign, job, case, contract, kinds, record, origin):
    guard_id = job["guard_run_id"]
    if not isinstance(guard_id, str) or guard_id.lower().startswith("dry-"):
        return None
    try:
        folder = artifact_dir(campaign, guard_id)
    except ValueError:
        return None
    if folder != campaign / "runs" / guard_id / "artifacts":
        return None
    if not is_real_run(dict(record)) or record["project"] != case.project:
        return None
    params = json.loads(record["params_json"])
    if not isinstance(params, dict) or not is_real_run(params):
        return None
    # Extra guard keys describe AEDT (PID, setup, etc.). Every design value
    # must be explicitly present and match, including categorical variables.
    reported = {name: params[name] for name in job["params"] if name in params}
    try:
        if reported != job["params"] or case.family.param_space.validate(reported) != reported:
            return None
    except (TypeError, ValueError, KeyError):
        return None
    recipe = contract.get("measurement_recipe") or {}
    for field, alias in (("design_name", "design"), ("setup_name", "setup")):
        target = recipe.get(alias, recipe.get(field))
        if target and params.get(field) and target != params[field]:
            return None
    if record["artifact_dir"]:
        # Station-specific absolute prefixes change when copying a campaign.
        tail = PureWindowsPath(record["artifact_dir"]).parts[-3:]
        if tail != ("runs", guard_id, "artifacts"):
            return None
    status = str(record["status"]).upper()
    gui_status = str(job.get("status", "")).upper()
    if gui_status != "COMPLETED" and status == "COMPLETED":
        status = gui_status
    if status not in _DIAGNOSTIC | {"COMPLETED"}:
        return None
    row = _workflow(job, contract)
    row.update(run_id=guard_id, guard_run_id=guard_id, job_run_id=job["run_id"],
               case=case.case_id, params=reported, learning_contract=contract, status=status,
               source="MEASURED", evidence_kind="guard_ledger", created_utc=record["created_utc"])
    if status != "COMPLETED":
        return row
    if origin == "cached":
        row["cache_hit"] = True
    if origin != "original":
        # The builder was observed in this job. Its script and elapsed time
        # cannot be attributed to an older or unverified cached solve.
        row.pop("builder_sha256", None)
        row.pop("total_seconds", None)
        row.pop("action_seconds", None)
        row["timing_evidence"] = "cached_or_unverified_job_timing"
    raw = db.execute("SELECT name,value,passed FROM metrics WHERE run_id=?", (guard_id,))
    values, checks = {}, {}
    for name, value, passed in raw:
        values[kinds.get(name, name)] = value
        if passed is not None:
            checks[name] = passed == 1
    metrics = finite_metrics(values)
    _, violations, _ = evaluate_constraints(metrics, case)
    for c in case.constraints:
        if checks.get(c.id) is not True:
            violations.setdefault(c.id, 1000.)
    for name, value in finite_metrics(job.get("violations") or {}).items():
        if value is not None and value > 0:
            violations[name] = max(violations.get(name, 0), value)
    try:
        stages = json.loads(record["notes"] or "[]")
    except (ValueError, TypeError):
        stages = []
    verdicts = [stage for stage in stages if isinstance(stage, dict)
                and stage.get("stage") == "COMPLETED"] if isinstance(stages, list) else []
    guard_rejected = any(stage.get("feasible") is False or stage.get("invalid") or stage.get("unmet")
                         for stage in verdicts)
    row.update(metrics=metrics, metric_checks=checks, violations=violations,
               feasible=not guard_rejected and not violations and job.get("feasible", True) is True)
    _attach_artifacts(row, folder)
    return row


def load_study_history(campaign: Path, case) -> list[dict]:
    """Join full real-job identities to guard evidence, deduplicated by actual solve.

    A known failed real workflow without a guard ID can inform timing/risk; its
    diagnostic row carries no metrics or artefacts. Missing ledgers cannot
    promote GUI success records. Only the existing SQLite file is opened.
    """
    campaign = Path(campaign).resolve()
    jobs = _events(campaign / "real_runs.jsonl")
    groups = defaultdict(list)
    rows = []
    for job in jobs.values():
        if not is_real_run(job):
            continue
        guard_id = job.get("guard_run_id")
        if isinstance(guard_id, str) and guard_id:
            groups[guard_id].append(job)
        elif job.get("status") in _DIAGNOSTIC and (contract := _identity(job, case)):
            row = _workflow(job, contract)
            row.update(run_id=job["run_id"], job_run_id=job["run_id"], case=case.case_id,
                       params=job["params"], learning_contract=contract, status=job["status"],
                       source="MEASURED", evidence_kind="workflow_outcome_only")
            rows.append(row)
    path = campaign / "campaign.sqlite"
    if groups and path.is_file():
        kinds = {m["id"]: m["kind"] for m in case.to_spec()["metrics"]}
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")  # Read runs and metrics in the same snapshot.
            for guard_id, claims in groups.items():
                # Cache hits can have different queue IDs. Never reassign one
                # solve to conflicting designs, even across cases or recipes.
                identities = [(j.get("case"), stored_contract(j).get("identity"), j.get("params"))
                              for j in claims]
                if any(i != identities[0] for i in identities):
                    continue
                record = db.execute("SELECT * FROM runs WHERE run_id=?", (guard_id,)).fetchone()
                if record is None:
                    continue
                origins = {j["run_id"]: _solve_origin(j, record) for j in claims}
                hashes = {digest for j in claims if origins[j["run_id"]] != "cached"
                          and (digest := _builder_digest(j)) is not None}
                if len(hashes) > 1:
                    continue
                claims.sort(key=lambda j: (j.get("status") != "COMPLETED",
                                           origins[j["run_id"]] != "original", origins[j["run_id"]] == "cached",
                                           j.get("started_utc") or "", j["run_id"]))
                job = claims[0]
                contract = _identity(job, case)
                if contract:
                    row = _guard_row(db, campaign, job, case, contract, kinds, record, origins[job["run_id"]])
                    if row is not None:
                        rows.append(row)
    return sorted(rows, key=lambda r: (r.get("created_utc") or r.get("started_utc") or "",
                                       r["run_id"]))
