"""Queue persistence: what a restart should remember and offer to finish."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from antopt.runtime.store import JobStore, summarise


def job(run_id: str, status: str = "COMPLETED", batch_id: str | None = "b1",
        ring: float = 2.2) -> dict:
    return {"run_id": run_id, "case": "lens28_var", "params": {"ring_outer": ring},
            "status": status, "stage": "DONE" if status == "COMPLETED" else "SOLVING",
            "metrics": {"peak_gain": 6.0} if status == "COMPLETED" else {},
            "feasible": status == "COMPLETED", "batch_id": batch_id}


class JobStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = JobStore(Path(self._tmp.name) / "queue.sqlite")

    def test_a_saved_job_comes_back_intact(self):
        self.store.save_job(job("r1"))
        rows = self.store.jobs()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["run_id"], "r1")
        self.assertEqual(rows[0]["params"], {"ring_outer": 2.2})
        self.assertEqual(rows[0]["metrics"], {"peak_gain": 6.0})
        self.assertTrue(rows[0]["feasible"])

    def test_saving_the_same_job_twice_updates_instead_of_duplicating(self):
        self.store.save_job(job("r1", status="QUEUED"))
        self.store.save_job(job("r1", status="COMPLETED"))
        rows = self.store.jobs()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "COMPLETED")

    def test_unfinished_lists_only_jobs_without_a_terminal_state(self):
        self.store.save_job(job("done", status="COMPLETED"))
        self.store.save_job(job("failed", status="FAILED"))
        self.store.save_job(job("running", status="RUNNING"))
        self.store.save_job(job("queued", status="QUEUED"))
        pending = {row["run_id"] for row in self.store.unfinished()}
        self.assertEqual(pending, {"running", "queued"})

    def test_batches_round_trip(self):
        self.store.save_batch({"batch_id": "b1", "case": "lens28_var", "state": "RUNNING",
                               "total": 3, "done": 1, "run_ids": ["r1"]})
        batches = self.store.batches()
        self.assertEqual(batches[0]["run_ids"], ["r1"])
        self.assertEqual(batches[0]["total"], 3)

    def test_a_finished_batch_is_not_resumable(self):
        self.store.save_batch({"batch_id": "b1", "state": "DONE", "total": 2, "done": 2})
        self.store.save_job(job("r1", status="COMPLETED"))
        self.assertEqual(self.store.resumable(), [])

    def test_a_stopped_batch_with_pending_jobs_is_resumable(self):
        self.store.save_batch({"batch_id": "b1", "state": "RUNNING", "total": 3, "done": 1})
        self.store.save_job(job("r1", status="COMPLETED"))
        self.store.save_job(job("r2", status="QUEUED", ring=2.35))
        self.store.save_job(job("r3", status="SOLVING", ring=2.45))
        resumable = self.store.resumable()
        self.assertEqual(len(resumable), 1)
        self.assertEqual(len(resumable[0]["pending"]), 2)

    def test_variants_to_resume_are_the_unfinished_parameters(self):
        self.store.save_batch({"batch_id": "b1", "state": "RUNNING", "total": 2, "done": 1})
        self.store.save_job(job("r1", status="COMPLETED", ring=2.2))
        self.store.save_job(job("r2", status="QUEUED", ring=2.35))
        self.assertEqual(self.store.variants_to_resume("b1"), [{"ring_outer": 2.35}])

    def test_the_schema_survives_reopening(self):
        self.store.save_job(job("r1"))
        reopened = JobStore(Path(self._tmp.name) / "queue.sqlite")
        self.assertEqual(len(reopened.jobs()), 1)

    def test_an_explanation_is_preserved(self):
        payload = job("r1", status="FAILED")
        payload["failure"] = {"code": "STALLED", "title": "求解停滞", "action": "重试",
                              "detail": "no progress", "retryable": True}
        self.store.save_job(payload)
        row = self.store.jobs()[0]
        self.assertEqual(row["failure"]["title"], "求解停滞")
        self.assertTrue(row["failure"]["retryable"])

    def test_summarise_reports_pending_counts(self):
        self.store.save_batch({"batch_id": "b1", "case": "lens28_var", "state": "RUNNING",
                               "total": 2, "done": 0})
        self.store.save_job(job("r1", status="QUEUED"))
        summary = summarise(self.store.resumable())
        self.assertEqual(summary["count"], 1)
        self.assertEqual(summary["batches"][0]["pending"], 1)


if __name__ == "__main__":
    unittest.main()
