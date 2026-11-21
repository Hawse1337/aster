"""Assess a versioned antenna design brief and allocate a shared simulation budget."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal
from copy import deepcopy
import hashlib
import json
import math
import re
from statistics import median
from pathlib import Path
from types import SimpleNamespace

from .band_acceptance import BandAcceptanceSpec, assess_s11_requirement, assess_touchstone_requirement
from .design_history import is_real_run
from .farfield import load_coverage_snapshots
from .learning_contract import stored_contract
from .pareto import archive_from_runs
from .pattern_acceptance import AngularCoverageSpec, assess_angular_coverage
from .parametric.registry import get_case as resolve_registration
from .recommend import recommend_from_runs
from .robustness import plan_tolerance_study


def _digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _positive(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return float(value)


@dataclass(frozen=True)
class _Margin:
    kind: str
    target: float = 0.
    operator: str = ">="
    violation_scale: float = 1.

    @property
    def id(self):
        return self.kind


class DesignStudy:
    """A design-side profile; its derived metrics never change the solver's wire schema."""

    def __init__(self, case, model, profile):
        profile = deepcopy(profile)
        allowed = {"profile_id", "revision", "case_id", "recipe_id", "band", "coverage",
                   "objectives", "interaction_steps", "tolerance", "cost_seconds", "weights", "builder_sha256"}
        if set(profile) - allowed:
            raise ValueError(f"unknown study settings: {sorted(set(profile) - allowed)}")
        if profile.get("case_id") != case.case_id:
            raise ValueError("profile and case differ")
        if not all(isinstance(profile.get(k), str) and profile[k].strip()
                   for k in ("profile_id", "recipe_id")) or type(profile.get("revision")) is not int or profile["revision"] < 1:
            raise ValueError("profile_id, recipe_id and positive integer revision are required")
        self.case, self.model, self.profile = case, model, profile
        self.builder_hash = profile.get("builder_sha256")
        if self.builder_hash is not None and (not isinstance(self.builder_hash, str)
                                             or not re.fullmatch("[0-9a-f]{64}", self.builder_hash)):
            raise ValueError("builder_sha256 must be a lowercase SHA-256")
        self.identity = _digest(profile)
        self.space = case.family.param_space
        self.specs = list(self.space)
        self.names = [spec.name for spec in self.specs]
        def parameter_schema(space):
            return [(s.name, s.unit, s.kind, s.low, s.high, s.default, s.choices, s.integer_step) for s in space]
        if parameter_schema(model.space) != parameter_schema(self.space):
            raise ValueError("model and case have different parameter spaces")
        band = dict(profile.get("band") or {})
        self.width = band.pop("required_width_ghz", None)
        self.band = BandAcceptanceSpec(**band) if band else None
        self.coverage = AngularCoverageSpec(**profile["coverage"]) if profile.get("coverage") else None
        if not (self.band or self.coverage):
            raise ValueError("a band or a coverage target is required")
        low, high = case.frequency_ghz
        if self.band:
            a, b = self.band.band_ghz
            if a < low or b > high or self.width is not None and _positive(self.width, "width") > b-a+1e-9:
                raise ValueError("band or requested width lies outside the case frequency range")
        if self.coverage and any(not low <= f <= high for f in self.coverage.frequencies_ghz):
            raise ValueError("coverage frequency lies outside the case frequency range")
        steps = profile.get("interaction_steps", {})
        if steps and (len(steps) != 2 or any(k not in self.names for k in steps)):
            raise ValueError("interaction_steps requires two different registered parameters")
        for p in self.specs:
            if p.name in steps and (p.kind == "choice" or not _positive(steps[p.name], p.name)
                                    or p.kind == "int" and not float(steps[p.name]).is_integer()):
                raise ValueError("interactions require numeric steps in the parameter's own units")
        self.derived = (["band_margin_db"] if self.band else []) + (
            ["min_realized_gain_dbi", "coverage_margin_db"] if self.coverage else [])
        directions = {o.kind: o.direction for o in case.objectives}
        directions.update(dict.fromkeys(self.derived, "maximize"))
        objectives = profile.get("objectives", self.derived)
        if not isinstance(objectives, list) or not objectives or any(k not in directions for k in objectives) or len(set(objectives)) != len(objectives):
            raise ValueError("objectives must be distinct case objectives or configured response metrics")
        self.objectives = [SimpleNamespace(kind=k, direction=directions[k]) for k in objectives]
        self.constraints = list(case.constraints) + [_Margin(k) for k in self.derived if k.endswith("margin_db")]
        if self.profile.get("tolerance"):
            plan_tolerance_study(self.space, self.space.default(), **self.profile["tolerance"])
        self.costs = profile.get("cost_seconds", {})
        self.weights = profile.get("weights", {})
        kinds = {"geometry", "frequency", "coverage", "interaction", "tolerance"}
        for mapping in (self.costs, self.weights):
            if not isinstance(mapping, dict) or set(mapping) - kinds:
                raise ValueError("unknown action in costs or weights")
            for name, value in mapping.items():
                _positive(value, name)

    def _clean(self, params):
        result = self.space.validate(params)
        if any(s.kind == "int" and result[s.name] != params[s.name] for s in self.specs):
            raise ValueError("non-integral design variable")
        if not self.model.validate_geometry(result).valid or not self.model.validate_em_setup(result).valid:
            raise ValueError("case geometry or EM setup check failed")
        return result

    def _assess(self, row):
        # Strip client-supplied derived scalars. Only re-derived values can enter a new profile.
        entry = archive_from_runs(self.case, [row])
        baseline = next(iter(entry.entries.values()), None)
        metrics = {k: v for k, v in (baseline.metrics if baseline else {}).items() if k not in self.derived}
        band = coverage = None
        if self.band:
            options = dict(source="MEASURED", required_width_ghz=self.width)
            if row.get("touchstone"):
                artifact = row["touchstone"]
                if row.get("s11_points") or not isinstance(artifact, dict) or artifact.get("run_id") != row["run_id"]:
                    raise ValueError("Touchstone requires one run-bound manifest (path, run_id, sha256)")
                band = assess_touchstone_requirement(artifact["path"], self.band, **options)
                if band["artifact"]["sha256"].lower() != str(artifact.get("sha256", "")).lower():
                    raise ValueError("Touchstone file digest differs from the run manifest")
            else:
                band = assess_s11_requirement(row.get("s11_points", []), self.band, **options)
            if band["coverage_complete"]:
                metrics["band_margin_db"] = band["worst_guarded_margin_db"]
        if self.coverage:
            snapshots = list(row.get("coverage_snapshots", []))
            for file in row.get("coverage_files", []):
                imported = load_coverage_snapshots(file)
                if any(s.get("run_id") != row["run_id"] for s in imported):
                    raise ValueError("external far-field data must identify the same run")
                snapshots.extend(imported)
            if any(s.get("run_id", row["run_id"]) != row["run_id"] for s in snapshots):
                raise ValueError("far-field data belong to a different run")
            coverage = assess_angular_coverage(snapshots, self.coverage)
            metrics.update({k: v for k, v in coverage["metrics"].items() if k in self.derived and v is not None})
        base_ok = baseline is not None and baseline.feasible
        metrics["case_evidence_margin"] = 1. if base_ok else -1.
        passed = base_ok and (not band or band["measured_pass"]) and (not coverage or coverage["status"] == "SAMPLED_PASS")
        complete = (baseline is not None and all(metrics.get(c.kind) is not None for c in [*self.case.constraints, *self.case.objectives])
                    and (not band or band["coverage_complete"])
                    and (not coverage or "coverage_margin_db" in coverage["metrics"]))
        return {"run_id": row["run_id"], "params": row["params"], "metrics": metrics, "base_pass": base_ok,
                "complete": bool(complete),
                "sampled_pass": bool(passed), "band": band, "coverage": coverage}

    def plan_campaign(self, campaign, **options):
        from .design_history import load_study_history, read_study_reservations
        return self.plan(load_study_history(campaign, self.case), **options,
                         queue_reservations=read_study_reservations(campaign, self.case, self.profile["recipe_id"]))

    def plan(self, runs, *, budget_seconds, count=4, seed=0, pending_ids=(), anchor_run_id=None,
             queue_reservations=()):
        budget = _positive(budget_seconds, "budget_seconds")
        if type(count) is not int or not 1 <= count <= 64:
            raise ValueError("count must be between 1 and 64")
        if isinstance(pending_ids, (str, bytes)):
            raise ValueError("pending_action_ids must contain action identifiers")
        pending_ids = tuple(pending_ids)
        if not all(isinstance(k, str) and re.fullmatch("[0-9a-f]{64}", k) for k in pending_ids):
            raise ValueError("pending_action_ids must contain action identifiers")
        pending_ids = set(pending_ids)
        rows = list(runs)
        def matches_source(row):
            contract = stored_contract(row)
            cases = {c for c in (row.get("case"), row.get("case_id"), row.get("case_name"), contract.get("case_id")) if c}
            recipes = {c for c in (contract.get("identity"), row.get("recipe_id")) if c}
            return (cases == {self.case.case_id} and recipes == {self.profile["recipe_id"]}
                    and str(row.get("source", "")).upper() == "MEASURED" and not row.get("invalid") and is_real_run(row))
        recorded = [row["builder_sha256"] for row in rows if matches_source(row) and row.get("builder_sha256") is not None]
        if any(not isinstance(h, str) or not re.fullmatch("[0-9a-f]{64}", h) for h in recorded):
            raise ValueError("invalid recorded builder_sha256")
        hashes = set(recorded)
        if len(hashes) > 1 and not self.builder_hash:
            raise ValueError("campaign contains different builder revisions; select builder_sha256 in the profile")
        builder_hash = self.builder_hash or next(iter(hashes), None)
        seen, occupied, active = set(), set(), set()
        pending_queue = []
        for reservation in queue_reservations:
            if builder_hash and reservation["builder_sha256"] not in (None, builder_hash):
                continue
            if reservation["action_id"]:
                pending_ids.add(reservation["action_id"])
            status = reservation["status"]
            if status in {"QUEUED", "RUNNING", "PREPARING", "SOLVING", "APPROVAL_REQUIRED", "RECOVERY_REQUIRED"}:
                pending_queue.append(reservation)
                active.add(_digest(self.space.validate(reservation["params"])))
            elif status == "COMPLETED":
                # The queue already reserves this geometry. Only the guard/history
                # join can supply completed evidence for training or the front.
                occupied.add(_digest(self.space.validate(reservation["params"])))
        occupied.update(active)
        excluded, compatible, reports = [], [], []
        for row in rows:
            rid = row.get("run_id")
            if not matches_source(row) or builder_hash and row.get("builder_sha256") != builder_hash:
                excluded.append({"run_id": rid, "reason": "incompatible case, recipe or evidence source"})
                continue
            if not isinstance(rid, str) or not rid or rid in seen:
                raise ValueError("each compatible row requires a unique run_id")
            seen.add(rid)
            try:
                params = self._clean(row.get("params") or {})
            except (ValueError, KeyError, TypeError) as exc:
                excluded.append({"run_id": rid, "reason": str(exc)})
                continue
            row = {**row, "params": params, "case": self.case.case_id}
            if row.get("status") in {"COMPLETED", "QUEUED", "APPROVAL_REQUIRED", "PREPARING", "RUNNING", "SOLVING", "RECOVERY_REQUIRED"}:
                occupied.add(_digest(params))
                if row.get("status") != "COMPLETED":
                    active.add(_digest(params))
            if row.get("status") == "COMPLETED":
                report = self._assess(row)
                reports.append(report)
                row = {**row, "metrics": report["metrics"]}
            compatible.append(row)

        def objective_vector(report, field="metrics"):
            values = report[field]
            return tuple((-1 if o.direction == "minimize" else 1) * values[o.kind]
                         if values.get(o.kind) is not None else -math.inf for o in self.objectives)

        feasible = [r for r in reports if r["sampled_pass"] and all(math.isfinite(v) for v in objective_vector(r))]
        def front(rows, field="metrics"):
            vectors = [objective_vector(r, field) for r in rows]
            return [row["run_id"] for row, v in zip(rows, vectors) if not any(
                all(a >= b for a,b in zip(other, v)) and any(a > b for a,b in zip(other, v)) for other in vectors)]
        nominal_front = front(feasible)
        ranked = sorted(reports, key=lambda r: (r["run_id"] in nominal_front, r["base_pass"],
                                                 objective_vector(r), r["run_id"]), reverse=True)
        if anchor_run_id is not None:
            if not any(r["run_id"] == anchor_run_id for r in ranked):
                raise ValueError("anchor_run_id must identify a compatible measured design")
            ranked.sort(key=lambda r: r["run_id"] != anchor_run_id)
        from .action_cost import fit_action_scorer
        from .surrogate.feasibility import fit_feasibility
        scorer = fit_action_scorer(compatible, self.space, case_id=self.case.case_id,
                                   recipe_id=self.profile["recipe_id"], cost_seconds=self.costs)
        classifier = fit_feasibility(self.space, [(r["params"], r["sampled_pass"]) for r in reports if r["complete"]])
        offers, rejected = [], []
        def action_id(kind, params, frequency=None, run_id=None, repeat_slot=None):
            geometry = kind in {"geometry", "interaction", "tolerance"}
            key = {"profile": self.identity, "params": params, "task": "geometry" if geometry else kind,
                   "frequency_ghz": frequency, "run_id": None if geometry else run_id}
            if builder_hash:
                key["builder_sha256"] = builder_hash
            if repeat_slot is not None:
                key["repeat_slot"] = repeat_slot
            return _digest(key)

        def available_geometry(params):
            clean = self._clean(params)
            return _digest(clean) not in occupied and action_id("geometry", clean) not in pending_ids

        def offer(kind, params, value, reason, *, run_id=None, frequency=None, detail=None, repeat_slot=None):
            try:
                clean = self._clean(params)
            except (ValueError, TypeError, KeyError) as exc:
                rejected.append({"kind": kind, "params": params, "reason": str(exc)})
                return
            is_geometry = kind in {"geometry", "interaction", "tolerance"}
            if is_geometry and _digest(clean) in (active if kind == "tolerance" else occupied):
                return
            estimate = scorer.estimate(kind, clean)
            probability = classifier.predict(clean) if kind == "geometry" else None
            # Preserve a positive exploration share even if observed neighbours were all infeasible.
            feasibility_weight = (1. + probability)/2. if probability is not None else 1.
            offers.append({"id": action_id(kind, clean, frequency, run_id, repeat_slot),
                           "kind": kind, "params": clean, "run_id": run_id,
                           "frequency_ghz": frequency, "reason": reason, "detail": detail,
                           "estimated_seconds": estimate["estimated_seconds"], "estimate": estimate,
                           "feasibility_probability": probability,
                           "priority": value*self.weights.get(kind, 1.)*estimate["success_factor"]*feasibility_weight})

        learning_case = SimpleNamespace(case_id=self.case.case_id, family=self.case.family,
            objectives=self.objectives, constraints=self.constraints + [_Margin("case_evidence_margin")])
        base = ranked[0]["params"] if ranked else self.space.default()
        bounds = [(0, len(p.choices)-1) if p.kind == "choice" else (p.low, p.high) for p in self.specs]
        geometry = recommend_from_runs(compatible + pending_queue, names=self.names, bounds=bounds, parameter_specs=self.specs,
            case=learning_case, count=count, seed=seed, defaults=base, validator=available_geometry,
            geometry_estimator=lambda p: scorer.estimate("geometry", p))
        for item in geometry["recommendations"]:
            offer("geometry", item["params"], 1., geometry["method"])
        diagnostics = []
        for rank, row in enumerate(ranked):
            params, rid = row["params"], row["run_id"]
            # Concentrate verification on the best measured designs. Include nominal front ties.
            refine = rank < 2 or rid in nominal_front
            importance = 1. / (1. + rank / 4.)
            if self.band and refine:
                for i, point in enumerate(row["band"]["suggestions"]):
                    offer("frequency", params, 2.*importance/(1.+i), point["reason"],
                          run_id=rid, frequency=point["frequency_ghz"], detail=point)
            if self.coverage and refine:
                unverified = row["coverage"]["unverified_frequencies_ghz"]
                if row["coverage"].get("reason") and not row["coverage"]["metrics"]:
                    unverified = self.coverage.frequencies_ghz
                for f in unverified:
                    offer("coverage", params, 2.*importance, "measure configured angular region",
                          run_id=rid, frequency=f,
                          detail={"quantity": "dB(RealizedGainTotal)", "coordinate_system": self.coverage.coordinate_system,
                                  "theta_deg": list(self.coverage.theta_deg), "phi_deg": list(self.coverage.phi_deg),
                                  "max_step_deg": self.coverage.max_step_deg})
            if self.profile.get("tolerance") and (refine or row["sampled_pass"]):
                tol = self.profile["tolerance"]
                # case_evidence_margin preserves the guard verdict, including multiple limits of the same kind.
                checks = {c.kind: (c.operator, c.target) for c in self.constraints}
                checks["case_evidence_margin"] = (">=", 0.)
                robustness = plan_tolerance_study(self.space, params, measurements=compatible,
                    keys=[o.kind for o in self.objectives], constraints=checks,
                    objective_directions={o.kind: o.direction for o in self.objectives}, **tol)
                diagnostics.append({"run_id": rid, **robustness})
                for point in robustness["pending"]:
                    if not refine:
                        continue
                    repeat = tol.get("required_repeats", 1) - point["remaining_repeats"] + 1
                    offer("tolerance", point["params"], 1.5*importance, "unverified manufacturing scenario",
                          run_id=rid, detail=point, repeat_slot=repeat if _digest(point["params"]) in occupied else None)
        interaction = None
        if ranked and self.profile.get("interaction_steps"):
            (x, dx), (y, dy) = self.profile["interaction_steps"].items()
            corners = {}
            for sx, sy in ((-1,-1), (1,1), (-1,1), (1,-1)):
                p = {**base, x: float(Decimal(str(base[x])) + sx*Decimal(str(dx))),
                     y: float(Decimal(str(base[y])) + sy*Decimal(str(dy)))}
                corners[sx, sy] = [r for r in reports if r["params"] == p and r["complete"]]
                offer("interaction", p, 1.5,
                      "paired parameter perturbation", run_id=ranked[0]["run_id"], detail=[x,y])
            contrasts = {}
            for o in self.objectives:
                if all(v and all(r["metrics"].get(o.kind) is not None for r in v) for v in corners.values()):
                    contrasts[o.kind] = sum(sx*sy*median(r["metrics"][o.kind] for r in v)
                                            for (sx,sy), v in corners.items())
            interaction = {"anchor_run_id": ranked[0]["run_id"], "parameters": [x,y],
                           "measured_corners": sum(bool(v) for v in corners.values()), "contrasts": contrasts}
        # Explainable configurable priority per estimated second, not a calibrated utility.
        missing_cost = sorted({a["kind"] for a in offers if not a["estimated_seconds"]})
        eligible = [a for a in offers if a["estimated_seconds"] and a["id"] not in pending_ids]
        for a in eligible:
            a["priority_per_second"] = a["priority"] / a["estimated_seconds"]
        eligible.sort(key=lambda a: -a["priority_per_second"])
        selected, selected_ids, total = [], set(), 0.
        for a in eligible:
            if a["id"] in selected_ids or total + a["estimated_seconds"] > budget:
                continue
            selected.append(a)
            selected_ids.add(a["id"])
            total += a["estimated_seconds"]
            if len(selected) == count:
                break
        robust_rows = [r for r in diagnostics if r["complete"] and r["scenario_pass_fraction"] == 1
                       and any(f["run_id"] == r["run_id"] for f in feasible)]
        return {"profile_id": self.profile["profile_id"], "revision": self.profile["revision"], "builder_sha256": builder_hash,
                "profile_sha256": self.identity, "case_id": self.case.case_id, "recipe_id": self.profile["recipe_id"],
                "assessments": reports, "nominal_front": nominal_front,
                "tested_tolerance_front": front(robust_rows, "verified_worst_objectives"),
                "tolerance_studies": diagnostics, "excluded": excluded,
                "recommendation_method": geometry["method"], "trained_on": geometry["trained_on"],
                "actions": selected, "budget_seconds": budget, "estimated_seconds": total,
                "missing_cost_estimates": missing_cost, "geometry_rejections": rejected,
                "selection_policy": "priority_per_estimated_second",
                "offered_actions": len(offers), "remaining_estimated_seconds": budget - total,
                "interaction": interaction, "feasibility_validation": classifier.diagnostics}


