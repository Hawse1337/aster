"""Failure evidence, hard measurement budgets and real strategy dispatch (offline)."""
from dataclasses import replace
import math
from types import SimpleNamespace
import unittest

import numpy as np

from aster.controller import Decision, GLOBAL, LOCAL, PHYSICS, STOP, SURROGATE, TOPOLOGY, StrategyController
from aster.cost import fit_cost_model
from aster.core import ParamSpec
from aster.experiment import run_experiment
from aster.optimize import Evaluation, evaluate_constraints
from aster.physics.sensitivity import BudgetedEvaluator, EvaluationBudgetExceeded, compute_sensitivity
from aster.risk import failure_kind, fit_risk_model
from aster.robustness import score_robustness
from aster.strategy_loop import run_local_search
from aster.synthetic import build_case, SyntheticProblem
from aster.topology.operators import FamilyManager, FamilyVersion, StructureOperator


def row(index, status="COMPLETED", error_code=None, seconds=100, x=0.5):
    return {"run_id": str(index), "params": {"x": x}, "status": status,
            "error_code": error_code, "total_seconds": seconds}


class EvidenceTests(unittest.TestCase):
    def test_infrastructure_failure_is_not_a_failed_geometry(self):
        rows = [row(0), row(1, "FAILED", "AEDT_CONNECT_FAILED"), row(2, "FAILED", "E_NOT_CONVERGED")]
        estimate = fit_risk_model(rows, ["x"]).predict({"x": 0.5})
        self.assertEqual(estimate.physical_samples, 2)
        self.assertEqual(estimate.environment_samples, 1)
        self.assertAlmostEqual(estimate.physical_failure_rate, 0.5)
        self.assertAlmostEqual(estimate.failure_rate, 2 / 3)
        self.assertGreater(estimate.risk_upper, estimate.risk_lower)

    def test_unlabelled_failure_and_timeout_remain_unknown_physics(self):
        estimate = fit_risk_model([row(0, "FAILED"), row(1, "FAILED", "STALLED")], ["x"]).predict({"x": .5})
        self.assertFalse(estimate.known)
        self.assertIsNone(estimate.physical_failure_rate)
        self.assertEqual(estimate.risk_upper, 1)
        self.assertEqual(estimate.unknown_samples, 1)
        self.assertEqual(estimate.censored_samples, 1)

    def test_one_success_does_not_claim_zero_physical_risk(self):
        estimate = fit_risk_model([row(0)], ["x"]).predict({"x": .5})
        self.assertGreater(estimate.physical_failure_rate, 0)
        self.assertGreater(estimate.risk_upper, .5)

    def test_global_success_prior_is_not_local_evidence_far_away(self):
        rows = [row(index, x=.1 + index * .01) for index in range(5)]
        estimate = fit_risk_model(rows, ["x"]).predict({"x": .9})
        self.assertEqual(estimate.method, "global")
        self.assertFalse(estimate.known)
        self.assertFalse(estimate.local_known)
        self.assertEqual(estimate.physical_samples, 0)
        self.assertIsNone(estimate.physical_failure_rate)
        self.assertEqual(estimate.risk_upper, 1)
        self.assertEqual(estimate.global_prior["physical_samples"], 5)
        self.assertGreater(estimate.global_prior["physical_failure_rate"], 0)

    def test_nonfinite_parameters_are_not_training_rows(self):
        estimate = fit_risk_model([row(0, x=math.nan)], ["x"]).predict({"x": .5})
        self.assertFalse(estimate.known)

    def test_failure_kind_uses_evidence_code(self):
        self.assertEqual(failure_kind(row(0, "FAILED", "E_SWEEP_EXPORT_INCOMPLETE")), "environment")
        self.assertEqual(failure_kind(row(0, "FAILED", "GEOMETRY_REJECTED")), "physical")

    def test_fast_failed_setup_does_not_make_successful_solve_cheap(self):
        rows = [row(0, seconds=1200), row(1, "FAILED", "AEDT_CONNECT_FAILED", seconds=1)]
        estimate = fit_cost_model(rows, ["x"]).predict({"x": .5})
        self.assertEqual(estimate.samples, 1)
        self.assertEqual(estimate.seconds, 1200)
        self.assertGreaterEqual(estimate.conservative_seconds, 1200)
        self.assertEqual(estimate.failure_groups["environment:CONNECTING"]["samples"], 1)

    def test_timeout_is_local_censored_lower_bound(self):
        rows = [row(0, seconds=100), row(1, "FAILED", "STALLED", seconds=700)]
        estimate = fit_cost_model(rows, ["x"]).predict({"x": .5})
        self.assertEqual(estimate.seconds, 100)
        self.assertEqual(estimate.lower_bound_seconds, 700)
        self.assertGreaterEqual(estimate.conservative_seconds, 700)
        self.assertEqual(estimate.censored_samples, 1)

    def test_unknown_cost_is_not_a_zero_upper_bound(self):
        estimate = fit_cost_model([row(0, "FAILED", "STALLED", seconds=700)], ["x"]).predict({"x": .5})
        self.assertEqual(estimate.lower_bound_seconds, 700)
        self.assertIsNone(estimate.conservative_seconds)
        self.assertFalse(estimate.to_dict()["known"])


