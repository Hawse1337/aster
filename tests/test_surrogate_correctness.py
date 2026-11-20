"""Physical-unit invariance, honest validation, constrained acquisition and GP fits."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np

from antopt.surrogate.batch import diverse_batch
from antopt.surrogate.backends import select_batch
from antopt.surrogate.models import GaussianProcessModel, cross_validate, ModelReport, model_is_usable
from antopt.surrogate.botorch_acq import available


class ValidationAndDiversityTests(unittest.TestCase):
    def test_pool_permutation_does_not_change_physical_batch(self):
        points = np.array([[.1, .9], [.12, .91], [.8, .1], [.5, .5], [.9, .8]])
        mean = np.array([2., 1.99, 1.8, 1.7, 1.5])
        std = np.array([.2, .21, .3, .25, .4])
        first = diverse_batch(mean, std, points=points, count=3, best=1.9)
        order = np.array([3, 1, 4, 0, 2])
        shuffled = diverse_batch(mean[order], std[order], points=points[order], count=3, best=1.9)
        self.assertEqual([tuple(points[p.index]) for p in first],
                         [tuple(points[order][p.index]) for p in shuffled])

    def test_two_samples_are_not_reported_as_training_accuracy(self):
        report = cross_validate(GaussianProcessModel(), np.array([[0.], [1.]]), np.array([0., 1.]))
        self.assertEqual(report.validation, "unvalidated")
        self.assertEqual(report.validated, 0)
        self.assertIsNone(report.to_dict()["rmse"])

    def test_groups_and_forward_splits_never_leak(self):
        observations = []
        class RecordingModel:
            name = "recorder"
            def fit(self, x, y):
                self.train = set(x[:, 0].tolist())
                return self
            def predict(self, x):
                observations.append((self.train, set(x[:, 0].tolist())))
                return x[:, 0], np.ones(len(x))
        x = np.arange(8.)[:, None]
        report = cross_validate(RecordingModel(), x, x[:, 0], groups=[0, 0, 1, 1, 2, 2, 3, 3], times=np.arange(8), folds=4)
        self.assertEqual(report.validation, "forward_group")
        for train, test in observations:
            self.assertLess(max(train), min(test))

    def test_parameter_units_do_not_change_fallback_choices(self):
        x = np.linspace(2, 4, 8)[:, None]
        y = np.column_stack([(x[:, 0] - 2.5) ** 2, (x[:, 0] - 3.5) ** 2])
        a = select_batch(x, y, [(2, 4)], method="diverse_ei", quality_check=False, count=2, seed=4)
        b = select_batch(x / 1000, y, [(.002, .004)], method="diverse_ei", quality_check=False, count=2, seed=4)
        np.testing.assert_allclose(a.points, b.points * 1000, atol=1e-9)
        self.assertEqual(a.method, "diverse_ei")

    def test_explicit_hypervolume_rejects_preference_weights(self):
        x = np.linspace(0, 1, 8)[:, None]
        y = np.column_stack([x[:, 0], 1 - x[:, 0]])
        with self.assertRaisesRegex(ValueError, "preference weights"):
            select_batch(x, y, [(0, 1)], method="qlognehvi", weights=[9, 1])

    def test_skipping_validation_is_not_reported_as_validated(self):
        x = np.linspace(0, 1, 8)[:, None]
        y = np.column_stack([x[:, 0], 1 - x[:, 0]])
        result = select_batch(x, y, [(0, 1)], method="diverse_ei", quality_check=False, count=1)
        self.assertEqual(result.diagnostics["quality_gate"], "not_checked")
        self.assertEqual(result.diagnostics["quality"], [])

    def test_low_error_does_not_override_poor_interval_coverage(self):
        report = ModelReport("gpr", 12, .01, .01, .2, validation="group_holdout", validated=6)
        self.assertFalse(model_is_usable(report, 1.0))
        self.assertTrue(model_is_usable(report, 1.0, minimum_coverage=.1))
        self.assertIn("held-out", report.to_dict()["coverage_basis"])
        self.assertAlmostEqual(report.to_dict()["nominal_gaussian_coverage"], .9545)

    def test_unvalidated_auto_selection_explores_without_calling_botorch(self):
        import antopt.surrogate.backends as backends
        x = np.linspace(0, 1, 8)[:, None]
        y = np.column_stack([x[:, 0], 1 - x[:, 0]])
        with patch.object(backends, "_quality", return_value=[{"usable": False, "coverage": .1}]), \
                patch.object(backends, "_run_botorch") as run_botorch:
            result = select_batch(x, y, [(0, 1)], method="auto", count=1)
        run_botorch.assert_not_called()
        self.assertEqual(result.method, "diverse_ei")
        self.assertEqual(result.diagnostics["quality_gate"], "exploration_only")
        self.assertEqual(result.diagnostics["stage"], "exploration")

    def test_smooth_objective_cannot_hide_unpredictable_constraint(self):
        import antopt.surrogate.backends as backends
        x = np.linspace(0, 1, 20)[:, None]
        y = x.copy()
        constraint = np.where(np.arange(len(x)) % 2, 1.0, -1.0)[:, None]
        with patch.object(backends, "_run_botorch") as run_botorch:
            result = select_batch(x, y, [(0, 1)], method="auto", count=1,
                                  constraint_obs=constraint)
        run_botorch.assert_not_called()
        self.assertTrue(result.diagnostics["quality"][0]["usable"])
        report = result.diagnostics["constraint_quality"][0]
        self.assertEqual(report["role"], "constraint")
        self.assertFalse(report["usable"])
        self.assertGreater(report["validated"], 0)
        self.assertEqual(result.diagnostics["constraint_quality_status"], "checked")
        self.assertEqual(result.diagnostics["quality_gate"], "exploration_only")
        self.assertEqual(result.diagnostics["stage"], "exploration")

    def test_constant_constraint_has_finite_fixed_scale_and_held_out_evidence(self):
        from antopt.surrogate.backends import _quality
        x = np.linspace(0, 1, 8)[:, None]
        report = _quality(x, np.full((8, 1), -.2), role="constraint")[0]
        self.assertEqual(report["error_scale"], 1.0)
        self.assertTrue(report["constant_observed"])
        self.assertGreater(report["validated"], 0)
        self.assertTrue(np.isfinite(report["rmse"]))
        self.assertIn("not global feasibility", report["scope"])


@unittest.skipUnless(available(), "torch/botorch unavailable")
class FittedConstrainedBotorchTests(unittest.TestCase):
    def test_hyperparameter_fit_improves_marginal_likelihood(self):
        import torch
        from botorch.models import SingleTaskGP
        from botorch.models.transforms.outcome import Standardize
        from gpytorch.mlls import ExactMarginalLogLikelihood
        from antopt.surrogate.botorch_acq import _fit_gp
        x = torch.linspace(0, 1, 12, dtype=torch.double)[:, None]
        y = torch.sin(x * 9)
        initial = SingleTaskGP(x, y, outcome_transform=Standardize(m=1))
        initial.train()
        initial_value = float(ExactMarginalLogLikelihood(initial.likelihood, initial)(initial(x), initial.train_targets).detach())
        diagnostics = {}
        fitted = _fit_gp(x, y, maxiter=35, timeout_s=6, diagnostics=diagnostics)
        fitted.train()
        final_value = float(ExactMarginalLogLikelihood(fitted.likelihood, fitted)(fitted(x), fitted.train_targets).detach())
        self.assertGreater(final_value, initial_value + .01)
        self.assertEqual(diagnostics["fit"]["status"], "fitted")
        self.assertEqual(diagnostics["fit"]["budget_seconds"], 6)

    def test_no_feasible_baseline_minimises_expected_violation(self):
        from antopt.surrogate.botorch_acq import qlognehvi_batch
        x = np.linspace(0, 1, 8)[:, None]
        y = np.column_stack([(x[:, 0] - .9) ** 2, x[:, 0]])
        diagnostics = {}
        result = qlognehvi_batch(x, y, [(0, 1)], q=1, num_restarts=1, raw_samples=8,
                                 constraint_obs=x + .1, pending_X=np.array([[.9]]),
                                 fit_maxiter=20, fit_timeout_s=5, diagnostics=diagnostics)
        self.assertEqual(diagnostics["stage"], "feasibility_first")
        self.assertEqual(diagnostics["feasible_observations"], 0)
        self.assertEqual(diagnostics["pending_count"], 1)
        self.assertLess(float(result[0, 0]), .2)

    def test_fit_failure_is_explicit(self):
        import torch
        from antopt.surrogate.botorch_acq import _fit_gp
        diagnostics = {}
        with patch("botorch.fit.fit_gpytorch_mll", side_effect=RuntimeError("budget failed")):
            with self.assertRaisesRegex(RuntimeError, "hyperparameter fitting failed"):
                _fit_gp(torch.tensor([[0.], [1.]], dtype=torch.double),
                        torch.tensor([[0.], [1.]], dtype=torch.double), diagnostics=diagnostics)
        self.assertEqual(diagnostics["fit"]["status"], "failed")

    def test_feasible_weighted_selection_fits_constraints_and_uses_pending(self):
        x = np.linspace(0, 1, 8)[:, None]
        y = np.column_stack([x[:, 0] ** 2, (1 - x[:, 0]) ** 2])
        result = select_batch(x, y, [(0, 1)], count=1, method="auto", weights=[.8, .2],
                              constraint_obs=x - .6, pending_X=np.array([[.2]]),
                              quality_check=False, fit_maxiter=20, fit_timeout_s=5)
        self.assertEqual(result.method, "qlognparego")
        self.assertEqual(result.diagnostics["fit"]["models"], 3)
        self.assertEqual(result.diagnostics["constraint_count"], 1)
        self.assertEqual(result.diagnostics["feasible_observations"], 5)
        self.assertEqual(result.diagnostics["pending_count"], 1)
        self.assertEqual(result.diagnostics["preference"], "user_chebyshev_weights")
        self.assertEqual(result.diagnostics["quality_gate"], "not_checked")
        self.assertTrue(np.all(np.isfinite(result.points)))
        self.assertTrue(np.all((result.points >= 0) & (result.points <= 1)))
        self.assertGreater(abs(float(result.points[0, 0]) - .2), 1e-6)


class PhysicalUnitsResearchEntryTests(unittest.TestCase):
    def test_refitting_high_only_cannot_reuse_an_old_low_fidelity_response(self):
        from antopt.surrogate.multifidelity import MultiFidelityModel, MultiFidelitySample
        low = [MultiFidelitySample([x], 20 + x, "low") for x in np.linspace(0, 1, 8)]
        high = [MultiFidelitySample([x], x * x, "high") for x in np.linspace(0, 1, 5)]
        reused = MultiFidelityModel().fit(low + high).fit(high)
        fresh = MultiFidelityModel().fit(high)
        query = np.linspace(0, 1, 7)[:, None]
        for left, right in zip(reused.predict(query), fresh.predict(query)):
            np.testing.assert_allclose(left, right)

    def test_campaign_prediction_queries_stay_in_training_units(self):
        from antopt.core import ParamSpec, ParamSpace
        from antopt.surrogate import campaign_data
        from antopt.surrogate.dataset import Dataset, Sample
        space = ParamSpace([ParamSpec("x", "mm", 2, 4, 3, integer_step=0)])
        case = SimpleNamespace(family=SimpleNamespace(param_space=space))
        dataset = Dataset()
        for index, value in enumerate(np.linspace(2, 4, 8)):
            dataset.add(Sample(str(index), {"x": value}, {"peak_gain": value}))
        class UnitCheckingModel:
            def predict(self, x):
                self.assert_units(x)
                return x[:, 0], np.ones(len(x)) * .1
            @staticmethod
            def assert_units(x):
                if not np.all((x[:, 0] >= 2) & (x[:, 0] <= 4)):
                    raise AssertionError("prediction was normalised twice")
        fitted = {"model": UnitCheckingModel(), "param_names": ["x"], "samples": 8,
                  "usable": True, "report": SimpleNamespace(rmse=.1)}
        with patch.object(campaign_data, "fit_measured_model", return_value=fitted):
            campaign_data.recommend_next(case, dataset, pool=12)
            campaign_data.grid_prediction(case, dataset, per_axis=5)

    def test_ablation_pool_uses_physical_parameter_values(self):
        from antopt.core import CaseSpec, FamilySpec, ParamSpace, ParamSpec, Objective
        from antopt.optimize import Evaluation
        from antopt.benchmark import ablation
        case = CaseSpec("units", "fake", "fake", (1., 2.),
                        FamilySpec("f", "fake", ParamSpace([ParamSpec("x", "mm", 2, 4, 3, integer_step=0)])),
                        constraints=[], objectives=[Objective("score", "peak_gain", "maximize")])
        class Model:
            def fit(self, x, y):
                return self
            def predict(self, x):
                if not np.all((x[:, 0] >= 2) & (x[:, 0] <= 4)):
                    raise AssertionError("ablation query uses normalised coordinates")
                return x[:, 0], np.ones(len(x)) * .1
        executor = SimpleNamespace(evaluate=lambda candidate: Evaluation(
            candidate.candidate_id, "f", candidate.params, {"peak_gain": candidate.params["x"]}, feasible=True))
        with patch.object(ablation, "GaussianProcessModel", Model):
            result = ablation.run_surrogate_assisted(case, executor, budget=6, initial=4, batch=2, pool=8)
        self.assertEqual(result.evaluations, 6)


if __name__ == "__main__":
    unittest.main()