def cli(parser):
    parser.add_argument("--profile", required=True, type=Path)
    history = parser.add_mutually_exclusive_group(required=True)
    history.add_argument("--history", type=Path)
    history.add_argument("--campaign", type=Path)
    parser.add_argument("--enqueue-geometry", action="store_true",
                        help="queue supported geometry actions in the local campaign")
    parser.add_argument("--budget-seconds", required=True, type=float)
    parser.add_argument("--count", default=4, type=int)
    parser.add_argument("--seed", default=0, type=int)
    parser.add_argument("--anchor-run")
    parser.add_argument("--output", type=Path)


def run_cli(args):
    profile = json.loads(args.profile.read_text(encoding="utf-8-sig"))
    name = profile["case_id"]
    registration = resolve_registration(name)
    study = DesignStudy(registration.build_case(), registration.build_model(), profile)
    if args.output and (args.output.suffix.lower() != ".json" or args.output.exists() or not args.output.parent.is_dir()):
        raise ValueError("output must be a new .json file in an existing directory")
    options = dict(budget_seconds=args.budget_seconds, count=args.count, seed=args.seed,
                   anchor_run_id=args.anchor_run)
    if args.history:
        if args.enqueue_geometry:
            raise ValueError("enqueue requires a real local campaign")
        history = json.loads(args.history.read_text(encoding="utf-8-sig"))
        for row in history["runs"]:
            if row.get("touchstone"):
                entry = row["touchstone"]
                if not isinstance(entry, dict):
                    raise ValueError("touchstone needs path, run_id and sha256")
                entry["path"] = args.history.parent / entry["path"]
            if row.get("coverage_files"):
                row["coverage_files"] = [args.history.parent / file for file in row["coverage_files"]]
        result = study.plan(history["runs"], pending_ids=history.get("pending_action_ids", []), **options)
    else:
        if not args.campaign.is_dir():
            raise ValueError("campaign directory does not exist")
        result = study.plan_campaign(args.campaign, **options)
    if args.enqueue_geometry:
        from .study_queue import enqueue_geometry_actions
        result["queue"] = enqueue_geometry_actions(args.campaign, result, case_id=name,
            recipe_id=profile["recipe_id"], profile_sha256=study.identity, family_id=study.case.family.family_id)
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(encoded)
    else:
        print(encoded)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    cli(parser)
    try:
        run_cli(parser.parse_args())
    except (KeyError, ValueError, TypeError, OSError, ImportError) as exc:
        parser.exit(2, f"{exc}\n")