class TrackedExecutor:
    def __init__(self, case, feasible=True):
        self.case, self.candidates, self.force_feasible = case, [], feasible

    def evaluate(self, candidate):
        self.candidates.append(candidate)
        metrics = SyntheticProblem().metrics(candidate.params)
        metrics["peak_gain"] += float(candidate.params.get("boost", 0))
        feasible, violations, total = evaluate_constraints(metrics, self.case)
        if not self.force_feasible:
            feasible, violations, total = False, {"peak_gain": 1.0}, 1.0
        return Evaluation(candidate.candidate_id, candidate.family_id, candidate.params,
                          metrics=metrics, feasible=feasible, violations=violations,
                          total_violation=total, status="COMPLETED")


class PlannedController(StrategyController):
    def __init__(self, plan):
        super().__init__()
        self.plan = iter(plan)

    def _decide(self, context):
        return Decision(next(self.plan, STOP), ["offline strategy plan"], context.budget_left)


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.case = build_case()

    def test_each_probe_is_guarded_and_cached_baseline_is_free(self):
        executor = TrackedExecutor(self.case)
        evaluate = BudgetedEvaluator(self.case, lambda params: executor.evaluate(self.case.make_candidate(params)), 2)
        matrix = compute_sensitivity(self.case, evaluate, {"x1": .5, "x2": .5})
        self.assertEqual(evaluate.calls, 2)
        self.assertEqual(len(executor.candidates), 2)
        self.assertEqual(matrix.evaluations, 2)
        self.assertFalse(matrix.complete)
        evaluate({"x1": .5, "x2": .5})
        self.assertEqual(evaluate.calls, 2)
        with self.assertRaises(EvaluationBudgetExceeded):
            evaluate({"x1": .9, "x2": .9})

    def test_failed_attempt_consumes_one_budget_slot(self):
        def failing(params):
            raise RuntimeError("offline transport fault")
        evaluate = BudgetedEvaluator(self.case, failing, 1)
        with self.assertRaises(RuntimeError):
            evaluate({"x1": .5, "x2": .5})
        with self.assertRaises(EvaluationBudgetExceeded):
            evaluate({"x1": .6, "x2": .5})
        self.assertEqual(evaluate.calls, 1)

    def test_local_radius_uses_measured_prediction_agreement(self):
        executor = TrackedExecutor(self.case)
        class Runner:
            case = self.case
            records = []
            def evaluate(inner, params):
                entry = executor.evaluate(self.case.make_candidate(params))
                inner.records.append(entry)
                return entry
        runner = Runner()
        runner.evaluate({"x1": .5, "x2": .5})
        result = run_local_search(runner, target_kind="peak_gain", desired_change=.4,
                                  budget=5, max_iterations=1, step_fraction=.1)
        self.assertEqual(result.evaluations, 5)
        self.assertTrue(result.steps[0].improved)
        self.assertGreater(result.steps[0].agreement_ratio, .75)
        self.assertGreater(result.final_trust_region, .4)

    def test_local_radius_shrinks_after_an_unreliable_proposal(self):
        executor = TrackedExecutor(self.case)
        class Runner:
            case = self.case
            records = []
            def evaluate(inner, params):
                entry = executor.evaluate(self.case.make_candidate(params))
                # The fifth new call is the proposal, after four derivative probes.
                if len(inner.records) == 5:
                    entry.metrics["peak_gain"] = 6.0
                inner.records.append(entry)
                return entry
        runner = Runner()
        runner.evaluate({"x1": .5, "x2": .5})
        result = run_local_search(runner, target_kind="peak_gain", desired_change=.4,
                                  budget=5, max_iterations=1, step_fraction=.1)
        self.assertFalse(result.steps[0].improved)
        self.assertLess(result.final_trust_region, .4)

    def test_experiment_cannot_overrun_last_probe_slot(self):
        executor = TrackedExecutor(self.case)
        result = run_experiment(self.case, executor, budget=5, batch=4,
            controller=PlannedController([GLOBAL, PHYSICS]))
        self.assertEqual(len(executor.candidates), 5)
        self.assertEqual(result["evaluations"], 5)

    def test_progress_does_not_stop_after_two_measured_rounds(self):
        executor = TrackedExecutor(self.case)
        result = run_experiment(self.case, executor, budget=7, batch=2,
            controller=PlannedController([GLOBAL] * 5))
        self.assertEqual(result["evaluations"], 7)
        self.assertGreaterEqual(len(result["steps"]), 4)

    def test_infeasible_evidence_produces_failing_context(self):
        executor = TrackedExecutor(self.case, feasible=False)
        result = run_experiment(self.case, executor, budget=6, batch=2)
        self.assertEqual(result["decisions"][0]["decision"]["strategy"], GLOBAL)
        self.assertTrue(result["decisions"][1]["context"]["failing"])

    def test_unavailable_strategy_is_explicit_stop(self):
        executor = TrackedExecutor(self.case)
        result = run_experiment(self.case, executor, budget=5,
            controller=PlannedController([TOPOLOGY]))
        self.assertEqual(result["evaluations"], 0)
        self.assertEqual(result["steps"][0]["strategy"], STOP)
        self.assertIn("callback unavailable", " ".join(result["steps"][0]["reasons"]))

    def test_surrogate_callback_fits_and_ranks_without_faking_measurements(self):
        calls = []
        def surrogate(**context):
            measured = list(context["archive"].entries.values())
            x = np.array([[1, entry.params["x1"], entry.params["x2"]] for entry in measured])
            y = np.array([entry.metrics["peak_gain"] for entry in measured])
            weights = np.linalg.lstsq(x, y, rcond=None)[0]
            grid = [{"x1": a, "x2": b} for a in (.1, .5, .9) for b in (.2, .8)]
            pick = max(grid, key=lambda p: np.dot([1, p["x1"], p["x2"]], weights))
            calls.append(pick)
            return [pick]
        executor = TrackedExecutor(self.case)
        result = run_experiment(self.case, executor, budget=5, batch=3,
            controller=PlannedController([GLOBAL, SURROGATE, STOP]),
            strategy_callbacks={SURROGATE: surrogate})
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["steps"][1]["strategy"], SURROGATE)
        self.assertEqual(executor.candidates[-1].params, calls[0])
        self.assertTrue(all(entry.status == "COMPLETED" for entry in result["archive"].entries.values()))

    def test_topology_callback_evaluates_explicit_descendant_family(self):
        manager = FamilyManager()
        manager.add_root(FamilyVersion(self.case.family.family_id, self.case.family.antenna_type,
                                      None, None, self.case.family.param_space))
        operator = StructureOperator("add_boost", "offline added feature", (self.case.family.antenna_type,),
            "adds a synthetic geometry feature", extra_params=(ParamSpec("boost", "", 0, 1, .5),))
        def topology(**context):
            version = manager.fork(self.case.family.family_id, operator, {"boost": .5}, new_id="descendant")
            child = replace(self.case, family=version.to_family_spec())
            context["evaluator"].evaluate_case(child, child.family.param_space.default())
        executor = TrackedExecutor(self.case)
        result = run_experiment(self.case, executor, budget=4, batch=2,
            controller=PlannedController([GLOBAL, TOPOLOGY, STOP]), strategy_callbacks={TOPOLOGY: topology})
        self.assertEqual(result["steps"][1]["strategy"], TOPOLOGY)
        self.assertEqual(executor.candidates[-1].family_id, "descendant")
        self.assertIn("boost", executor.candidates[-1].params)
        self.assertEqual(result["evaluations"], 3)
        self.assertNotEqual(self.case.family.family_id, "descendant")


class RobustnessEvidenceTests(unittest.TestCase):
    def test_minimised_objective_worst_is_maximum(self):
        result = score_robustness([{"ripple": 1}, {"ripple": 3}], keys=("ripple",), directions={"ripple": "minimize"})
        self.assertEqual(result["worst"]["ripple"], 3)

    def test_failed_and_absent_planned_points_reduce_yield(self):
        result = score_robustness([{"status": "COMPLETED", "metrics": {"peak_gain": 7}},
                                   {"status": "FAILED", "metrics": {"peak_gain": 9}}],
            keys=("peak_gain",), constraints={"peak_gain": (">=", 6)}, planned_count=4)
        self.assertEqual(result["yield"], .25)
        self.assertEqual(result["missing_count"], 2)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["worst"]["peak_gain"], 7)
        self.assertFalse(result["worst_verified"])

    def test_nonfinite_value_cannot_pass_unconstrained_plan(self):
        result = score_robustness([{"peak_gain": math.nan}], keys=("peak_gain",))
        self.assertEqual(result["yield"], 0)


if __name__ == "__main__":
    unittest.main()
