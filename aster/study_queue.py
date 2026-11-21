"""Reserve study geometry actions in the local worker queue."""
from __future__ import annotations

from pathlib import Path
import re

from .design_study import _digest, resolve_registration
from .learning_contract import case_contract, compatibility, stored_contract
from .runtime.store import JobStore


def _contract(store, case, model, recipe_id):
    """Recover the existing worker's exact acquisition recipe from its local queue."""
    for row in store.jobs():
        if row.get("case") != case.case_id:
            continue
        contract = stored_contract(row)
        if contract.get("identity") != recipe_id or contract.get("measurement_recipe_binding") == "execution":
            continue
        recipe = contract.get("measurement_recipe") or {}
        if not recipe.get("builder") or not re.fullmatch("[0-9a-f]{64}", str(recipe.get("project_source_hash"))):
            continue
        actual = case_contract(case, model, recipe)
        if (actual["identity"] == recipe_id and compatibility(row, actual)[0]
                and contract.get("parameter_schema") == actual["parameter_schema"]):
            return actual
    return None


def enqueue_geometry_actions(campaign, plan, *, case_id, recipe_id, profile_sha256, family_id=None):
    """Enqueue full-solve actions only. Reads a persisted exact worker contract.

    No solver is started here. If an approved-builder recipe has never been
    bound in the local queue, the supplied hash cannot reconstruct the worker's
    full contract. The caller gets a per-action explanation.
    """
    if (not isinstance(case_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", case_id)
            or not isinstance(recipe_id, str) or not recipe_id.strip()
            or not isinstance(profile_sha256, str) or not re.fullmatch("[0-9a-f]{64}", profile_sha256)):
        raise ValueError("invalid case, recipe or profile identity")
    for key, value in (("case_id", case_id), ("recipe_id", recipe_id), ("profile_sha256", profile_sha256)):
        if plan.get(key) != value:
            raise ValueError(f"study plan has a different {key}")
    builder_hash = plan.get("builder_sha256")
    if builder_hash is not None and (not isinstance(builder_hash, str)
                                     or not re.fullmatch(r"[0-9a-f]{64}", builder_hash)):
        raise ValueError("builder_sha256 must be a lowercase SHA-256")
    registration = resolve_registration(case_id)
    case, model = registration.build_case(), registration.build_model()
    if case.case_id != case_id or family_id is not None and family_id != case.family.family_id:
        raise ValueError("study case/family does not match the registered model")
    actions = plan.get("actions")
    if not isinstance(actions, list):
        raise ValueError("study plan requires an actions list")
    records, seen, skipped = [], set(), []
    for action in actions:
        aid, kind = action.get("id"), action.get("kind")
        if not isinstance(aid, str) or not re.fullmatch("[0-9a-f]{64}", aid) or aid in seen:
            raise ValueError("invalid or duplicate study action ID")
        seen.add(aid)
        params = case.family.param_space.validate(action["params"])
        if params != action["params"] or model.space.validate(params) != params:
            raise ValueError("study parameters do not match registered model variables")
        if kind not in {"geometry", "interaction"}:
            reasons = {"frequency": "requires a run-specific frequency measurement",
                       "coverage": "requires a run-specific angular measurement",
                       "tolerance": "requires scenario evidence and independent-retest/cache handling"}
            skipped.append({"action_id": aid, "kind": kind, "reason": reasons.get(kind, "unsupported action")})
            continue
        if (action.get("frequency_ghz") is not None or action.get("repeat_slot") is not None
                or not model.validate_geometry(params).valid or not model.validate_em_setup(params).valid):
            raise ValueError("action is not a valid full-solve geometry")
        key = {"profile": profile_sha256, "params": params, "task": "geometry",
               "frequency_ghz": None, "run_id": None}
        metadata = {"id": aid, "kind": kind, "profile_sha256": profile_sha256,
                    "case_id": case_id, "recipe_id": recipe_id}
        if builder_hash is not None:
            key["builder_sha256"] = builder_hash
            metadata["builder_sha256"] = builder_hash
        if aid != _digest(key):
            raise ValueError("geometry action ID does not match its profile and parameters")
        records.append((params, metadata))
    enqueued = []
    if records:
        path = Path(campaign) / "queue.sqlite"
        store = JobStore(path) if path.is_file() else None
        binding = _contract(store, case, model, recipe_id) if store else None
        if binding:
            run_ids = store.enqueue([params for params, _ in records], case_id,
                                    "study-" + profile_sha256, learning_contract=binding,
                                    study_actions=[action for _, action in records])
            created = set(run_ids)
        else:
            created = set()
        for _, action in records:
            rid = "study-" + action["id"]
            result = {"action_id": action["id"], "kind": action["kind"]}
            if rid in created:
                enqueued.append({**result, "run_id": rid})
            else:
                reason = ("already queued, previously attempted, or occupied by a compatible geometry"
                          if binding else "no matching approved-builder learning contract in local queue")
                skipped.append({**result, "reason": reason})
    return {"enqueued": enqueued, "skipped": skipped}
