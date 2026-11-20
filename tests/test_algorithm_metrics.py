"""Progress must use the complete current objective/constraint contract."""
from dataclasses import replace
import unittest

from aster.cases.lens28_var import build_case
from aster.metrics import hypervolume_progress


def record(identity="ok", **metrics):
    values = {"s11_bandwidth": 8, "peak_gain": 7, "directivity_horiz_db": 8,
              "beam_theta": 90, "ripple": 1, "sll": -10}
    values.update(metrics)
    return {"case": "lens28_var", "run_id": identity, "status": "COMPLETED",
            "created_utc": identity, "metrics": values, "feasible": True}


class CurrentContractProgressTests(unittest.TestCase):
    def test_four_dimensions_and_minimization_direction(self):
        case = build_case()
        first = hypervolume_progress([record("a", sll=-10)], case=case)
        second = hypervolume_progress([record("a", sll=-10), record("b", sll=-15)], case=case)
        self.assertEqual(len(first["keys"]), 4)
        self.assertEqual(first["directions"][-1], "minimize")
        self.assertGreater(second["best"], first["best"])
        self.assertAlmostEqual(sum(second["generation"]), second["best"], places=5)

    def test_old_feasibility_flag_cannot_admit_violations_or_wrong_case(self):
        wrong = record("wrong")
        wrong["case"] = "lens28_ref"
        missing = record("missing")
        del missing["metrics"]["beam_theta"]
        result = hypervolume_progress([wrong, missing, record("bad", directivity_horiz_db=1)], case=build_case())
        self.assertEqual(result["best"], 0)
        self.assertEqual(result["points"], [])
        self.assertEqual(result["excluded"]["case"], 1)
        self.assertEqual(result["excluded"]["constraints"], 2)

    def test_dominated_run_has_zero_marginal_gain(self):
        result = hypervolume_progress([record("a", sll=-15), record("b", sll=-10)], case=build_case())
        self.assertEqual(result["generation"][1], 0)

    def test_same_case_different_recipe_is_not_progress(self):
        from aster.learning_contract import case_contract
        case = build_case()
        wanted = case_contract(case)
        row = record()
        row["learning_contract"] = case_contract(case, measurement_recipe={"sweep_step": 99})
        result = hypervolume_progress([row], case=case, contract=wanted)
        self.assertEqual(result["best"], 0)
        self.assertEqual(result["excluded"]["contract"], 1)


if __name__ == "__main__":
    unittest.main()
