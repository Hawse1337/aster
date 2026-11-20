"""Learning curves and multi-fidelity surrogate tests (offline)."""
from __future__ import annotations

import os
import unittest

import numpy as np

from aster.benchmark.curves import best_so_far, compare_curves, feasible_only_curve
from aster.surrogate.multifidelity import (
    FidelityBudget,
    MultiFidelityModel,
    MultiFidelitySample,
    high_fidelity_error,
)

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")


class FakeEntry:
    def __init__(self, feasible, gain):
        self.feasible = feasible
        self.metrics = {"realized_gain_h": gain}


class CurveTests(unittest.TestCase):
    def test_best_so_far_ignores_missing(self):
        curve = best_so_far([None, 3.0, None, 5.0, 4.0])
        self.assertTrue(np.isnan(curve[0]))  # nothing measured yet -> nan placeholder
        self.assertEqual(curve[3], 5.0)
        self.assertEqual(curve[4], 5.0)

    def test_minimize_direction(self):
        curve = best_so_far([-8.0, -12.0, -9.0], maximize=False)
        self.assertEqual(curve, [-8.0, -12.0, -12.0])

    def test_feasible_only_curve(self):
        entries = [FakeEntry(False, 9.0), FakeEntry(True, 4.0), FakeEntry(True, 3.5)]
        curve = feasible_only_curve(entries, "realized_gain_h")
        self.assertEqual(curve[1], 4.0)
        self.assertEqual(curve[2], 4.0)

    def test_compare_curves_samples_at_common_counts(self):
        comparison = compare_curves({"a": [1, 2, 3, 4], "b": [2, 2, 2, 2]})
        self.assertEqual(comparison.counts[0], 1)
        self.assertIn(4, comparison.counts)
        self.assertEqual(comparison.best["a"], 4.0)
        self.assertEqual(comparison.best["b"], 2.0)
        self.assertGreater(comparison.auc["a"], comparison.auc["b"])

    def test_empty_input_rejected(self):
        with self.assertRaises(ValueError):
            compare_curves({})


class MultiFidelityTests(unittest.TestCase):
    def _truth(self, x):
        return np.sin(2.0 * np.asarray(x)) + 0.3 * np.asarray(x)

    def _low(self, x):
        return 0.8 * self._truth(x) + 0.1  # biased, strongly correlated

    def _samples(self, rng, n_low=20, n_high=5):
        samples = []
        for index in range(n_low):
            x = float(rng.uniform(0, 1.5))
            samples.append(MultiFidelitySample([x], float(self._low(x)), "low",
                                               candidate_id=f"l{index}"))
        for index in range(n_high):
            x = float(rng.uniform(0, 1.5))
            samples.append(MultiFidelitySample([x], float(self._truth(x)), "high",
                                               candidate_id=f"h{index}"))
        return samples

    def test_low_fidelity_data_improves_high_fidelity_prediction(self):
        from aster.surrogate.models import GaussianProcessModel

        rng = np.random.RandomState(0)
        samples = self._samples(rng)
        high_only = [s for s in samples if s.fidelity == "high"]
        baseline = GaussianProcessModel().fit(
            np.array([s.x for s in high_only]), np.array([s.y for s in high_only])
        )
        base_mean, _ = baseline.predict(np.array([s.x for s in high_only]))
        base_error = float(np.sqrt(np.mean((base_mean - np.array([s.y for s in high_only])) ** 2)))

        mf = MultiFidelityModel(rho=1.0).fit(samples)
        mf_error = high_fidelity_error(mf, samples)
        self.assertGreaterEqual(mf.n_low, 1)
        self.assertLessEqual(mf_error, base_error + 1e-9)

    def test_prediction_shape_and_uncertainty(self):
        rng = np.random.RandomState(1)
        model = MultiFidelityModel().fit(self._samples(rng))
        mean, std = model.predict(np.array([[0.2], [0.7]]))
        self.assertEqual(mean.shape, (2,))
        self.assertTrue(np.all(std >= 0))

    def test_requires_high_fidelity_sample(self):
        with self.assertRaises(ValueError):
            MultiFidelityModel().fit([MultiFidelitySample([0.1], 1.0, "low")])

    def test_budget_tracking(self):
        budget = FidelityBudget(low_left=2, high_left=1)
        self.assertTrue(budget.take("high"))
        self.assertFalse(budget.take("high"))
        self.assertTrue(budget.take("low"))
        self.assertTrue(budget.take("low"))
        self.assertFalse(budget.take("low"))
        self.assertEqual(budget.used_low, 2)
        self.assertEqual(budget.used_high, 1)


if __name__ == "__main__":
    unittest.main()

