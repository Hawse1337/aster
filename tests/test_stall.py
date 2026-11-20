"""Stall detection: the window semantics, and that the monitor stops when it fires."""
from __future__ import annotations

import unittest

from aster.stall import StallMonitor, StallWatch, sample_sequence


class WatchSemanticsTests(unittest.TestCase):
    def test_the_first_sample_only_establishes_a_baseline(self):
        watch = StallWatch(sampler=iter([100.0, 50.0]).__next__, window_s=60.0,
                           clock=lambda: 0.0)
        self.assertFalse(watch.sample())

    def test_a_window_with_real_work_is_not_a_stall(self):
        ticks = iter([0.0, 61.0])
        values = iter([1000.0, 1300.0])
        watch = StallWatch(sampler=values.__next__, window_s=60.0, min_delta_s=15.0,
                           clock=ticks.__next__)
        self.assertFalse(watch.sample())
        self.assertFalse(watch.sample())

    def test_a_window_without_work_is_a_stall(self):
        ticks = iter([0.0, 61.0])
        values = iter([1000.0, 1001.4])
        watch = StallWatch(sampler=values.__next__, window_s=60.0, min_delta_s=15.0,
                           clock=ticks.__next__)
        self.assertFalse(watch.sample())
        self.assertTrue(watch.sample())

    def test_a_short_window_never_reports_before_it_elapses(self):
        ticks = iter([0.0, 10.0, 20.0])
        values = iter([1000.0, 1000.1, 1000.2])
        watch = StallWatch(sampler=values.__next__, window_s=60.0, clock=ticks.__next__)
        self.assertFalse(watch.sample())
        self.assertFalse(watch.sample())
        self.assertFalse(watch.sample())

    def test_the_window_restarts_after_a_stall_is_reported(self):
        ticks = iter([0.0, 61.0, 62.0])
        values = iter([1000.0, 1000.5, 1000.6])
        watch = StallWatch(sampler=values.__next__, window_s=60.0, clock=ticks.__next__)
        watch.sample()
        self.assertTrue(watch.sample())
        self.assertFalse(watch.sample())  # new baseline, window has not elapsed

    def test_samples_are_kept_for_the_log(self):
        watch = StallWatch(sampler=iter([1.0, 2.0]).__next__, window_s=0.0, clock=lambda: 0.0)
        watch.sample()
        watch.sample()
        self.assertEqual(watch.samples, [1.0, 2.0])


class MonitorTests(unittest.TestCase):
    def test_the_callback_fires_once_and_the_monitor_stops(self):
        fired: list[int] = []
        watch = sample_sequence([0.0, 1.0, 2.0, 3.0])
        monitor = StallMonitor(watch, spawn=False, on_stall=lambda: fired.append(1))
        self.assertFalse(monitor.poll())   # first reading is the baseline
        self.assertTrue(monitor.poll())
        self.assertEqual(len(fired), 1)
        monitor.stop()
        self.assertTrue(monitor.stalled)

    def test_a_callback_that_raises_does_not_break_the_monitor(self):
        def boom() -> None:
            raise RuntimeError("callback exploded")

        watch = sample_sequence([0.0, 1.0])
        monitor = StallMonitor(watch, spawn=False, on_stall=boom)
        monitor.poll()
        self.assertTrue(monitor.poll())

    def test_stop_is_safe_without_start(self):
        monitor = StallMonitor(sample_sequence([0.0]), spawn=False)
        monitor.stop()

    def test_a_threaded_monitor_runs_polls(self):
        import time

        fired: list[int] = []
        watch = StallWatch(sampler=lambda: 0.0, window_s=0.0, clock=lambda: 0.0)
        monitor = StallMonitor(watch, interval_s=0.01, on_stall=lambda: fired.append(1)).start()
        time.sleep(0.1)
        monitor.stop()
        self.assertTrue(fired)


if __name__ == "__main__":
    unittest.main()
