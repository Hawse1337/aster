"""Leases: atomic claim, expiry reclaim, idempotent finish, and dedupe by design identity."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from aster.distributed import DistributedScheduler, config_hash


def scheduler(tmp: str) -> DistributedScheduler:
    return DistributedScheduler(Path(tmp) / "dist.sqlite")


class EnqueueTests(unittest.TestCase):
    def test_a_design_is_named_by_its_parameters(self):
        first = config_hash("lens28_var", {"ring_outer": 2.3, "ring_height": 0.6})
        second = config_hash("lens28_var", {"ring_height": 0.6, "ring_outer": 2.3})
        other = config_hash("lens28_var", {"ring_outer": 2.4, "ring_height": 0.6})
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)

    def test_the_same_design_is_not_enqueued_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = scheduler(tmp)
            first = dist.enqueue("lens28_var", [{"ring_outer": 2.3}])
            second = dist.enqueue("lens28_var", [{"ring_outer": 2.3}])
            self.assertEqual(len(first["added"]), 1)
            self.assertEqual(second["added"], [])
            self.assertEqual(len(second["duplicates"]), 1)
            self.assertEqual(dist.snapshot()["total"], 1)


class ClaimTests(unittest.TestCase):
    def test_a_claim_leases_one_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = scheduler(tmp)
            dist.enqueue("lens28_var", [{"ring_outer": 2.3}, {"ring_outer": 2.4}])
            first = dist.claim("worker-a", lease_seconds=30)
            second = dist.claim("worker-b", lease_seconds=30)
            self.assertNotEqual(first.task_id, second.task_id)
            self.assertEqual({first.state, second.state}, {"LEASED"})
            self.assertIsNone(dist.claim("worker-c", lease_seconds=30))

    def test_an_expired_lease_can_be_claimed_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = scheduler(tmp)
            dist.enqueue("lens28_var", [{"ring_outer": 2.3}])
            task = dist.claim("worker-a", lease_seconds=0.05)
            time.sleep(0.1)
            again = dist.claim("worker-b", lease_seconds=30)
            self.assertIsNotNone(again)
            self.assertEqual(again.task_id, task.task_id)
            self.assertEqual(again.attempts, 2)

    def test_a_heartbeat_extends_only_the_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = scheduler(tmp)
            dist.enqueue("lens28_var", [{"ring_outer": 2.3}])
            task = dist.claim("worker-a", lease_seconds=0.2)
            self.assertFalse(dist.heartbeat(task.task_id, "worker-b", extend_seconds=30))
            self.assertTrue(dist.heartbeat(task.task_id, "worker-a", extend_seconds=30))
            time.sleep(0.25)
            self.assertIsNone(dist.claim("worker-c", lease_seconds=30))

    def test_reclaim_expired_puts_the_task_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = scheduler(tmp)
            dist.enqueue("lens28_var", [{"ring_outer": 2.3}])
            dist.claim("worker-a", lease_seconds=0.05)
            time.sleep(0.1)
            self.assertEqual(dist.reclaim_expired(), 1)
            self.assertEqual(dist.snapshot()["by_state"].get("QUEUED"), 1)

    def test_a_clean_release_makes_a_task_available_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = scheduler(tmp)
            dist.enqueue("lens28_var", [{"ring_outer": 2.3}])
            task = dist.claim("worker-a", lease_seconds=60)
            self.assertTrue(dist.release(task.task_id, "worker-a"))
            self.assertIsNotNone(dist.claim("worker-b", lease_seconds=60))


class FinishTests(unittest.TestCase):
    def test_a_finish_records_the_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = scheduler(tmp)
            dist.enqueue("lens28_var", [{"ring_outer": 2.3}])
            task = dist.claim("worker-a", lease_seconds=60)
            outcome = dist.finish(task.task_id, "worker-a",
                                  result={"status": "COMPLETED", "peak_gain": 6.1})
            self.assertTrue(outcome["ok"])
            snapshot = dist.snapshot()
            self.assertEqual(snapshot["by_state"].get("DONE"), 1)
            self.assertEqual(snapshot["tasks"][0]["result"]["peak_gain"], 6.1)

    def test_a_second_finish_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = scheduler(tmp)
            dist.enqueue("lens28_var", [{"ring_outer": 2.3}])
            task = dist.claim("worker-a", lease_seconds=60)
            dist.finish(task.task_id, "worker-a", result={"status": "COMPLETED"})
            again = dist.finish(task.task_id, "worker-a", result={"status": "COMPLETED"})
            self.assertTrue(again["ok"])
            self.assertTrue(again["duplicate"])
            self.assertEqual(dist.snapshot()["by_state"].get("DONE"), 1)

    def test_a_foreign_worker_cannot_finish_someone_elses_lease(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = scheduler(tmp)
            dist.enqueue("lens28_var", [{"ring_outer": 2.3}])
            task = dist.claim("worker-a", lease_seconds=60)
            outcome = dist.finish(task.task_id, "worker-b", result={"status": "COMPLETED"})
            self.assertFalse(outcome["ok"])
            self.assertEqual(outcome["error"], "LEASE_MISMATCH")

    def test_an_error_is_recorded_as_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = scheduler(tmp)
            dist.enqueue("lens28_var", [{"ring_outer": 2.3}])
            task = dist.claim("worker-a", lease_seconds=60)
            dist.finish(task.task_id, "worker-a", error="solver exploded")
            snapshot = dist.snapshot()
            self.assertEqual(snapshot["by_state"].get("FAILED"), 1)
            self.assertEqual(snapshot["tasks"][0]["error"], "solver exploded")

    def test_results_are_grouped_by_config_hash_for_merging(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = scheduler(tmp)
            dist.enqueue("lens28_var", [{"ring_outer": 2.3}])
            task = dist.claim("worker-a", lease_seconds=60)
            dist.finish(task.task_id, "worker-a", result={"status": "COMPLETED"})
            merged = dist.results_by_config()
            self.assertEqual(len(merged), 1)
            self.assertEqual(len(next(iter(merged.values()))), 1)


if __name__ == "__main__":
    unittest.main()
