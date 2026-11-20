"""Candidate-level risk: the failure rate around a proposal, not just the campaign average."""
from __future__ import annotations

import unittest

from antopt.risk import fit_risk_model, measured_outcomes

NAMES = ("ring_outer", "ring_height")


def run(index: int, ring: float, height: float, status: str = "COMPLETED") -> dict:
    return {"run_id": "r%d" % index, "status": status,
            "params": {"ring_outer": ring, "ring_height": height},
            "total_seconds": 1200.0}


class OutcomeTests(unittest.TestCase):
    def test_failed_and_completed_runs_both_count(self):
        rows = [run(0, 2.0, 0.6), run(1, 2.2, 0.6, status="FAILED"),
                run(2, 2.4, 0.6), {"run_id": "ignored", "status": "RUNNING", "params": {}}]
        x, failed, ids = measured_outcomes(rows, NAMES)
        self.assertEqual(len(x), 3)
        self.assertEqual(list(failed), [0.0, 1.0, 0.0])
        self.assertEqual(ids, ["r0", "r1", "r2"])


class NeighbourRiskTests(unittest.TestCase):
    def _rows(self):
        # the low-ring corner always fails, the high-ring corner never does
        rows = []
        for index, ring in enumerate((2.0, 2.02, 2.04, 2.5, 2.52, 2.54)):
            status = "FAILED" if ring < 2.1 else "COMPLETED"
            rows.append(run(index, ring, 0.6, status=status))
        return rows

    def test_a_proposal_near_the_failing_corner_gets_a_high_risk(self):
        model = fit_risk_model(self._rows(), NAMES)
        estimate = model.predict({"ring_outer": 2.01, "ring_height": 0.6})
        self.assertEqual(estimate.method, "neighbours")
        self.assertGreaterEqual(estimate.failure_rate, 0.5)

    def test_a_proposal_near_the_healthy_corner_gets_a_low_risk(self):
        model = fit_risk_model(self._rows(), NAMES)
        estimate = model.predict({"ring_outer": 2.53, "ring_height": 0.6})
        self.assertEqual(estimate.method, "neighbours")
        self.assertEqual(estimate.failure_rate, 0.0)

    def test_the_global_rate_is_reported_alongside(self):
        model = fit_risk_model(self._rows(), NAMES)
        estimate = model.predict({"ring_outer": 2.6, "ring_height": 0.7})
        self.assertAlmostEqual(estimate.global_rate, 0.5, places=3)

    def test_no_nearby_measurement_falls_back_to_the_global_rate(self):
        model = fit_risk_model(self._rows(), NAMES)
        estimate = model.predict({"ring_outer": 5.0, "ring_height": 1.2})   # far outside
        self.assertEqual(estimate.method, "global")
        self.assertEqual(estimate.neighbours, 0)
        self.assertAlmostEqual(estimate.failure_rate, estimate.global_rate, places=3)

    def test_no_measurements_at_all_is_not_a_crash(self):
        model = fit_risk_model([], NAMES)
        estimate = model.predict({"ring_outer": 2.0, "ring_height": 0.6})
        self.assertEqual(estimate.method, "global")
        self.assertEqual(estimate.failure_rate, 0.0)


if __name__ == "__main__":
    unittest.main()
