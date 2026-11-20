"""Evidence identity and discrete probes must agree with the run actually charged."""
from dataclasses import replace
from types import SimpleNamespace
import tempfile
import unittest

from antopt.core import ParamSpace, ParamSpec
from antopt.cost import fit_cost_model
from antopt.physics.local_opt import propose_local_step
from antopt.physics.sensitivity import BudgetedEvaluator, SensitivityMatrix, compute_sensitivity, screen_parameters
from antopt.risk import fit_risk_model
from antopt.robustness import score_robustness
from antopt.synthetic import build_case


def integer_case():
    case = build_case()
    space = ParamSpace([ParamSpec("x1", "", 1, 5, 3, kind="int"),
                        ParamSpec("x2", "", 0, 1, .5)])
    return replace(case, family=replace(case.family, param_space=space))


class BudgetIdentityTests(unittest.TestCase):
    def test_integer_identity_is_the_actual_evaluated_design(self):
        case = integer_case()
        calls = []
        def measure(params):
            calls.append(dict(params))
            return SimpleNamespace(status="COMPLETED", metrics={"peak_gain": 3 * params["x1"]})
        gate = BudgetedEvaluator(case, measure, budget=1)
        first = gate({"x1": 2.49, "x2": .5})
        second = gate({"x1": 2.1, "x2": .5})
        self.assertIs(first, second)
        self.assertEqual(calls, [{"x1": 2, "x2": .5}])
        self.assertEqual(gate.calls, 1)

    def test_integer_derivative_uses_distinct_rounded_measurements(self):
        case = integer_case()
        calls = []
        def measure(params):
            calls.append(dict(params))
            return SimpleNamespace(status="COMPLETED", metrics={"peak_gain": 3 * params["x1"]})
        gate = BudgetedEvaluator(case, measure, budget=3)
        matrix = compute_sensitivity(case, gate, {"x1": 3, "x2": .5},
                                     params=["x1"], metric_kinds=["peak_gain"], step_fraction=.05)
        self.assertEqual([p["x1"] for p in calls], [3, 4, 2])
        self.assertAlmostEqual(matrix.raw["peak_gain"]["x1"], 3)
        self.assertEqual(matrix.evaluations, 3)

    def test_one_sided_screen_respects_shared_measurement_limit(self):
        case = build_case()
        calls = []
        def measure(params):
            calls.append(dict(params))
            return SimpleNamespace(status="COMPLETED", metrics={"peak_gain": 3 * params["x1"]})
        gate = BudgetedEvaluator(case, measure, budget=2)
        scores = screen_parameters(case, gate, {"x1": .5, "x2": .5}, metric_kind="peak_gain")
        self.assertEqual(gate.calls, 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual([name for name, _ in scores], ["x1"])

    def test_failed_screen_baseline_is_not_sensitivity_evidence(self):
        result = screen_parameters(build_case(), lambda p: SimpleNamespace(
            status="FAILED", metrics={"peak_gain": 9}), {"x1": .5, "x2": .5},
            metric_kind="peak_gain")
        self.assertEqual(result, [])


class EvidenceBoundaryTests(unittest.TestCase):
    def test_missing_prediction_parameters_are_not_silently_zero(self):
        for model in (fit_cost_model([], ["x"]), fit_risk_model([], ["x"])):
            with self.assertRaisesRegex(ValueError, "parameters"):
                model.predict({})

    def test_default_sidelobe_worst_is_the_least_negative_value(self):
        result = score_robustness([{"sll": -20}, {"sll": -5}], keys=("sll",),
                                  planned_count=3)
        self.assertEqual(result["worst"]["sll"], -5)
        self.assertEqual(result["directions"]["sll"], "minimize")
        self.assertAlmostEqual(result["yield"], 2 / 3)
        self.assertFalse(result["worst_verified"])


class TrustRegionUnitsTests(unittest.TestCase):
    def test_fd_step_size_does_not_change_the_physical_trust_region(self):
        case = build_case()
        space = ParamSpace([ParamSpec("x1", "mm", 10, 30, 20),
                            ParamSpec("x2", "mm", .01, .03, .02)])
        case = replace(case, family=replace(case.family, param_space=space))
        def measure(params):
            value = .5 * (params["x1"] - 10) / 20 + 1.5 * (params["x2"] - .01) / .02
            return SimpleNamespace(status="COMPLETED", metrics={"peak_gain": value})
        proposals = []
        for fraction in (.05, .25):
            matrix = compute_sensitivity(case, measure, space.default(),
                                         metric_kinds=["peak_gain"], step_fraction=fraction)
            proposal = propose_local_step(matrix, target_kind="peak_gain", desired_change=1,
                                          trust_region=.2)
            self.assertTrue(proposal.accepted)
            self.assertEqual(proposal.trust_region_basis, "case_parameter_ranges")
            magnitude = ((proposal.delta["x1"] / 20) ** 2 + (proposal.delta["x2"] / .02) ** 2) ** .5
            self.assertAlmostEqual(magnitude, .2)
            proposals.append(proposal)
        for name in ("x1", "x2"):
            self.assertAlmostEqual(proposals[0].delta[name], proposals[1].delta[name])

    def test_legacy_matrix_reports_step_units_explicitly(self):
        matrix = SensitivityMatrix(base_params={"x1": .5}, raw={"peak_gain": {"x1": 2}},
                                   steps={"x1": .4}, params=["x1"])
        proposal = propose_local_step(matrix, target_kind="peak_gain", desired_change=.8, trust_region=.3)
        self.assertTrue(proposal.accepted)
        self.assertEqual(proposal.trust_region_basis, "finite_difference_steps_legacy")
        self.assertIn("legacy", proposal.reason)
        self.assertAlmostEqual(proposal.delta["x1"], .12)

    def test_protected_limit_is_respected_using_parameter_range_units(self):
        matrix = SensitivityMatrix(base_params={"x1": .5}, raw={
            "peak_gain": {"x1": 2}, "ripple": {"x1": 4}},
            steps={"x1": .01}, params=["x1"], parameter_spans={"x1": 2})
        proposal = propose_local_step(matrix, target_kind="peak_gain", desired_change=.4,
                                      protected={"ripple": .6}, trust_region=.3)
        self.assertTrue(proposal.accepted)
        self.assertAlmostEqual(proposal.delta["x1"], .15)
        self.assertLessEqual(abs(proposal.predicted_protected_change["ripple"]), .6 + 1e-12)


if __name__ == "__main__":
    unittest.main()
