import json
from pathlib import Path
import tempfile
import unittest

from antopt.core import ParamSpace, ParamSpec
from antopt.doe import plan_doe, load_points, enqueue_plan


class DoeContractTests(unittest.TestCase):
    def test_queue_does_not_confuse_other_cases_or_measurement_recipes(self):
        from antopt.runtime.store import JobStore
        from antopt.learning_contract import case_contract
        from antopt.cases.lens28_var import build_case
        case = build_case()
        contract = case_contract(case)
        params = case.family.param_space.default()
        with tempfile.TemporaryDirectory() as temporary:
            store = JobStore(Path(temporary) / "queue.sqlite")
            store.enqueue([params], "other", "other")
            result = enqueue_plan(store, [params], case.case_id, "new", learning_contract=contract)
            self.assertEqual(result["enqueued"], 1)
            again = enqueue_plan(store, [params], case.case_id, "again", learning_contract=contract)
            self.assertEqual(again["enqueued"], 0)

    def test_geometry_filter_and_recipe_are_preserved(self):
        space = ParamSpace([ParamSpec("radius", "mm", 1, 3, 2)])
        contract = {"identity": "mesh-and-model-A"}
        plan = plan_doe(space, "test", count=8, attempts=12,
                        validator=lambda p: p["radius"] >= 2, learning_contract=contract)
        self.assertEqual(len(plan.points), 8)
        self.assertTrue(all(p["radius"] >= 2 for p in plan.points))
        other = plan_doe(space, "test", count=8, attempts=12,
                         validator=lambda p: p["radius"] >= 2, learning_contract={"identity": "B"})
        self.assertNotEqual(plan.hashes, other.hashes)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            path.write_text(json.dumps(plan.to_dict()))
            self.assertEqual(load_points(path, expected_contract=contract), plan.points)
            with self.assertRaisesRegex(ValueError, "contract"):
                load_points(path, expected_contract={"identity": "B"})


if __name__ == "__main__":
    unittest.main()
