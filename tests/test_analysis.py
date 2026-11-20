"""Measured-data analysis tests (campaign records -> slopes)."""
from __future__ import annotations

import os
import unittest

from aster.analysis import (
    first_order_sensitivity,
    measured_rows,
    summarize_campaign,
    table,
)

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")


def _record(candidate_id, step_L, s11, gain, status="COMPLETED", feasible=True):
    return {
        "candidate_id": candidate_id,
        "params": {"step_L": step_L, "step_ro": 0.8},
        "metrics": {"s11": s11, "peak_gain": gain},
        "status": status,
        "feasible": feasible,
        "run_id": f"run-{candidate_id}",
    }


class MeasuredRowTests(unittest.TestCase):
    def test_only_completed_measurements_are_used(self):
        rows = measured_rows([
            _record("a", 2.0, -8.0, 6.0),
            _record("b", 3.0, -12.0, 6.2),
            _record("c", 4.0, -1.0, 0.0, status="FAILED"),
            {"candidate_id": "d", "params": {}, "metrics": {}, "status": "COMPLETED"},
        ])
        self.assertEqual([row.candidate_id for row in rows], ["a", "b"])

    def test_table_contains_params_and_metrics(self):
        rows = measured_rows([_record("a", 2.0, -8.0, 6.0)])
        text = table(rows)
        self.assertIn("step_L", text)
        self.assertIn("s11", text)
        self.assertIn("-8.000", text)


class SensitivityFromMeasurementsTests(unittest.TestCase):
    def test_slope_between_two_points(self):
        rows = measured_rows([_record("a", 2.0, -8.0, 6.0),
                              _record("b", 3.0, -12.5, 6.1)])
        result = first_order_sensitivity(rows, metric="s11", parameter="step_L")
        self.assertEqual(result["pairs"], 1)
        self.assertAlmostEqual(result["slope"], -4.5)

    def test_requires_identical_other_parameters(self):
        rows = measured_rows([
            {"candidate_id": "a", "params": {"step_L": 2.0, "step_ro": 0.8},
             "metrics": {"s11": -8.0}, "status": "COMPLETED", "feasible": True},
            {"candidate_id": "b", "params": {"step_L": 3.0, "step_ro": 0.6},
             "metrics": {"s11": -12.0}, "status": "COMPLETED", "feasible": True},
        ])
        result = first_order_sensitivity(rows, metric="s11", parameter="step_L")
        self.assertIsNone(result["slope"])

    def test_least_squares_over_three_points(self):
        rows = measured_rows([_record("a", 1.0, -4.0, 6.0),
                              _record("b", 2.0, -8.0, 6.0),
                              _record("c", 3.0, -12.0, 6.0)])
        result = first_order_sensitivity(rows, metric="s11", parameter="step_L")
        self.assertEqual(result["pairs"], 3)
        self.assertAlmostEqual(result["slope"], -4.0)

    def test_campaign_summary(self):
        summary = summarize_campaign({"records": [
            _record("a", 2.0, -8.0, 6.0),
            _record("b", 3.0, -12.5, 6.1),
        ]})
        self.assertEqual(summary["measured"], 2)
        self.assertTrue(summary["table"])
        slopes = {(row["metric"], row["parameter"]): row["slope"]
                  for row in summary["sensitivities"]}
        self.assertAlmostEqual(slopes[("s11", "step_L")], -4.5)
        self.assertAlmostEqual(slopes[("peak_gain", "step_L")], 0.1)


if __name__ == "__main__":
    unittest.main()
