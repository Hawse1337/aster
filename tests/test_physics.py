"""Module C tests: sensitivity recovery, coupling, local proposal, diagnosis."""
from __future__ import annotations

import math
import os
import unittest

from aster.core import Constraint, Objective
from aster.physics.diagnosis import diagnose, pairwise_interactions
from aster.physics.local_opt import propose_local_step
from aster.physics.sensitivity import SensitivityMatrix, compute_sensitivity, screen_parameters
from aster.synthetic import build_case

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")


class AnalyticProblem:
    """Two parameters, two metrics with known first and second derivatives."""

    def metrics(self, params):
        x1, x2 = float(params["x1"]), float(params["x2"])
        gain = 2.0 + 6.0 * x1 - 3.0 * x2
        return {
            "realized_gain_h": gain,
            "peak_gain": gain,
            "s11": -12.0 - 8.0 * x1 + 1.5 * x2,
            "coupling": 1.0 + 10.0 * x1 * x2,
        }


class Evaluator:
    def __init__(self, case, problem):
        self.case = case
        self.problem = problem
        self.calls = 0
        self.seen = {}

    def __call__(self, params):
        from aster.optimize import evaluate_constraints

        key = tuple(sorted(params.items()))
        if key in self.seen:
            return self.seen[key]
        metrics = self.problem.metrics(params)
        feasible, violations, total = evaluate_constraints(metrics, self.case)
        entry = type("E", (), {})()
        entry.metrics = metrics
        entry.feasible = feasible
        entry.violations = violations
        entry.total_violation = total
        self.calls += 1
        self.seen[key] = entry
        return entry


class SensitivityTests(unittest.TestCase):
    def setUp(self):
        self.case = build_case()
        self.problem = AnalyticProblem()
        self.evaluator = Evaluator(self.case, self.problem)

    def test_recovers_linear_derivatives(self):
        matrix = compute_sensitivity(
            self.case, self.evaluator, {"x1": 0.5, "x2": 0.5},
            metric_kinds=["peak_gain", "s11"],
        )
        self.assertAlmostEqual(matrix.raw["peak_gain"]["x1"], 6.0, places=6)
        self.assertAlmostEqual(matrix.raw["peak_gain"]["x2"], -3.0, places=6)
        self.assertAlmostEqual(matrix.raw["s11"]["x1"], -8.0, places=6)

    def test_normalisation_scales_to_unit_range(self):
        matrix = compute_sensitivity(
            self.case, self.evaluator, {"x1": 0.5, "x2": 0.5},
            metric_kinds=["peak_gain"],
        )
        normalized = matrix.normalized["peak_gain"]["x1"]
        base = matrix.base_params  # noqa: F841 - documentation only
        self.assertGreater(abs(normalized), 0.0)
        self.assertGreater(abs(normalized), abs(matrix.normalized["peak_gain"]["x2"]))

    def test_top_params_ranking(self):
        matrix = compute_sensitivity(
            self.case, self.evaluator, {"x1": 0.5, "x2": 0.5},
            metric_kinds=["peak_gain"],
        )
        top = matrix.top_params("peak_gain", 2)
        self.assertEqual(top[0][0], "x1")

    def test_screen_parameters_charges_few_runs(self):
        scored = screen_parameters(self.case, self.evaluator, {"x1": 0.5, "x2": 0.5},
                                   metric_kind="realized_gain_h", top_k=2)
        self.assertEqual(len(scored), 2)
        self.assertLessEqual(self.evaluator.calls, 1 + 2 + 2)

    def test_evaluations_are_counted(self):
        matrix = compute_sensitivity(self.case, self.evaluator,
                                     {"x1": 0.5, "x2": 0.5},
                                     metric_kinds=["realized_gain_h"])
        self.assertGreater(matrix.evaluations, 0)


