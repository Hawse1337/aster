"""A run whose guard status stops moving must be reported, not waited on forever."""
from __future__ import annotations

import unittest

from antopt.status_watch import ACTIVE_STATUSES, StatusMonitor, StatusWatch


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class StatusWatchTests(unittest.TestCase):
    def _watch(self, statuses, stale_after=600.0):
        stream = iter(statuses)
        clock = _Clock()
        return StatusWatch(status_provider=lambda: next(stream), stale_after_s=stale_after,
                           clock=clock), clock

    def test_a_changing_status_is_progress(self):
        watch, clock = self._watch(["CREATED", "PREPARED", "SOLVING"])
        self.assertFalse(watch.sample())
        clock.now = 900.0
        self.assertFalse(watch.sample())   # it moved, so no stall
        clock.now = 1000.0
        self.assertFalse(watch.sample())

    def test_solving_is_not_a_stall_no_matter_how_long(self):
        # a real solve stays in SOLVING for twenty-five minutes; the CPU channel watches it instead
        watch, clock = self._watch(["SOLVING", "SOLVING", "SOLVING"], stale_after=60.0)
        watch.sample()
        clock.now = 600.0
        self.assertFalse(watch.sample())
        clock.now = 3000.0
        self.assertFalse(watch.sample())

    def test_an_unchanged_active_status_becomes_a_stall(self):
        watch, clock = self._watch(["PREPARED", "PREPARED", "PREPARED"], stale_after=600.0)
        self.assertFalse(watch.sample())
        clock.now = 300.0
        self.assertFalse(watch.sample())
        clock.now = 700.0
        self.assertTrue(watch.sample())

    def test_a_terminal_status_is_never_a_stall(self):
        watch, clock = self._watch(["SOLVING", "COMPLETED", "COMPLETED"], stale_after=60.0)
        watch.sample()
        clock.now = 120.0
        self.assertFalse(watch.sample())
        clock.now = 600.0
        self.assertFalse(watch.sample())

    def test_an_empty_status_is_not_a_stall(self):
        watch, clock = self._watch(["", ""], stale_after=1.0)
        watch.sample()
        clock.now = 10.0
        self.assertFalse(watch.sample())

    def test_the_history_records_each_transition(self):
        watch, clock = self._watch(["CREATED", "PREPARED"])
        watch.sample()
        clock.now = 10.0
        watch.sample()
        self.assertEqual([status for _when, status in watch.history], ["CREATED", "PREPARED"])

    def test_every_active_status_is_known(self):
        self.assertIn("CREATED", ACTIVE_STATUSES)
        self.assertIn("SOLVING", ACTIVE_STATUSES)
        self.assertNotIn("COMPLETED", ACTIVE_STATUSES)
        self.assertNotIn("FAILED", ACTIVE_STATUSES)


class StatusMonitorTests(unittest.TestCase):
    def test_the_callback_receives_the_stuck_status(self):
        seen: list[str] = []
        watch = StatusWatch(status_provider=lambda: "CREATED", stale_after_s=0.0,
                            clock=lambda: 1.0)
        monitor = StatusMonitor(watch, spawn=False, on_stall=seen.append)
        monitor.poll()          # first sample records the status
        self.assertTrue(monitor.poll())
        self.assertEqual(seen, ["CREATED"])

    def test_a_raising_callback_does_not_break_the_monitor(self):
        def boom(_status: str) -> None:
            raise RuntimeError("callback")

        watch = StatusWatch(status_provider=lambda: "CREATED", stale_after_s=0.0,
                            clock=lambda: 1.0)
        monitor = StatusMonitor(watch, spawn=False, on_stall=boom)
        monitor.poll()
        self.assertTrue(monitor.poll())

    def test_stop_is_safe_without_start(self):
        monitor = StatusMonitor(StatusWatch(status_provider=lambda: "SOLVING"), spawn=False)
        monitor.stop()


if __name__ == "__main__":
    unittest.main()
