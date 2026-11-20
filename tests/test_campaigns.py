"""Housekeeping: dry-run retention by default, and an export that keeps the evidence."""
from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from aster import campaigns


def make_campaign(root: Path, runs: int = 6) -> Path:
    """A campaign with numbered run directories, a ledger and a queue file."""
    (root / "runs").mkdir(parents=True)
    for index in range(runs):
        name = "20261113T1000%02d" % index
        artifacts = root / "runs" / name / "artifacts"
        artifacts.mkdir(parents=True)
        (artifacts / "result.s1p").write_text("# GHz S dB R 50\n24 -20 0\n", encoding="utf-8")
    (root / "projects").mkdir()
    (root / "projects" / "LensFinal.aedt").write_text("fake", encoding="utf-8")
    rows = [{"run_id": "r%d" % i, "case": "lens28_var", "status": "COMPLETED",
             "params": {"ring_outer": 2.0 + 0.1 * i, "ring_height": 0.6, "tip_offset": 1.0},
             "metrics": {"s11_bandwidth": 3.0, "peak_gain": 5.9, "ripple": 1.6},
             "total_seconds": 1200 + i, "started_utc": "2026-11-13T10:00:0%dZ" % i}
            for i in range(3)]
    (root / "real_runs.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    (root / "campaign.sqlite").write_text("", encoding="utf-8")
    return root


class SummaryTests(unittest.TestCase):
    def test_a_campaign_reports_its_runs_and_bulk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_campaign(Path(tmp))
            report = campaigns.summary(root)
            self.assertEqual(len(report.runs), 6)
            self.assertGreater(report.bytes_runs, 0)
            self.assertTrue(report.has_ledger)
            payload = report.to_dict()
            self.assertEqual(payload["runs"], 6)
            self.assertEqual(payload["newest"], "20261113T100005")

    def test_a_missing_campaign_is_empty_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = campaigns.summary(Path(tmp) / "nope")
            self.assertEqual(report.runs, [])
            self.assertEqual(report.bytes_total, 0)


class RetentionTests(unittest.TestCase):
    def test_the_plan_keeps_the_newest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_campaign(Path(tmp), runs=6)
            doomed = campaigns.retention_plan(root, keep_last=2)
            self.assertEqual([path.name for path in doomed],
                             ["20261113T100000", "20261113T100001",
                              "20261113T100002", "20261113T100003"])

    def test_a_dry_run_deletes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_campaign(Path(tmp), runs=6)
            report = campaigns.apply_retention(root, keep_last=2, apply=False)
            self.assertFalse(report["applied"])
            self.assertEqual(len(list((root / "runs").iterdir())), 6)
            self.assertEqual(len(report["planned"]), 4)
            self.assertGreater(report["bytes_freed"], 0)

    def test_applying_removes_only_the_planned_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_campaign(Path(tmp), runs=6)
            report = campaigns.apply_retention(root, keep_last=2, apply=True)
            self.assertTrue(report["applied"])
            remaining = sorted(path.name for path in (root / "runs").iterdir())
            self.assertEqual(remaining, ["20261113T100004", "20261113T100005"])
            self.assertEqual(len(report["removed"]), 4)

    def test_the_evidence_files_survive_retention(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_campaign(Path(tmp), runs=6)
            campaigns.apply_retention(root, keep_last=1, apply=True)
            # the files this fixture created must survive; PROTECTED also lists names a campaign
            # may legitimately never have written (dry_runs.jsonl only exists after a dry run)
            for name in ("campaign.sqlite", "real_runs.jsonl", "projects"):
                with self.subTest(item=name):
                    path = root / name
                    self.assertTrue(path.exists(), "%s must survive" % name)

    def test_keeping_everything_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_campaign(Path(tmp), runs=3)
            self.assertEqual(campaigns.retention_plan(root, keep_last=10), [])
            report = campaigns.apply_retention(root, keep_last=10, apply=True)
            self.assertEqual(report["removed"], [])


class ExportTests(unittest.TestCase):
    def test_the_export_writes_csv_and_json_with_the_measured_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_campaign(Path(tmp))
            out = Path(tmp) / "export"
            report = campaigns.export_results(root, out)
            self.assertEqual(report["runs"], 3)
            with (out / "results.csv").open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]["run_id"], "r2")          # newest first
            self.assertEqual(rows[0]["case"], "lens28_var")
            self.assertEqual(float(rows[0]["peak_gain"]), 5.9)
            payload = json.loads((out / "results.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"]["runs"], 6)     # run directories, not rows
            self.assertEqual(len(payload["runs"]), 3)

    def test_an_empty_campaign_exports_headers_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "empty"
            root.mkdir()
            report = campaigns.export_results(root, Path(tmp) / "out")
            self.assertEqual(report["runs"], 0)
            header = (Path(tmp) / "out" / "results.csv").read_text(encoding="utf-8").splitlines()[0]
            self.assertIn("run_id", header)
            self.assertIn("peak_gain", header)

    def test_rows_can_be_filtered_before_exporting(self):
        rows = [{"run_id": "a", "status": "COMPLETED"}, {"run_id": "b", "status": "FAILED"}]
        self.assertEqual([row["run_id"] for row in campaigns.filter_rows(rows, "completed")],
                         ["a"])
        self.assertEqual(len(campaigns.filter_rows(rows)), 2)


if __name__ == "__main__":
    unittest.main()
