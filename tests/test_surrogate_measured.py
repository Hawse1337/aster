"""Surrogate-on-measured-data tests (campaign records -> recommendation)."""
from __future__ import annotations

import os
import unittest

from antopt.cases.lens28 import build_case
from antopt.surrogate.campaign_data import (
    dataset_from_records,
    fit_measured_model,
    grid_prediction,
    recommend_next,
)

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")


def _record(candidate_id, step_L, step_ro, peak, s11=-12.0, status="COMPLETED"):
    return {
        "candidate_id": candidate_id,
        "params": {"step_L": step_L, "step_ro": step_ro, "wedge": 1, "cone_dz": 0.0},
        "metrics": {"peak_gain": peak, "s11": s11},
        "status": status,
        "feasible": True,
        "run_id": f"run-{candidate_id}",
    }


class DatasetFromRecordsTests(unittest.TestCase):
    def test_only_completed_records_with_target(self):
        dataset = dataset_from_records([
            _record("a", 2.0, 0.8, 6.75),
            _record("b", 3.0, 0.8, 6.57),
            _record("c", 4.0, 0.8, 7.10, status="FAILED"),
            {"candidate_id": "d", "params": {}, "metrics": {"s11": -9.0},
             "status": "COMPLETED", "run_id": None},
        ])
        self.assertEqual(len(dataset), 2)
        self.assertEqual({sample.candidate_id for sample in dataset.samples()},
                         {"a", "b"})

    def test_samples_are_marked_measured(self):
        dataset = dataset_from_records([_record("a", 2.0, 0.8, 6.75)])
        sample = dataset.samples()[0]
        self.assertTrue(sample.is_measured)
        self.assertEqual(sample.run_id, "run-a")


class SurrogateRecommendationTests(unittest.TestCase):
    def setUp(self):
        self.case = build_case()
        self.records = [
            _record("a", 2.0, 0.8, 6.756),
            _record("b", 2.4, 0.8, 6.601),
            _record("c", 2.8, 0.8, 6.574),
            _record("d", 3.0, 0.8, 6.568),
            _record("e", 3.0, 0.6, 6.866),
            _record("f", 3.5, 0.6, 6.464),
            _record("g", 3.9, 0.6, 6.594),
            _record("h", 3.9, 0.5, 6.604),
        ]

    def test_fit_reports_quality(self):
        dataset = dataset_from_records(self.records)
        fitted = fit_measured_model(dataset, self.case, target="peak_gain")
        self.assertEqual(fitted["samples"], 8)
        self.assertGreater(fitted["report"].rmse, 0.0)
        self.assertIn("usable", fitted)

    def test_recommendation_stays_inside_bounds(self):
        dataset = dataset_from_records(self.records)
        picks = recommend_next(self.case, dataset, count=1, pool=128, seed=1)
        self.assertTrue(picks)
        params = picks[0]["params"]
        space = self.case.family.param_space
        clean = space.validate(params)
        self.assertEqual(set(clean), {"step_L", "step_ro", "wedge", "cone_dz"})
        self.assertEqual(picks[0]["source"], "predicted")

    def test_grid_prediction_reports_reachability(self):
        dataset = dataset_from_records(self.records)
        grid = grid_prediction(self.case, dataset, per_axis=6)
        self.assertGreater(grid["predicted_max"], 6.0)
        self.assertLessEqual(grid["predicted_max"], 8.5)
        self.assertGreater(grid["median"], 0.0)

    def test_insufficient_data_is_reported_not_hidden(self):
        dataset = dataset_from_records(self.records[:2])
        fitted = fit_measured_model(dataset, self.case)
        self.assertFalse(fitted["usable"])


if __name__ == "__main__":
    unittest.main()
