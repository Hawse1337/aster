"""Paired coarse screening: provenance, leakage, missed feasibility and cost."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from contextlib import redirect_stderr
import io
from pathlib import Path
import tempfile
import unittest

import numpy as np

from antopt.learning_contract import _hash
from antopt.surrogate.coarse_validation import (
    CoarseScreeningGate, GatePolicy, audit_history, main, pair_records,
)
from antopt.surrogate.multifidelity import MultiFidelityModel, MultiFidelitySample

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")


def contract(tier):
    recipe = {"frequency_ghz": [24.0, 28.0, 32.0], "extraction": {"gain": "extractor-v1"},
              "mesh_settings": {"density": tier}}
    result = {"version": 1, "case_id": "test", "family_id": "test-family",
              "structure_hash": "fixture-geometry-v1", "parameter_schema": [{"name": "x", "unit": "mm"}],
              "measurement_recipe": recipe, "recipe_hash": _hash(recipe)}
    result["identity"] = _hash({k: result[k] for k in ("case_id", "family_id", "structure_hash", "recipe_hash")})
    return result


def fixture(count=48):
    records = []
    for i, x in enumerate(np.linspace(0, 1, count)):
        for tier in ("low", "high"):
            records.append({"run_id": f"{tier}-{i}", "candidate_id": f"{tier}-alias-{i}",
                            "status": "COMPLETED", "source": "MEASURED", "params": {"x": float(x)},
                            "learning_contract": contract(tier), "metrics": {"gain": float(2 * x + (0.2 if tier == "high" else 0))},
                            "measurement": {"frequency_ghz": [24.0, 28.0, 32.0], "reference_plane": "port1 deembed 0mm",
                                            "metric_units": {"gain": "dBi"}, "metric_definitions": {"gain": "realized total gain at 28GHz"}},
                            "total_seconds": 1.0 if tier == "low" else 100.0,
                            "cost_scope": "workflow_wall_clock", "timing_source": "completed run ledger"})
    return {"version": 1, "low_recipe_hash": contract("low")["recipe_hash"],
            "high_recipe_hash": contract("high")["recipe_hash"],
            "metrics": [{"name": "gain", "unit": "dBi", "operator": ">=", "threshold": 1.2, "maximum_error": 0.3}],
            "records": records}


class PairingTests(unittest.TestCase):
    def test_pairs_physical_geometry_not_candidate_aliases(self):
        pairs, rejected = pair_records(fixture())
        self.assertEqual(len(pairs), 48)
        self.assertFalse(rejected)
        self.assertNotEqual(pairs[0]["low"]["run_id"], pairs[0]["high"]["run_id"])

    def test_repeats_do_not_become_independent_designs(self):
        data = fixture()
        repeated = deepcopy(data["records"][0])
        repeated["run_id"] = "repeated-geometry"
        repeated["candidate_id"] = "different-alias"
        data["records"].append(repeated)
        pairs, rejected = pair_records(data)
        self.assertEqual(len(pairs), 47)
        self.assertTrue(any(r["reason"] == "unpaired_or_repeated_geometry" for r in rejected))

    def test_axis_reference_and_units_must_match(self):
        for change in (lambda r: r["measurement"].update(frequency_ghz=[24, 27, 32]),
                       lambda r: r["measurement"].update(reference_plane="port1 deembed 1mm"),
                       lambda r: r["measurement"]["metric_units"].update(gain="dB")):
            data = fixture()
            change(data["records"][1])
            pairs, rejected = pair_records(data)
            self.assertEqual(len(pairs), 47)
            self.assertTrue(rejected)

    def test_missing_legacy_and_predicted_rows_cannot_be_used(self):
        data = fixture()
        data["records"][0].pop("learning_contract")
        data["records"][2]["source"] = "PREDICTED"
        pairs, rejected = pair_records(data)
        self.assertEqual(len(pairs), 46)
        self.assertTrue(any(r["reason"] == "legacy_missing_contract" for r in rejected))
        self.assertTrue(any(r["reason"] == "not_explicit_measured" for r in rejected))

    def test_no_default_parameter_fill_or_zero_cost(self):
        data = fixture()
        for row in data["records"]:
            row.pop("total_seconds")
        gate = CoarseScreeningGate().fit(data)
        self.assertFalse(gate.report["usable_for_screening"])
        self.assertEqual(gate.report["paired_designs"], 0)
        data = fixture()
        data["records"][0]["params"].clear()
        self.assertEqual(len(pair_records(data)[0]), 47)

    def test_derived_quantity_change_cannot_be_called_fidelity(self):
        data = fixture()
        for row in data["records"]:
            if row["run_id"].startswith("high"):
                c = row["learning_contract"]
                c["measurement_recipe"]["extraction"]["gain"] = "changed-definition"
                c["recipe_hash"] = _hash(c["measurement_recipe"])
                c["identity"] = _hash({k: c[k] for k in ("case_id", "family_id", "structure_hash", "recipe_hash")})
                data["high_recipe_hash"] = c["recipe_hash"]
        pairs, rejected = pair_records(data)
        self.assertEqual(pairs, [])
        self.assertTrue(all(r["reason"] == "recipe_changes_more_than_fidelity" for r in rejected))

    def test_mixed_domains_fail_closed_not_arbitrarily_choose_one(self):
        data = fixture()
        for row in data["records"][:2]:
            row["measurement"]["frequency_ghz"] = [25.0, 28.0, 32.0]
        self.assertFalse(pair_records(data)[0])

    def test_signed_zero_is_not_an_independent_geometry(self):
        data = fixture()
        other = deepcopy(data["records"][0])
        other["run_id"] = "alias-zero"
        other["params"]["x"] = -0.0
        data["records"].append(other)
        self.assertEqual(len(pair_records(data)[0]), 47)


class GateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = fixture()
        cls.gate = CoarseScreeningGate().fit(cls.data)

    def test_known_bias_passes_with_real_holdout_and_cost(self):
        report = self.gate.report
        self.assertTrue(report["usable_for_screening"], report["reasons"])
        self.assertGreater(report["cost"]["replay_saved_seconds"], 0)
        self.assertEqual(report["false_rejected_high_feasible"], 0)
        self.assertIsNone(report["cost"]["realized_saving_seconds"])
        self.assertEqual(report["source"], "PREDICTED")
        splits = [{r["geometry_key"] for r in report["splits"][name]} for name in ("train", "calibration", "test")]
        self.assertTrue(all(not splits[i] & splits[j] for i in range(3) for j in range(i)))
        for name, rows in report["splits"].items():
            self.assertTrue(all(r["low_run"].startswith("low") and r["high_run"].startswith("high") for r in rows))

    def test_row_reorder_does_not_change_splits_or_predictions(self):
        data = deepcopy(self.data)
        data["records"].reverse()
        gate = CoarseScreeningGate().fit(data)
        self.assertEqual(gate.report["splits"], self.gate.report["splits"])
        np.testing.assert_allclose([r["predicted_high"] for r in gate.report["replay"]],
                                   [r["predicted_high"] for r in self.gate.report["replay"]])

    def test_too_few_pairs_never_filters(self):
        data = fixture(10)
        gate = CoarseScreeningGate().fit(data)
        self.assertFalse(gate.report["usable_for_screening"])
        self.assertFalse(gate.screen([self.data["records"][0]])[0]["discard"])

    def test_rank_reversal_fails_even_if_error_is_accurately_corrected(self):
        data = fixture()
        for row in data["records"]:
            if row["run_id"].startswith("high"):
                row["metrics"]["gain"] = -row["metrics"]["gain"]
        report = CoarseScreeningGate().fit(data).report
        self.assertFalse(report["usable_for_screening"])
        self.assertIn("ranking_not_preserved:gain", report["reasons"])

    def test_unseen_feasible_false_rejection_closes_gate(self):
        data = deepcopy(self.data)
        candidates = [r for r in self.gate.report["replay"] if r["would_discard"]]
        target = max(candidates, key=lambda r: r["predicted_high"][0])
        matching = next(r for r in self.gate.report["splits"]["test"] if r["geometry_key"] == target["geometry_key"])
        next(r for r in data["records"] if r["run_id"] == matching["high_run"])["metrics"]["gain"] = 1.2001
        report = CoarseScreeningGate().fit(data).report
        self.assertFalse(report["usable_for_screening"])
        self.assertGreater(report["false_rejected_high_feasible"], 0)
        self.assertIn("heldout_threshold_false_rejection", report["reasons"])

    def test_small_untested_drift_cannot_reuse_an_overconfident_calibration_bound(self):
        data = deepcopy(self.data)
        target = self.gate.report["splits"]["test"][0]["high_run"]
        next(r for r in data["records"] if r["run_id"] == target)["metrics"]["gain"] += 0.02
        report = CoarseScreeningGate().fit(data).report
        self.assertFalse(report["usable_for_screening"])
        self.assertIn("heldout_error_exceeds_calibration_bound:gain", report["reasons"])

    def test_no_cost_saving_keeps_every_candidate(self):
        data = fixture()
        for row in data["records"]:
            row["total_seconds"] = 100
        gate = CoarseScreeningGate().fit(data)
        self.assertIn("no_positive_measured_cost_replay_evidence", gate.report["reasons"])
        self.assertTrue(all(not r["discard"] for r in gate.screen(data["records"])))

    def test_failed_coarse_executions_are_not_hidden_by_complete_pair_costs(self):
        data = fixture()
        failed = deepcopy(data["records"][0])
        failed.update(run_id="failed-coarse", status="TIMEOUT", total_seconds=1800.0)
        data["records"].append(failed)
        report = CoarseScreeningGate().fit(data).report
        self.assertFalse(report["usable_for_screening"])
        self.assertIn("incomplete_or_failed_workflow_cost_evidence", report["reasons"])
        self.assertIn("failed-coarse", report["cost_input_audit"]["incomplete_or_failed_run_ids"])

    def test_screen_requires_low_measurement_and_local_support(self):
        rows = [deepcopy(self.data["records"][0]), deepcopy(self.data["records"][1])]
        rows[0]["params"]["x"] = -2.0
        results = self.gate.screen(rows)
        self.assertTrue(all(not r["discard"] for r in results))
        self.assertEqual(results[0]["reason"], "outside_validated_local_support")
        self.assertEqual(results[1]["reason"], "unselected_recipe")

    def test_cli_writes_only_predicted_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            report = root / "report.json"
            manifest.write_text(json.dumps(self.data), encoding="utf-8")
            self.assertEqual(main(["validate", str(manifest), "--output", str(report)]), 0)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertTrue(payload["usable_for_screening"])
            self.assertEqual(payload["source"], "PREDICTED")

    def test_cli_cannot_overwrite_its_input_ledger(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "history.jsonl"
            content = '{"run_id":"unchanged"}\n'
            path.write_text(content, encoding="utf-8")
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(["audit", str(path), "--output", str(path)])
            self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_history_audit_does_not_infer_missing_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "real_runs.jsonl"
            content = json.dumps({"run_id": "legacy", "status": "COMPLETED", "total_seconds": 100}) + "\ninvalid\n"
            path.write_text(content, encoding="utf-8")
            report = audit_history([path])
            self.assertEqual(report["counts"]["missing_contract"], 1)
            self.assertEqual(report["counts"]["unknown_reference_plane"], 1)
            self.assertFalse(report["usable_for_screening"])
            self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_status_snapshots_are_not_independent_history_measurements(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "real_runs.jsonl"
            path.write_text('\n'.join(json.dumps({"run_id": "same", "status": state})
                                      for state in ("RUNNING", "RUNNING", "COMPLETED")), encoding="utf-8")
            report = audit_history([path])
            self.assertEqual(report["events"], 3)
            self.assertEqual(report["records"], 1)
            self.assertEqual(report["counts"]["completed"], 1)

    def test_minimize_constraint_checked_independently_of_gain(self):
        data = fixture()
        data["metrics"].append({"name": "ripple", "unit": "dB", "operator": "<=", "threshold": 1.0,
                                "maximum_error": 0.1, "check_ranking": False})
        for row in data["records"]:
            row["metrics"]["ripple"] = 2.0 - row["params"]["x"]
            row["measurement"]["metric_units"]["ripple"] = "dB"
            row["measurement"]["metric_definitions"]["ripple"] = "azimuth maximum minus minimum gain"
        report = CoarseScreeningGate().fit(data).report
        self.assertFalse(report["usable_for_screening"])
        self.assertIn("insufficient_heldout_high_feasible_designs", report["reasons"])


class ObservedCoarseTests(unittest.TestCase):
    def test_observed_low_prediction_uses_exact_measured_coarse(self):
        samples = [MultiFidelitySample([x], y, tier) for x in [0.0, 0.5, 1.0]
                   for tier, y in (("low", x), ("high", x + 0.2))]
        model = MultiFidelityModel().fit(samples)
        first, _ = model.predict_from_low([[0.2]], [100.0])
        second, _ = model.predict_from_low([[0.2]], [101.0])
        self.assertAlmostEqual(float(second[0] - first[0]), 1.0)

    def test_unpaired_model_cannot_claim_observed_coarse_correction(self):
        model = MultiFidelityModel().fit([MultiFidelitySample([0.0], 0, "low"), MultiFidelitySample([1.0], 1, "high")])
        with self.assertRaises(RuntimeError):
            model.predict_from_low([[0.2]], [0.2])


if __name__ == "__main__":
    unittest.main()
