"""Offline evidence gate for *measured* coarse-to-fine screening.

Run ``python -m aster.surrogate.coarse_validation --help``. No solver, ledger
write, automatic recipe inference, or promotion of a prediction to a measurement.
The permission is local to the declared observable/recipe/parameter support; a
held-out replay does not establish population-wide safety or realized savings.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Any, Mapping, Sequence
import warnings

import numpy as np

from ..learning_contract import _hash, stored_contract
from .multifidelity import MultiFidelityModel, MultiFidelitySample

UNKNOWN = {"", "unknown", "none", "unspecified"}
FIDELITY_RECIPE_KEYS = {"execution_policy", "solver_accuracy", "mesh_settings",
                        "adaptive_settings", "setup_name"}


@dataclass(frozen=True)
class GatePolicy:
    minimum_pairs: int = 24
    minimum_train: int = 8
    minimum_calibration: int = 6
    minimum_test: int = 6
    minimum_test_feasible: int = 3
    minimum_spearman: float = 0.95
    minimum_saving_fraction: float = 0.05
    seed: int = 0


def _known(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() not in UNKNOWN


def _finite(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean_is_not_a_measurement")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite_measurement")
    return result


def _metric_specs(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    metrics = list(manifest.get("metrics") or [])
    if not metrics or not all(isinstance(m, dict) for m in metrics) or len({m.get("name") for m in metrics}) != len(metrics):
        raise ValueError("unique metric specifications are required")
    for metric in metrics:
        if not _known(metric.get("name")) or not _known(metric.get("unit")):
            raise ValueError("metric name and unit are required")
        if metric.get("operator") not in {">=", "<="}:
            raise ValueError("metric operator must be >= or <=")
        _finite(metric["threshold"])
        if _finite(metric["maximum_error"]) <= 0:
            raise ValueError("maximum_error must be positive in the metric unit")
    return metrics


def _record(row: Mapping[str, Any], metrics: Sequence[Mapping[str, Any]],
            recipes: set[str]) -> dict[str, Any]:
    if str(row.get("status", "")).upper() != "COMPLETED":
        raise ValueError("not_completed")
    if str(row.get("source", "")).lower() != "measured":
        raise ValueError("not_explicit_measured")
    if not _known(row.get("run_id")):
        raise ValueError("missing_run_id")
    contract = stored_contract(row)
    if not contract:
        raise ValueError("legacy_missing_contract")
    keys = ("case_id", "family_id", "structure_hash", "recipe_hash", "identity")
    if contract.get("version") != 1 or not all(_known(contract.get(k)) for k in keys):
        raise ValueError("incomplete_contract")
    recipe = contract.get("measurement_recipe")
    if not isinstance(recipe, dict) or not recipe or _hash(recipe) != contract["recipe_hash"]:
        raise ValueError("invalid_recipe_hash")
    expected = _hash({k: contract[k] for k in ("case_id", "family_id", "structure_hash", "recipe_hash")})
    if expected != contract["identity"]:
        raise ValueError("invalid_contract_identity")
    if contract["recipe_hash"] not in recipes:
        raise ValueError("unselected_recipe")
    schema = contract.get("parameter_schema")
    if not isinstance(schema, list) or not schema:
        raise ValueError("missing_parameter_schema")
    names = [p["name"] for p in schema]
    params = row.get("params") or {}
    # No default fill, extra free geometry parameter, rounding, or candidate-id pairing.
    if set(params) != set(names) or len(set(names)) != len(names):
        raise ValueError("parameter_schema_mismatch")
    params = {name: _finite(params[name]) for name in names}
    params = {name: 0.0 if value == 0 else value for name, value in params.items()}
    measurement = row.get("measurement") or {}
    axis = np.asarray(measurement.get("frequency_ghz", []), dtype=float)
    if axis.ndim != 1 or not len(axis) or not np.all(np.isfinite(axis)) or np.any(axis <= 0) or np.any(np.diff(axis) <= 0):
        raise ValueError("missing_or_invalid_observed_frequency_axis")
    if not _known(measurement.get("reference_plane")):
        raise ValueError("unknown_reference_plane")
    definitions = measurement.get("metric_definitions") or {}
    units = measurement.get("metric_units") or {}
    for metric in metrics:
        name = metric["name"]
        if units.get(name) != metric["unit"] or not _known(definitions.get(name)):
            raise ValueError("unknown_or_incompatible_metric_unit_or_definition")
    values = [_finite((row.get("metrics") or {})[m["name"]]) for m in metrics]
    seconds = _finite(row.get("total_seconds"))
    if seconds <= 0 or row.get("cost_scope") != "workflow_wall_clock" or not _known(row.get("timing_source")):
        raise ValueError("missing_measured_workflow_cost")
    comparable = {"frequency_ghz": axis.tolist(), "reference_plane": measurement["reference_plane"],
                  "metric_units": {m["name"]: units[m["name"]] for m in metrics},
                  "metric_definitions": {m["name"]: definitions[m["name"]] for m in metrics}}
    geometry = {"case_id": contract["case_id"], "family_id": contract["family_id"],
                "structure_hash": contract["structure_hash"], "parameter_schema": schema,
                "params": params}
    return {"geometry_key": _hash(geometry), "geometry_contract": {k: v for k, v in geometry.items() if k != "params"},
            "params": params, "names": names, "values": values, "seconds": seconds,
            "run_id": row["run_id"], "contract": contract, "measurement": comparable}


def pair_records(manifest: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Strict same-geometry pairing; ambiguous repeats are excluded, never averaged."""
    if manifest.get("version") != 1:
        raise ValueError("coarse validation manifest version must be 1")
    metrics = _metric_specs(manifest)
    rows = manifest.get("records", [])
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("records must be a list of measurement objects")
    low, high = manifest.get("low_recipe_hash"), manifest.get("high_recipe_hash")
    if not _known(low) or not _known(high) or low == high:
        raise ValueError("explicit different low/high recipe hashes are required")
    buckets: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    rejected: list[dict[str, Any]] = []
    run_ids = Counter(str(r.get("run_id")) for r in rows)
    for row in rows:
        try:
            if run_ids[str(row.get("run_id"))] != 1:
                raise ValueError("ambiguous_duplicate_run_id")
            parsed = _record(row, metrics, {low, high})
            buckets[parsed["geometry_key"]][parsed["contract"]["recipe_hash"]].append(parsed)
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            rejected.append({"run_id": row.get("run_id"), "reason": str(exc)})
    pairs = []
    for key, bucket in sorted(buckets.items()):
        a, b = bucket.get(low, []), bucket.get(high, [])
        reason = ""
        if len(a) != 1 or len(b) != 1:
            reason = "unpaired_or_repeated_geometry"
        elif a[0]["measurement"] != b[0]["measurement"]:
            reason = "different_axis_reference_unit_or_definition"
        else:
            left, right = a[0]["contract"]["measurement_recipe"], b[0]["contract"]["measurement_recipe"]
            changed = {k for k in set(left) | set(right) if left.get(k) != right.get(k)}
            if not changed or changed - FIDELITY_RECIPE_KEYS:
                reason = "recipe_changes_more_than_fidelity"
        if reason:
            rejected.append({"geometry_key": key, "run_ids": [p["run_id"] for rows in bucket.values() for p in rows],
                             "reason": reason})
        else:
            pairs.append({"geometry_key": key, "low": a[0], "high": b[0]})
    # A validation must not pool families, geometry source versions, observable axes or recipes.
    if pairs:
        signatures = {_hash([p["low"]["geometry_contract"], p["low"]["measurement"]]) for p in pairs}
        if len(signatures) != 1:
            rejected.extend({"geometry_key": p["geometry_key"], "reason": "different_validation_domain"} for p in pairs)
            pairs = []
    return pairs, rejected


