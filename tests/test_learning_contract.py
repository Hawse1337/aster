"""Evidence contracts and production recommendation invariants; no solver required."""
from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from antopt.core import CaseSpec, Constraint, FamilySpec, Objective, ParamSpace, ParamSpec
from antopt.learning_contract import case_contract, compatibility
from antopt.recommend import recommend_from_runs, training_set
from antopt.surrogate.backends import BatchSelection


def case():
    space = ParamSpace([ParamSpec("n", "", 0, 10, 5, kind="int"),
                        ParamSpec("x", "mm", 0, 10, 5, integer_step=1)])
    return CaseSpec("test", "test", "HFSS", (24, 32), FamilySpec("test", "test", space),
        [Constraint("gain", "peak_gain", ">=", 6), Constraint("ripple", "ripple", "<=", 3)],
        [Objective("gain", "peak_gain", "maximize"), Objective("ripple", "ripple", "minimize")])


def rows(spec, contract):
    return [{"run_id": "r%d" % i, "case": spec.case_id, "status": "COMPLETED",
             "params": {"n": i, "x": float(i)}, "total_seconds": 100,
             "metrics": {"peak_gain": 6 + i / 10, "ripple": 2 - i / 10},
             "telemetry": {"learning_contract": copy.deepcopy(contract)}} for i in range(6)]


class LearningContractTests(unittest.TestCase):
    def setUp(self):
        self.case = case()
        self.contract = case_contract(self.case)
        self.rows = rows(self.case, self.contract)

    def recommend(self, data=None, **kwargs):
        return recommend_from_runs(self.rows if data is None else data, case=self.case,
            contract=self.contract, names=["n", "x"], bounds=[(0, 10), (0, 10)],
            parameter_specs=list(self.case.family.param_space), extrapolate=True,
            defaults=self.case.family.param_space.default(), **kwargs)

    def test_objective_directions_and_missing_hard_metrics_share_case_spec(self):
        data = copy.deepcopy(self.rows)
        data[0]["metrics"].pop("ripple")
        data[1]["metrics"]["peak_gain"] = float("nan")
        x, y, used = training_set(data, ["n", "x"], case=self.case, contract=self.contract)
        self.assertEqual(len(x), 4)
        self.assertEqual(y[0].tolist(), [-6.2, 1.8])
        self.assertEqual(used[0]["run_id"], "r2")

    def test_old_dimensions_are_not_filled_from_defaults(self):
        data = copy.deepcopy(self.rows)
        data[0]["params"].pop("x")
        x, _, _ = training_set(data, ["n", "x"], defaults={"n": 5, "x": 5},
                               case=self.case, contract=self.contract)
        self.assertEqual(len(x), 5)
        self.assertNotEqual(x[0].tolist(), [0, 5])

    def test_case_structure_recipe_and_legacy_rows_are_isolated(self):
        variants = []
        for key in ("case_id", "structure_hash", "recipe_hash"):
            row = copy.deepcopy(self.rows[0])
            row["telemetry"]["learning_contract"][key] = "different"
            variants.append(row)
        legacy = copy.deepcopy(self.rows[0])
        legacy.pop("telemetry")
        variants.append(legacy)
        self.assertTrue(all(not compatibility(row, self.contract)[0] for row in variants))
        _, _, used = training_set(self.rows + variants, ["n", "x"],
                                  case=self.case, contract=self.contract)
        self.assertEqual(len(used), 6)

    def test_recipe_change_invalidates_identity_but_objective_rescore_does_not(self):
        other = case_contract(self.case, measurement_recipe={"sweep_step_ghz": 0.1})
        self.assertNotEqual(other["identity"], self.contract["identity"])
        self.case.constraints[0] = Constraint("gain", "peak_gain", ">=", 7)
        self.assertEqual(case_contract(self.case)["identity"], self.contract["identity"])

    def test_cold_start_is_bounded_validated_and_never_calls_surrogate(self):
        with patch("antopt.recommend.select_batch", side_effect=AssertionError("not a GP yet")):
            result = self.recommend([], count=8, validator=lambda params: params["x"] >= 5)
        self.assertEqual(result["method"], "controlled_exploration")
        self.assertGreater(len(result["recommendations"]), 0)
        self.assertLessEqual(len(result["recommendations"]), 2)
        for item in result["recommendations"]:
            self.assertIsNone(item["feasible"])
            self.assertEqual(item["feasibility_status"], "unknown")
            self.assertGreaterEqual(item["params"]["x"], 5)
            self.assertLessEqual(item["params"]["x"], 6)

    def test_constraints_pending_and_canonical_dedup_reach_acquisition(self):
        pending = {**self.rows[0], "run_id": "pending", "status": "QUEUED",
                   "params": {"n": 8, "x": 8}}
        selection = BatchSelection(np.asarray([[0, 0], [8, 8], [6.1, 6.2],
                                               [6.3, 6.4], [7.2, 7.1]]), "fake")
        with patch("antopt.recommend.select_batch", return_value=selection) as pick:
            result = self.recommend(self.rows + [pending], count=2)
        self.assertEqual(pick.call_args.kwargs["constraint_obs"].shape, (6, 2))
        self.assertTrue(np.all(pick.call_args.kwargs["constraint_obs"] <= 0))
        self.assertEqual(pick.call_args.kwargs["pending_X"].tolist(), [[8, 8]])
        self.assertEqual([row["params"] for row in result["recommendations"]],
                         [{"n": 6, "x": 6.0}, {"n": 7, "x": 7.0}])
        self.assertEqual(result["dropped_duplicates"], 3)
        self.assertTrue(all(row["feasible"] is None for row in result["recommendations"]))

    def test_total_budget_uses_sum_of_conservative_costs(self):
        cost = SimpleNamespace(predict=lambda p: SimpleNamespace(to_dict=lambda:
            {"seconds": 40, "spread": 5, "conservative_seconds": 70}))
        risk = SimpleNamespace(predict=lambda p: SimpleNamespace(to_dict=lambda:
            {"known": True, "risk_upper": 0.1}))
        selection = BatchSelection(np.asarray([[6, 6], [7, 7]]), "fake")
        with patch("antopt.recommend.select_batch", return_value=selection), \
             patch("antopt.cost.fit_cost_model", return_value=cost), \
             patch("antopt.risk.fit_risk_model", return_value=risk):
            result = self.recommend(count=2, budget_seconds=100)
        self.assertEqual(len(result["recommendations"]), 1)
        self.assertEqual(result["batch_conservative_seconds"], 70)
        self.assertEqual(len(result["over_budget"]), 1)

    def test_queue_persists_contract_without_changing_old_rows(self):
        from antopt.runtime.store import JobStore
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "queue.sqlite")
            first = store.enqueue([{"n": 5, "x": 5}], "test", "batch",
                                  learning_contract=self.contract)
            second = store.enqueue([{"n": 5, "x": 5}], "test", "batch")
            data = {row["run_id"]: row for row in store.jobs()}
            self.assertEqual(data[first[0]]["telemetry"]["learning_contract"], self.contract)
            self.assertEqual(data[second[0]]["telemetry"], {})
            self.assertNotEqual(first[0], second[0])


if __name__ == "__main__":
    unittest.main()
