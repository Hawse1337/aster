"""DoE analysis: counts, constraint pass rates, spread, best points, coarse parameter effects."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from antopt.doe_report import load_doe_rows, summarise_doe, write_report
from antopt.runtime.store import JobStore


def _metrics(bandwidth=2.0, gain=6.0, directivity=6.7, beam=90.0, ripple=1.8):
    return {"s11_bandwidth": bandwidth, "peak_gain": gain, "directivity_horiz_db": directivity,
            "beam_theta": beam, "ripple": ripple}


def _campaign(tmp: str, rows: list[dict]) -> Path:
    store = JobStore(Path(tmp) / "queue.sqlite")
    for row in rows:
        store.save_job(row)
    return Path(tmp) / "queue.sqlite"


class LoadTests(unittest.TestCase):
    def test_rows_are_read_with_metrics_and_params(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = _campaign(tmp, [
                {"run_id": "a", "status": "COMPLETED", "params": {"ring_outer": 3.0},
                 "metrics": _metrics()},
                {"run_id": "b", "status": "FAILED", "params": {"ring_outer": 2.5},
                 "metrics": {}, "error_code": "NO_AEDT"},
            ])
            rows = load_doe_rows(queue)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["run_id"], "a")
        self.assertAlmostEqual(rows[0]["directivity_horiz_db"], 6.7)
        self.assertIsNone(rows[1]["directivity_horiz_db"])
        self.assertEqual(rows[1]["error_code"], "NO_AEDT")


class RetryDedupeTests(unittest.TestCase):
    def test_a_retried_design_is_counted_once_with_the_latest_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "queue.sqlite")
            params = {"ring_outer": 3.0}
            store.save_job({"run_id": "first", "status": "FAILED", "params": params,
                            "metrics": {}, "error_code": "NO_AEDT"})
            store.save_job({"run_id": "second", "status": "COMPLETED", "params": params,
                            "metrics": _metrics()})
            rows = load_doe_rows(Path(tmp) / "queue.sqlite")
        self.assertEqual(len(rows), 1, "the same design was enqueued twice")
        self.assertEqual(rows[0]["run_id"], "second")
        self.assertEqual(rows[0]["status"], "COMPLETED")

    def test_distinct_designs_are_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "queue.sqlite")
            store.save_job({"run_id": "a", "status": "COMPLETED",
                            "params": {"ring_outer": 3.0}, "metrics": _metrics()})
            store.save_job({"run_id": "b", "status": "COMPLETED",
                            "params": {"ring_outer": 2.0}, "metrics": _metrics()})
            rows = load_doe_rows(Path(tmp) / "queue.sqlite")
        self.assertEqual(len(rows), 2)


class SummaryTests(unittest.TestCase):
    def rows(self) -> list[dict]:
        flat = []
        for row in self._rows():
            entry = {"run_id": row["run_id"], "status": row["status"],
                     "params": row["params"], "error_code": row.get("error_code")}
            for key in ("s11_bandwidth", "peak_gain", "directivity_horiz_db",
                        "beam_theta", "ripple"):
                entry[key] = row["metrics"].get(key)
            flat.append(entry)
        return flat

    def _rows(self) -> list[dict]:
        return [
            {"run_id": "hit", "status": "COMPLETED", "params": {"ring_outer": 3.0},
             "metrics": _metrics(bandwidth=3.1, gain=6.2, directivity=7.2, beam=90.0, ripple=1.5)},
            {"run_id": "near", "status": "COMPLETED", "params": {"ring_outer": 2.0},
             "metrics": _metrics(bandwidth=2.0, gain=6.1, directivity=6.5, beam=88.0, ripple=2.0)},
            {"run_id": "far", "status": "COMPLETED", "params": {"ring_outer": 1.0},
             "metrics": _metrics(bandwidth=1.0, gain=5.5, directivity=5.0, beam=120.0, ripple=4.0)},
            {"run_id": "mid", "status": "COMPLETED", "params": {"ring_outer": 2.6},
             "metrics": _metrics(bandwidth=2.5, gain=5.9, directivity=6.1, beam=92.0, ripple=2.2)},
            {"run_id": "dead", "status": "FAILED", "params": {"ring_outer": 2.4},
             "metrics": {}, "error_code": "NO_AEDT"},
        ]

    def test_counts_and_feasibility(self):
        report = summarise_doe(self.rows())
        self.assertEqual(report["planned"], 5)
        self.assertEqual(report["completed"], 4)
        self.assertEqual(report["pending"], 0)
        self.assertEqual(report["failed"], 1)
        self.assertEqual(report["failure_codes"], ["NO_AEDT"])
        self.assertEqual(report["feasible_run_ids"], ["hit"])

    def test_spread_and_best(self):
        report = summarise_doe(self.rows())
        band = report["spread"]["s11_bandwidth"]
        self.assertEqual((band["min"], band["max"], band["n"]), (1.0, 3.1, 4))
        self.assertEqual(report["best"]["directivity_horiz_db"]["run_id"], "hit")
        self.assertAlmostEqual(report["best"]["directivity_horiz_db"]["value"], 7.2)

    def test_pass_rates_use_the_declared_constraints(self):
        report = summarise_doe(self.rows())
        self.assertAlmostEqual(report["constraint_pass_rate"]["directivity_horiz_db"], 1 / 4)
        self.assertAlmostEqual(report["constraint_pass_rate"]["beam_theta"], 3 / 4)

    def test_parameter_effect_ranks_the_large_gap_first(self):
        report = summarise_doe(self.rows())
        effects = report["parameter_effect"]["directivity_horiz_db"]
        self.assertTrue(effects)
        self.assertEqual(effects[0]["parameter"], "ring_outer")
        self.assertGreater(effects[0]["gap"], 0)

    def test_default_comparison_is_honest_about_direction(self):
        report = summarise_doe(self.rows(),
                               default_metrics=_metrics(bandwidth=3.5, directivity=6.7))
        self.assertTrue(report["best_beats_default"]["directivity_horiz_db"])
        self.assertFalse(report["best_beats_default"]["s11_bandwidth"])

    def test_a_batch_in_flight_is_not_counted_as_failed(self):
        rows = self.rows() + [{"run_id": "waiting", "status": "QUEUED",
                                "params": {"ring_outer": 9.9}, "error_code": None}]
        report = summarise_doe(rows)
        self.assertEqual(report["planned"], 6)
        self.assertEqual(report["completed"], 4)
        self.assertEqual(report["failed"], 1)
        self.assertEqual(report["pending"], 1)

    def test_an_empty_batch_says_so(self):
        report = summarise_doe([])
        self.assertEqual(report["completed"], 0)
        self.assertIn("nothing to conclude", report["verdict"])

    def test_the_verdict_counts_what_matters(self):
        report = summarise_doe(self.rows())
        self.assertIn("4 of 5 completed", report["verdict"])
        self.assertIn("1 meet every constraint", report["verdict"])

    def test_the_lowest_sidelobe_is_reported(self):
        rows = self.rows()
        rows[0]["sll"] = -12.0
        rows[1]["sll"] = -18.5
        rows[2]["sll"] = -6.0
        report = summarise_doe(rows)
        self.assertEqual(report["lowest_sll"]["run_id"], "near")
        self.assertAlmostEqual(report["lowest_sll"]["value"], -18.5)
        self.assertAlmostEqual(report["spread"]["sll"]["min"], -18.5)

    def test_a_report_is_written_as_json(self):
        report = summarise_doe(self.rows())
        with tempfile.TemporaryDirectory() as tmp:
            path = write_report(report, Path(tmp) / "doe_results.json")
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["completed"], 4)


if __name__ == "__main__":
    unittest.main()