def _ranks(values: np.ndarray) -> np.ndarray:
    unique, inverse, counts = np.unique(values, return_inverse=True, return_counts=True)
    del unique
    return (np.cumsum(counts) - (counts + 1) / 2)[inverse]


def _ranking(left: np.ndarray, right: np.ndarray) -> dict[str, Any]:
    a, b = _ranks(left), _ranks(right)
    identifiable = bool(np.std(a) > 0 and np.std(b) > 0)
    correlation = float(np.corrcoef(a, b)[0, 1]) if identifiable else None
    discordant = sum((left[i] - left[j]) * (right[i] - right[j]) < -1e-12
                     for i in range(len(left)) for j in range(i))
    return {"spearman": correlation, "discordant_pairs": int(discordant), "identifiable": identifiable}


def _supported(x: np.ndarray, train: np.ndarray) -> bool:
    span = np.ptp(train, axis=0)
    scale = np.where(span > 1e-12, span, 1.0)
    unit = (train - train.min(axis=0)) / scale
    point = (x - train.min(axis=0)) / scale
    if np.any(point < -1e-10) or np.any(point > np.max(unit, axis=0) + 1e-10):
        return False
    center = unit.mean(axis=0)
    _, singular, basis = np.linalg.svd(unit - center, full_matrices=False)
    active = basis[singular > max(1e-10, singular[0] * 1e-8)]
    residual = point - center - ((point - center) @ active.T) @ active
    if np.linalg.norm(residual) > 1e-7:
        return False
    distances = np.linalg.norm(unit[:, None] - unit[None, :], axis=2)
    np.fill_diagonal(distances, np.inf)
    radius = min(0.35, 1.5 * float(np.median(np.min(distances, axis=1))))
    return bool(np.min(np.linalg.norm(unit - point, axis=1)) <= radius + 1e-12)


