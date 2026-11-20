"""Queueing a DoE plan is idempotent and skips what the campaign already measured."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from aster.cases.lens28_var import build_case
from aster.distributed import config_hash
from aster.doe import enqueue_plan, plan_doe
from aster.runtime.store import JobStore


def _space():
    return build_case().family.param_space


class EnqueueTests(unittest.TestCase):
    def test_a_plan_is_queued_once(self):
        plan = plan_doe(_space(), "lens28_var", count=4, seed=1)
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "queue.sqlite")
            summary = enqueue_plan(store, plan, "lens28_var", "doe-test")
            self.assertEqual(summary["enqueued"], 4)
            self.assertEqual(summary["planned"], 4)
            self.assertEqual(len(store.queued()), 4)
            self.assertEqual(len(summary["run_ids"]), 4)

    def test_queueing_the_same_plan_again_adds_nothing(self):
        plan = plan_doe(_space(), "lens28_var", count=4, seed=1)
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "queue.sqlite")
            enqueue_plan(store, plan, "lens28_var", "doe-test")
            again = enqueue_plan(store, plan, "lens28_var", "doe-test")
        self.assertEqual(again["enqueued"], 0)
        self.assertEqual(again["skipped_existing"], 4)

    def test_a_measured_point_is_not_queued_again(self):
        plan = plan_doe(_space(), "lens28_var", count=3, seed=2)
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "queue.sqlite")
            store.save_job({"run_id": "measured", "status": "COMPLETED", "case": "lens28_var",
                            "params": dict(plan.points[0]), "metrics": {"peak_gain": 6.0}})
            summary = enqueue_plan(store, plan, "lens28_var", "doe-test")
            queued = store.queued()
        self.assertEqual(summary["enqueued"], 2)
        self.assertEqual(summary["skipped_existing"], 1)
        queued_params = {json.dumps(row["params"], sort_keys=True) for row in queued}
        self.assertNotIn(json.dumps(plan.points[0], sort_keys=True), queued_params)

    def test_a_failed_point_is_queued_again(self):
        plan = plan_doe(_space(), "lens28_var", count=2, seed=4)
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "queue.sqlite")
            store.save_job({"run_id": "failed", "status": "FAILED",
                            "params": dict(plan.points[0]), "error_code": "NO_AEDT"})
            summary = enqueue_plan(store, plan, "lens28_var", "doe-test")
            queued = store.queued()
        self.assertEqual(summary["enqueued"], 2, "a failed design was not measured")
        self.assertEqual(len(queued), 2)

    def test_identity_matches_the_distributed_scheduler(self):
        plan = plan_doe(_space(), "lens28_var", count=2, seed=5)
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "queue.sqlite")
            enqueue_plan(store, plan, "lens28_var", "doe-test")
            queued = store.queued()
            hashes = {config_hash("lens28_var", row["params"]) for row in queued}
        self.assertEqual(hashes, set(plan.hashes))

    def test_an_empty_plan_is_reported_without_touching_the_queue(self):
        plan = plan_doe(_space(), "lens28_var", count=1, seed=0)
        plan.points = []
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "queue.sqlite")
            summary = enqueue_plan(store, plan, "lens28_var", "doe-test")
            self.assertEqual(store.queued(), [])
        self.assertEqual(summary["planned"], 0)
        self.assertEqual(summary["enqueued"], 0)


class PlanFileTests(unittest.TestCase):
    def test_the_script_can_read_the_committed_plan(self):
        from aster.doe import load_points

        path = Path(__file__).resolve().parent.parent / "docs" / "evidence" / "doe_plan.json"
        if not path.is_file():
            self.skipTest("no committed plan")
        points = load_points(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(points), len(payload["points"]))
        self.assertGreaterEqual(len(points), 8)


if __name__ == "__main__":
    unittest.main()
