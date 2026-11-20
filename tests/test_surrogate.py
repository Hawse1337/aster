"""Module B tests: dataset fidelity handling, surrogate accuracy, acquisition."""
from __future__ import annotations

import os
import unittest

import numpy as np

from antopt.surrogate.dataset import Dataset, Sample
from antopt.surrogate.models import (
    GaussianProcessModel,
    RBFModel,
    cross_validate,
    model_is_usable,
)
from antopt.surrogate.selection import expected_improvement, select_candidates

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")


def _sample(index: int, gain: float, fidelity: str = "high", source: str = "measured"):
    return Sample(
        candidate_id=f"c{index}",
        params={"x1": (index % 10) / 10.0, "x2": (index // 10) / 5.0},
        targets={"realized_gain_h": gain},
        fidelity=fidelity,
        source=source,
    )


class DatasetTests(unittest.TestCase):
    def test_fidelity_separation(self):
        data = Dataset()
        data.extend([_sample(0, 5.0), _sample(1, 6.0, fidelity="low")])
        high = data.samples()
        low = data.samples(fidelity="low")
        self.assertEqual(len(high), 1)
        self.assertEqual(len(low), 1)

    def test_low_fidelity_cannot_downgrade_high(self):
        data = Dataset()
        data.add(_sample(0, 5.0))
        data.add(_sample(0, 4.0, fidelity="low"))
        self.assertEqual(data.samples()[0].targets["realized_gain_h"], 5.0)

    def test_matrix_and_summary(self):
        data = Dataset()
        for index in range(6):
            data.add(_sample(index, float(index)))
        x, y = data.matrix("realized_gain_h", ["x1", "x2"])
        self.assertEqual(x.shape, (6, 2))
        self.assertEqual(y.shape, (6,))
        summary = data.summary()
        self.assertEqual(summary["samples"], 6)
        self.assertEqual(summary["fidelity"]["high"], 6)

    def test_anomaly_detection(self):
        data = Dataset()
        for index in range(8):
            data.add(_sample(index, float(index % 3)))
        data.add(_sample(99, 80.0))
        flagged = data.anomalies("realized_gain_h", z=3.0)
        self.assertIn("c99", flagged)

    def test_predicted_samples_are_not_measured(self):
        data = Dataset()
        data.add(_sample(0, 5.0, source="predicted"))
        self.assertFalse(data.samples()[0].is_measured)


class SurrogateTests(unittest.TestCase):
    def _data(self, n=24):
        rng = np.random.RandomState(0)
        x = rng.rand(n, 2)
        y = np.sin(3 * x[:, 0]) + 0.5 * x[:, 1]
        return x, y

    def test_gpr_fits_smooth_function(self):
        x, y = self._data()
        model = GaussianProcessModel().fit(x, y)
        mean, std = model.predict(x)
        self.assertLess(float(np.sqrt(np.mean((mean - y) ** 2))), 0.25)
        self.assertTrue(np.all(std > 0))

    def test_rbf_fits_smooth_function(self):
        x, y = self._data()
        model = RBFModel().fit(x, y)
        mean, std = model.predict(x)
        self.assertLess(float(np.mean(np.abs(mean - y))), 0.2)
        self.assertEqual(mean.shape, std.shape)

    def test_cross_validation_report(self):
        x, y = self._data(30)
        report = cross_validate(GaussianProcessModel(), x, y, folds=4)
        self.assertEqual(report.samples, 30)
        self.assertGreater(report.rmse, 0.0)
        self.assertTrue(0.0 <= report.coverage <= 1.0)
        self.assertTrue(model_is_usable(report, float(y.max() - y.min()), 0.5))

    def test_unfitted_model_refuses_prediction(self):
        with self.assertRaises(RuntimeError):
            GaussianProcessModel().predict(np.zeros((1, 2)))


class AcquisitionTests(unittest.TestCase):
    def test_ei_prefers_high_mean_with_low_uncertainty(self):
        mean = np.array([1.0, 2.0, 1.5])
        std = np.array([0.1, 0.1, 0.05])
        ei = expected_improvement(mean, std, best=1.0)
        self.assertEqual(int(np.argmax(ei)), 1)

    def test_ucb_explores_uncertain_point(self):
        mean = np.array([1.0, 0.5])
        std = np.array([0.1, 3.0])
        picks = select_candidates(mean, std, strategy="ucb", count=1, beta=2.0)
        self.assertEqual(picks[0].index, 1)

    def test_ei_selects_multiple(self):
        mean = np.array([0.1, 0.9, 0.5])
        std = np.array([0.1, 0.1, 0.1])
        picks = select_candidates(mean, std, best=0.0, strategy="ei", count=2)
        self.assertEqual([p.index for p in picks], [1, 2])

    def test_unknown_strategy(self):
        with self.assertRaises(ValueError):
            select_candidates(np.zeros(2), np.ones(2), strategy="magic")


if __name__ == "__main__":
    unittest.main()