def _reject(prediction: np.ndarray, bounds: np.ndarray, metrics: Sequence[Mapping[str, Any]]) -> list[bool]:
    return [bool(value + error < m["threshold"] if m["operator"] == ">=" else value - error > m["threshold"])
            for value, error, m in zip(prediction, bounds, metrics)]


class CoarseScreeningGate:
    """Calibrate on separate designs and audit on an untouched design holdout."""

    def __init__(self, policy: GatePolicy | None = None):
        self.policy = policy or GatePolicy()
        self.models: list[MultiFidelityModel] = []
        self.report: dict[str, Any] = {}

    def fit(self, manifest: Mapping[str, Any]) -> "CoarseScreeningGate":
        self.models = []
        self.manifest = deepcopy(dict(manifest))
        self.metrics = _metric_specs(self.manifest)
        policy = self.policy
        counts = (policy.minimum_pairs, policy.minimum_train, policy.minimum_calibration,
                  policy.minimum_test, policy.minimum_test_feasible)
        if any(isinstance(c, bool) or not isinstance(c, int) for c in counts):
            raise ValueError("sample thresholds must be integers")
        if min(policy.minimum_train, policy.minimum_calibration, policy.minimum_test) < 2 or policy.minimum_pairs < 6:
            raise ValueError("at least two independent designs per split are required")
        if policy.minimum_test_feasible < 1:
            raise ValueError("high-feasible holdout evidence is required")
        if not 0 <= policy.minimum_spearman <= 1 or not 0 < policy.minimum_saving_fraction < 1:
            raise ValueError("invalid ranking or savings threshold")
        pairs, rejected = pair_records(self.manifest)
        self.report = {"version": 1, "source": "PREDICTED", "kind": "coarse_screening_validation",
                       "usable_for_screening": False, "feasibility_scope": "configured_thresholds_only",
                       "evidence_manifest_hash": _hash(self.manifest),
                       "low_recipe_hash": self.manifest.get("low_recipe_hash"),
                       "high_recipe_hash": self.manifest.get("high_recipe_hash"),
                       "policy": asdict(policy), "metrics": self.metrics, "paired_designs": len(pairs),
                       "excluded": rejected, "reasons": [],
                       "bound_basis": "maximum absolute error on separate calibration designs; not calibrated population coverage",
                       "limitation": "Held-out replay and local support checks do not guarantee no future false rejection or realized savings."}
        # Failed/censored/unpaired executions still consume budget. Do not make
        # a cheap workflow appear cheap by counting successful pairs alone.
        selected_rows = {str(r.get("run_id")): r for r in self.manifest["records"]
                         if str(r.get("source", "")).lower() == "measured"
                         and stored_contract(r).get("recipe_hash") in
                         {self.manifest["low_recipe_hash"], self.manifest["high_recipe_hash"]}}
        measured_seconds = 0.0
        incomplete_cost = []
        for run_id, row in selected_rows.items():
            try:
                duration = _finite(row.get("total_seconds"))
                if (str(row.get("status", "")).upper() != "COMPLETED" or duration <= 0
                        or row.get("cost_scope") != "workflow_wall_clock" or not _known(row.get("timing_source"))):
                    raise ValueError("not_a_complete_workflow_timing")
                measured_seconds += duration
            except (ValueError, TypeError, OverflowError):
                incomplete_cost.append(run_id)
        self.report["cost_input_audit"] = {"selected_run_ids": sorted(selected_rows),
                                            "complete_workflow_seconds": measured_seconds,
                                            "incomplete_or_failed_run_ids": incomplete_cost}
        if incomplete_cost:
            self.report["reasons"].append("incomplete_or_failed_workflow_cost_evidence")
        needed = max(policy.minimum_pairs, policy.minimum_train + policy.minimum_calibration + policy.minimum_test)
        if len(pairs) < needed:
            self.report["reasons"].append("insufficient_independent_paired_designs")
            return self
        # Hash-based seeded shuffle is invariant to row order, candidate aliases and repeats.
        pairs.sort(key=lambda p: hashlib.sha256(f'{policy.seed}:{p["geometry_key"]}'.encode()).hexdigest())
        ncal = max(policy.minimum_calibration, len(pairs) // 4)
        ntest = max(policy.minimum_test, len(pairs) // 4)
        train, calibration, test = pairs[:-(ncal + ntest)], pairs[-(ncal + ntest):-ntest], pairs[-ntest:]
        self.train_x = np.array([[p["low"]["params"][n] for n in p["low"]["names"]] for p in train])
        self.prototype = train[0]["low"]
        self.report["validation_domain"] = deepcopy(self.prototype["geometry_contract"])
        self.report["measurement"] = deepcopy(self.prototype["measurement"])
        self.report["measurement_recipes"] = {tier: deepcopy(train[0][tier]["contract"]["measurement_recipe"])
                                              for tier in ("low", "high")}
        self.report["splits"] = {name: [{"geometry_key": p["geometry_key"], "low_run": p["low"]["run_id"],
                                        "high_run": p["high"]["run_id"]} for p in rows]
                                 for name, rows in (("train", train), ("calibration", calibration), ("test", test))}
        calibration_errors = []
        prediction_columns = []
        compute_started = time.perf_counter()
        inference_seconds = 0.0
        try:
            with warnings.catch_warnings(record=True) as caught:
                for index, metric in enumerate(self.metrics):
                    del metric
                    samples = [MultiFidelitySample([p[tier]["params"][n] for n in p[tier]["names"]],
                                                   p[tier]["values"][index], tier, p["geometry_key"])
                               for p in train for tier in ("low", "high")]
                    model = MultiFidelityModel().fit(samples)
                    predictions = []
                    for rows in (calibration, test):
                        x = np.array([[p["low"]["params"][n] for n in p["low"]["names"]] for p in rows])
                        prediction_started = time.perf_counter()
                        predictions.append(model.predict_from_low(x, np.array([p["low"]["values"][index] for p in rows]))[0])
                        if rows is test:
                            inference_seconds += time.perf_counter() - prediction_started
                    calibration_errors.append(np.abs(predictions[0] - np.array([p["high"]["values"][index] for p in calibration])))
                    prediction_columns.append(predictions[1])
                    self.models.append(model)
                self.report["fit_warnings"] = sorted({str(w.message) for w in caught})
        except Exception as exc:
            self.models = []
            self.report["reasons"].append("model_fit_failed")
            self.report["model_error"] = f"{type(exc).__name__}: {exc}"
            return self
        self.bounds = np.array([max(float(np.max(e)), 1e-9) for e in calibration_errors])
        predictions = np.column_stack(prediction_columns)
        truth = np.array([p["high"]["values"] for p in test])
        coarse = np.array([p["low"]["values"] for p in test])
        metric_reports = []
        for j, metric in enumerate(self.metrics):
            error = np.abs(predictions[:, j] - truth[:, j])
            raw_rank, corrected_rank = _ranking(coarse[:, j], truth[:, j]), _ranking(predictions[:, j], truth[:, j])
            metric_reports.append({"name": metric["name"], "unit": metric["unit"], "mae": float(np.mean(error)),
                                   "rmse": float(np.sqrt(np.mean(error ** 2))), "maximum_error": float(max(error)),
                                   "bound_holdout_coverage": float(np.mean(error <= self.bounds[j] + 1e-7)),
                                   "calibration_absolute_bound": float(self.bounds[j]), "coarse_ranking": raw_rank,
                                   "corrected_ranking": corrected_rank})
            if max(float(max(error)), self.bounds[j]) > metric["maximum_error"]:
                self.report["reasons"].append(f'error_exceeds_tolerance:{metric["name"]}')
            if float(max(error)) > self.bounds[j] + 1e-7:
                self.report["reasons"].append(f'heldout_error_exceeds_calibration_bound:{metric["name"]}')
            if metric.get("check_ranking", True) and any(not r["identifiable"] or r["discordant_pairs"] or r["spearman"] < policy.minimum_spearman
                                                       for r in (raw_rank, corrected_rank)):
                self.report["reasons"].append(f'ranking_not_preserved:{metric["name"]}')
        self.report["holdout_metrics"] = metric_reports
        replay = []
        false_rejects = 0
        threshold_misses = 0
        for i, pair in enumerate(test):
            x = np.array([pair["low"]["params"][n] for n in pair["low"]["names"]])
            supported = _supported(x, self.train_x)
            decisions = _reject(predictions[i], self.bounds, self.metrics) if supported else [False] * len(self.metrics)
            passes = [truth[i, j] >= m["threshold"] if m["operator"] == ">=" else truth[i, j] <= m["threshold"]
                      for j, m in enumerate(self.metrics)]
            discarded = any(decisions)
            false_rejects += int(discarded and all(passes))
            threshold_misses += sum(d and bool(p) for d, p in zip(decisions, passes))
            replay.append({"geometry_key": pair["geometry_key"], "source": "PREDICTED", "supported": supported,
                           "would_discard": discarded, "high_passes_configured_thresholds": bool(all(passes)),
                           "predicted_high": predictions[i].tolist(), "measured_high": truth[i].tolist(),
                           "low_seconds": pair["low"]["seconds"], "high_seconds": pair["high"]["seconds"]})
        baseline = sum(p["high"]["seconds"] for p in test)
        screen = inference_seconds + sum(p["low"]["seconds"] + (0 if row["would_discard"] else p["high"]["seconds"]) for p, row in zip(test, replay))
        saving = baseline - screen
        compute_seconds = time.perf_counter() - compute_started
        validation_cost = compute_seconds + measured_seconds
        self.report.update({"replay": replay, "false_rejected_high_feasible": false_rejects,
                            "threshold_false_rejections": threshold_misses,
                            "holdout_high_feasible": sum(row["high_passes_configured_thresholds"] for row in replay),
                            "cost": {"source": "measured_workflow_wall_clock", "baseline_high_seconds": baseline,
                                     "comparison_mode": "serial sum of completed workflow durations; not concurrent campaign makespan",
                                     "validation_compute_seconds": compute_seconds, "replay_inference_seconds": inference_seconds,
                                     "replay_screened_seconds": screen, "replay_saved_seconds": saving,
                                     "replay_saving_fraction": saving / baseline,
                                     "observed_validation_total_seconds": validation_cost,
                                     "amortization_batches_at_replay_rate": math.ceil(validation_cost / saving) if saving > 0 else None,
                                     "realized_saving_seconds": None,
                                     "note": "Validation actually measured both fidelities. Replay savings are counterfactual; amortization includes all validation runs."}})
        if false_rejects or threshold_misses:
            self.report["reasons"].append("heldout_threshold_false_rejection")
        if self.report["holdout_high_feasible"] < policy.minimum_test_feasible:
            self.report["reasons"].append("insufficient_heldout_high_feasible_designs")
        if saving / baseline < policy.minimum_saving_fraction:
            self.report["reasons"].append("no_positive_measured_cost_replay_evidence")
        if not any(m.get("check_ranking", True) for m in self.metrics):
            self.report["reasons"].append("no_ranking_metric_checked")
        self.report["usable_for_screening"] = not self.report["reasons"]
        return self

    def screen(self, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for row in rows:
            item = {"run_id": row.get("run_id"), "source": "PREDICTED", "discard": False, "reason": "validation_gate_closed"}
            if self.report.get("usable_for_screening"):
                try:
                    parsed = _record(row, self.metrics, {self.manifest["low_recipe_hash"]})
                    if parsed["measurement"] != self.prototype["measurement"] or parsed["geometry_contract"] != self.prototype["geometry_contract"]:
                        raise ValueError("different_validation_domain")
                    x = np.array([parsed["params"][n] for n in parsed["names"]])
                    if not _supported(x, self.train_x):
                        raise ValueError("outside_validated_local_support")
                    means = np.array([model.predict_from_low(x, [parsed["values"][j]])[0][0] for j, model in enumerate(self.models)])
                    item.update({"discard": any(_reject(means, self.bounds, self.metrics)),
                                 "reason": "validated_coarse_replay_rule", "predicted_high": means.tolist(),
                                 "absolute_error_bound": self.bounds.tolist()})
                except (ValueError, KeyError, TypeError, OverflowError) as exc:
                    item["reason"] = str(exc)
            result.append(item)
        return result


def audit_history(paths: Sequence[str | Path]) -> dict[str, Any]:
    """Read historical JSON/JSONL ledgers without assigning missing metadata."""
    rows, errors = [], []
    for source in paths:
        path = Path(source)
        try:
            if path.suffix.lower() == ".jsonl":
                for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
                    if line.strip():
                        try:
                            rows.append(json.loads(line))
                        except json.JSONDecodeError:
                            errors.append({"path": str(path), "line": number, "reason": "invalid_json"})
            else:
                value = json.loads(path.read_text(encoding="utf-8-sig"))
                rows.extend(value if isinstance(value, list) else value.get("records", [value]) if isinstance(value, dict) else [value])
        except (OSError, ValueError, TypeError) as exc:
            errors.append({"path": str(path), "reason": str(exc)})
    events = len(rows)
    # real_runs.jsonl appends phase/status snapshots of the same run. They are
    # events, not independent measurements or new paired designs.
    latest = {}
    for index, row in enumerate(rows):
        key = str(row.get("run_id")) if isinstance(row, dict) and row.get("run_id") else f"anonymous:{index}"
        latest[key] = row
    rows = list(latest.values())
    counts = Counter()
    recipe_counts = Counter()
    for row in rows:
        if not isinstance(row, dict):
            counts["invalid_record"] += 1
            continue
        contract = stored_contract(row)
        counts["completed"] += str(row.get("status", "")).upper() == "COMPLETED"
        counts["explicit_measured_source"] += str(row.get("source", "")).lower() == "measured"
        counts["missing_contract"] += not bool(contract)
        counts["missing_observed_frequency_axis"] += not bool((row.get("measurement") or {}).get("frequency_ghz"))
        counts["unknown_reference_plane"] += not _known((row.get("measurement") or {}).get("reference_plane"))
        counts["missing_workflow_cost_provenance"] += row.get("cost_scope") != "workflow_wall_clock" or not _known(row.get("timing_source"))
        if contract.get("recipe_hash"):
            recipe_counts[contract["recipe_hash"]] += 1
    return {"source": "PREDICTED", "kind": "coarse_history_audit", "usable_for_screening": False,
            "events": events, "records": len(rows), "counts": dict(counts), "recipe_counts": dict(recipe_counts),
            "has_two_declared_recipe_identities": len(recipe_counts) >= 2,
            "input_errors": errors, "reason": "audit_only_explicit_paired_validation_manifest_required"}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="validate a paired evidence manifest; optionally screen completed coarse candidates",
                             description="Manifest v1: low_recipe_hash, high_recipe_hash, metrics, records. Each completed measured record requires its original learning_contract, params, metrics, observed measurement axis/reference/units/definitions, total_seconds, cost_scope and timing_source.")
    validate.add_argument("manifest", type=Path)
    validate.add_argument("--candidates", type=Path)
    validate.add_argument("--output", type=Path)
    audit = sub.add_parser("audit", help="read JSON/JSONL history and report missing provenance")
    audit.add_argument("paths", nargs="+", type=Path)
    audit.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        inputs = args.paths if args.command == "audit" else [args.manifest] + ([args.candidates] if args.candidates else [])
        if args.output and args.output.resolve() in {p.resolve() for p in inputs}:
            raise ValueError("report output must not overwrite evidence or candidate input")
        if args.command == "audit":
            report = audit_history(args.paths)
        else:
            manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
            if not isinstance(manifest, dict):
                raise ValueError("manifest must be a JSON object")
            gate = CoarseScreeningGate(GatePolicy(**manifest.get("policy", {}))).fit(manifest)
            report = gate.report
            if args.candidates:
                rows = json.loads(args.candidates.read_text(encoding="utf-8-sig"))
                if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                    raise ValueError("candidates must be a JSON list of completed coarse measurements")
                report["candidate_decisions"] = gate.screen(rows)
        encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded + "\n", encoding="utf-8")
        else:
            print(encoded)
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
