"""The queue: enqueue from one place, claim atomically from another, never run a job twice."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from aster.runtime.store import JobStore


def store(tmp: str) -> JobStore:
    return JobStore(Path(tmp) / "queue.sqlite")


class EnqueueTests(unittest.TestCase):
    def test_enqueue_creates_queued_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = store(tmp)
            run_ids = jobs.enqueue([{"ring_outer": 2.2}, {"ring_outer": 2.3}], "lens28_var",
                                   "batch-test")
            self.assertEqual(len(run_ids), 2)
            queued = jobs.queued()
            self.assertEqual([row["run_id"] for row in queued], run_ids)
            self.assertEqual(queued[0]["status"], "QUEUED")
            self.assertEqual(queued[0]["case"], "lens28_var")
            self.assertEqual(queued[0]["params"], {"ring_outer": 2.2})


class ClaimTests(unittest.TestCase):
    def test_a_claim_marks_the_row_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = store(tmp)
            run_ids = jobs.enqueue([{"ring_outer": 2.2}], "lens28_var", "b1")
            claimed = jobs.claim_next("worker-a")
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed["run_id"], run_ids[0])
            self.assertEqual(claimed["status"], "RUNNING")
            self.assertEqual(claimed["stage"], "CLAIMED")
            self.assertEqual(jobs.queued(), [])

    def test_two_claims_get_different_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = store(tmp)
            run_ids = jobs.enqueue([{"ring_outer": 2.2}, {"ring_outer": 2.3}], "lens28_var", "b1")
            first = jobs.claim_next("worker-a")
            second = jobs.claim_next("worker-b")
            self.assertNotEqual(first["run_id"], second["run_id"])
            self.assertEqual({first["run_id"], second["run_id"]}, set(run_ids))

    def test_an_empty_queue_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(store(tmp).claim_next("worker-a"))

    def test_an_abandoned_claim_can_be_requeued(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = store(tmp)
            run_ids = jobs.enqueue([{"ring_outer": 2.2}], "lens28_var", "b1")
            jobs.claim_next("worker-a")
            self.assertTrue(jobs.requeue_claim(run_ids[0]))
            self.assertEqual([row["run_id"] for row in jobs.queued()], run_ids)
            self.assertEqual(jobs.claim_next("worker-b")["run_id"], run_ids[0])

    def test_a_requeue_does_not_touch_a_job_that_started(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = store(tmp)
            run_ids = jobs.enqueue([{"ring_outer": 2.2}], "lens28_var", "b1")
            jobs.claim_next("worker-a")
            jobs.save_job({"run_id": run_ids[0], "status": "RUNNING", "stage": "SOLVING",
                           "params": {"ring_outer": 2.2}})
            self.assertFalse(jobs.requeue_claim(run_ids[0]))

    def test_the_claim_survives_a_new_store_instance(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = store(tmp)
            run_ids = jobs.enqueue([{"ring_outer": 2.2}], "lens28_var", "b1")
            claimed = jobs.claim_next("worker-a")
            reopened = store(tmp)
            self.assertIsNone(reopened.claim_next("worker-b"))
            row = next(item for item in reopened.jobs() if item["run_id"] == run_ids[0])
            self.assertEqual(row["stage"], "CLAIMED")
            self.assertIn("worker-a", row["error_detail"])


if __name__ == "__main__":
    unittest.main()