class LocalOptTests(unittest.TestCase):
    def setUp(self):
        self.case = build_case()
        self.evaluator = Evaluator(self.case, AnalyticProblem())
        self.matrix = compute_sensitivity(
            self.case, self.evaluator, {"x1": 0.5, "x2": 0.5},
            metric_kinds=["peak_gain", "s11"],
        )

    def test_proposal_improves_target_and_respects_protection(self):
        proposal = propose_local_step(
            self.matrix, target_kind="peak_gain", desired_change=1.0,
            protected={"s11": 0.5}, trust_region=0.3,
        )
        self.assertTrue(proposal.accepted, proposal.reason)
        self.assertGreater(proposal.predicted_target_change, 0.0)
        self.assertLessEqual(abs(proposal.predicted_protected_change["s11"]), 0.5 + 1e-9)

    def test_tight_protection_blocks_large_step(self):
        proposal = propose_local_step(
            self.matrix, target_kind="peak_gain", desired_change=5.0,
            protected={"s11": 0.01}, trust_region=0.3,
        )
        self.assertFalse(proposal.accepted)

    def test_zero_budget_still_allows_motion_in_the_null_space(self):
        matrix = SensitivityMatrix(base_params={}, raw={
            "gain": {"x": 1, "y": 1}, "protected": {"x": 1, "y": 0}},
            params=["x", "y"], steps={"x": .01, "y": .02}, parameter_spans={"x": 1, "y": 1})
        proposal = propose_local_step(matrix, target_kind="gain", desired_change=1,
                                      protected={"protected": 0}, trust_region=1)
        self.assertTrue(proposal.accepted, proposal.reason)
        self.assertAlmostEqual(proposal.delta["x"], 0)
        self.assertAlmostEqual(proposal.delta["y"], 1)
        self.assertAlmostEqual(proposal.predicted_target_change, 1)

    def test_nonzero_slabs_and_trust_ball_in_scaled_coordinates(self):
        # With u=x/span the normalised target gradient is [1,1,1]. At the
        # negative support point two slabs bind and the last coordinate uses
        # the remaining radius. The requested change is beyond that support.
        matrix = SensitivityMatrix(base_params={}, raw={"gain": {"x": .5, "y": 2, "z": 1},
            "p": {"x": .5}, "q": {"y": 2}}, params=["x","y","z"],
            steps={"x":.1,"y":.01,"z":.1}, parameter_spans={"x":2,"y":.5,"z":1})
        proposal = propose_local_step(matrix, target_kind="gain", desired_change=-2,
                                      protected={"p": .2, "q": .3}, trust_region=1)
        self.assertTrue(proposal.accepted, proposal.reason)
        self.assertAlmostEqual(proposal.delta["x"], -.4)
        self.assertAlmostEqual(proposal.delta["y"], -.15)
        self.assertAlmostEqual(proposal.delta["z"], -math.sqrt(.87))
        self.assertAlmostEqual(proposal.predicted_target_change, -.5 - math.sqrt(.87))

    def test_unknown_target_rejected(self):
        proposal = propose_local_step(self.matrix, target_kind="nope", desired_change=1.0)
        self.assertFalse(proposal.accepted)


class CouplingTests(unittest.TestCase):
    def test_detects_product_interaction(self):
        case = build_case()
        evaluator = Evaluator(case, AnalyticProblem())
        effects = pairwise_interactions(
            case, evaluator, {"x1": 0.5, "x2": 0.5},
            [("x1", "x2")], metric_kind="coupling",
        )
        value = effects["x1|x2"]
        # second-order cross derivative of 10*x1*x2 is exactly 10
        self.assertAlmostEqual(abs(value), 10.0, places=6)


class DiagnosisTests(unittest.TestCase):
    def setUp(self):
        self.case = build_case()
        self.evaluator = Evaluator(self.case, AnalyticProblem())

    def test_reports_failing_and_protected_metrics(self):
        metrics = {"peak_gain": 6.5, "s11": -13.0}
        report = diagnose(self.case, metrics, None)
        self.assertEqual(report["failing_constraints"], [])
        self.assertEqual(report["protected_metrics"], ["s11", "peak_gain"])
        self.assertTrue(report["requires_hfss_validation"])

    def test_failing_metric_suggests_local_optimisation_with_sensitivity(self):
        matrix = compute_sensitivity(
            self.case, self.evaluator, {"x1": 0.5, "x2": 0.5},
            metric_kinds=["peak_gain", "s11"],
        )
        report = diagnose(self.case, {"peak_gain": 3.0, "s11": -13.0}, matrix,
                          budget_left=10)
        self.assertEqual(report["failing_constraints"], ["peak_gain"])
        self.assertEqual(report["recommended_strategy"], "local_constrained_optimization")
        self.assertIn("x1", report["sensitive_parameters"])
        self.assertEqual(report["confidence"], "medium")

    def test_no_sensitivity_suggests_scan(self):
        report = diagnose(self.case, {"peak_gain": 3.0, "s11": -13.0}, None)
        self.assertEqual(report["recommended_strategy"], "sensitivity_scan")

    def test_stalled_and_persistent_failure_suggests_topology(self):
        matrix = compute_sensitivity(
            self.case, self.evaluator, {"x1": 0.5, "x2": 0.5},
            metric_kinds=["peak_gain", "s11"],
        )
        report = diagnose(self.case, {"peak_gain": 1.0, "s11": -5.0}, matrix,
                          stalled_rounds=4, budget_left=20)
        self.assertEqual(report["recommended_strategy"], "topology_search")


if __name__ == "__main__":
    unittest.main()






