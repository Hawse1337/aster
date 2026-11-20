"""DoE planning: stratified, deduplicated by identity, resumable, without touching AEDT."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from aster.cases.lens28_var import build_case
from aster.core import latin_hypercube
from aster.distributed import config_hash
from aster.doe import already_measured, load_points, mean_nearest_neighbour, plan_doe


def _space():
    return build_case().family.param_space


class PlanTests(unittest.TestCase):
    def test_a_plan_has_the_requested_number_of_points(self):
        plan = plan_doe(_space(), "lens28_var", count=10, seed=3)
        self.assertEqual(len(plan.points), 10)
        self.assertEqual(len(set(plan.hashes)), 10, "no duplicate designs")

    def test_the_plan_is_deterministic_for_a_seed(self):
        first = plan_doe(_space(), "lens28_var", count=8, seed=11).points
        second = plan_doe(_space(), "lens28_var", count=8, seed=11).points
        self.assertEqual(first, second)

    def test_seeds_give_different_plans(self):
        first = plan_doe(_space(), "lens28_var", count=8, seed=1).points
        second = plan_doe(_space(), "lens28_var", count=8, seed=2).points
        self.assertNotEqual(first, second)

    def test_points_respect_the_parameter_space(self):
        space = _space()
        for point in plan_doe(space, "lens28_var", count=10, seed=5).points:
            with self.subTest(point=point):
                space.validate(point)

    def test_measured_designs_are_skipped_and_replaced(self):
        existing = latin_hypercube(_space(), 3, seed=99)
        measured = {config_hash("lens28_var", point): {"params": point} for point in existing}
        plan = plan_doe(_space(), "lens28_var", count=6, seed=99, measured=measured)
        planned = set(plan.hashes)
        self.assertFalse(planned & set(measured), "a measured design must not be planned again")
        self.assertEqual(len(plan.points), 6)

    def test_a_fully_measured_plan_is_reported_not_faked(self):
        space = _space()
        existing = latin_hypercube(space, 5, seed=7)
        measured = {config_hash("lens28_var", p): {"params": p} for p in existing}
        # one attempt cannot escape a fully measured seed: it must say so, not invent points
        exhausted = plan_doe(space, "lens28_var", count=5, seed=7, measured=measured,
                             attempts=1)
        self.assertEqual(exhausted.points, [])
        self.assertEqual(exhausted.to_dict()["planned"], 0)
        # the default planner retries with another seed and then has to find new designs
        retried = plan_doe(space, "lens28_var", count=5, seed=7, measured=measured)
        self.assertEqual(len(retried.points), 5)
        self.assertTrue(all(config_hash("lens28_var", p) not in measured
                            for p in retried.points))

    def test_the_payload_is_json_shaped(self):
        payload = plan_doe(_space(), "lens28_var", count=4, seed=0).to_dict()
        json.dumps(payload)
        self.assertEqual(payload["planned"], 4)
        self.assertGreater(payload["estimated_hours"], 0)

    def test_the_spread_check_prefers_the_stratified_sample(self):
        space = _space()
        stratified = mean_nearest_neighbour(latin_hypercube(space, 10, seed=2))
        clustered = mean_nearest_neighbour([space.default() for _ in range(10)])
        self.assertGreater(stratified, clustered)


class LedgerTests(unittest.TestCase):
    def test_measured_entries_are_keyed_by_identity(self):
        points = [{"ring_outer": 3.0}, {"ring_outer": 3.0}, {"ring_outer": 2.5}]
        measured = already_measured("lens28_var", points)
        self.assertEqual(len(measured), 2)

    def test_a_plan_file_can_be_resumed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            plan = plan_doe(_space(), "lens28_var", count=3, seed=1)
            path.write_text(json.dumps(plan.to_dict()), encoding="utf-8")
            points = load_points(path)
        self.assertEqual(points, plan.points)


if __name__ == "__main__":
    unittest.main()
