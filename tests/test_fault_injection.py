"""Fault injection: each scenario pins one invariant the system claims to hold."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from aster import faults


class ScenarioTests(unittest.TestCase):
    def test_every_scenario_holds(self):
        report = faults.run_all()
        self.assertEqual(report["total"], len(faults.SCENARIOS))
        failed = [item for item in report["scenarios"] if not item.get("held")]
        self.assertEqual(failed, [], "these invariants did not hold: %s"
                         % [item.get("name") for item in failed])
        self.assertTrue(report["all_held"])

    def test_a_short_export_never_becomes_a_bandwidth(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = faults.scenario_short_export(Path(tmp))
        self.assertTrue(result["held"])
        self.assertFalse(result["value_ghz"])

    def test_a_repeated_frequency_breaks_the_band(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = faults.scenario_duplicate_frequency(Path(tmp))
        self.assertTrue(result["held"])
        self.assertLessEqual(result["value_ghz"], 1.0)

    def test_mhz_files_are_read_as_ghz(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = faults.scenario_mhz_units(Path(tmp))
        self.assertTrue(result["held"])
        self.assertAlmostEqual(result["lowest_ghz"], 24.0)

    def test_a_locked_database_does_not_lose_a_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = faults.scenario_sqlite_lock(Path(tmp), hold_seconds=0.5)
        self.assertTrue(result["held"])
        self.assertEqual(result["recorded"], "COMPLETED")

    def test_a_licence_refusal_is_environment_not_physics(self):
        result = faults.scenario_licence_refusal()
        self.assertTrue(result["held"])
        self.assertIn("许可", result["title"])

    def test_a_dead_worker_task_is_retried_and_counted_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = faults.scenario_worker_death(Path(tmp))
        self.assertTrue(result["held"])
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["state"].get("DONE"), 1)


class FailureCatalogueTests(unittest.TestCase):
    def test_licence_errors_are_classified(self):
        from aster.failures import classify

        failure = classify("LICENSE_ERROR", "Failed to check out license hfss_solve")
        self.assertEqual(failure.title, "许可证不可用")
        self.assertTrue(failure.retryable)
        self.assertIn("许可证", failure.action)

    def test_a_licence_error_is_not_confused_with_geometry(self):
        from aster.failures import classify

        failure = classify("LICENSE_ERROR", "license checkout failed")
        self.assertNotIn("几何", failure.title)


if __name__ == "__main__":
    unittest.main()
