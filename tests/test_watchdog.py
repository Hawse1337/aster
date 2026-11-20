"""The stall detector must separate 'slow' from 'not working at all'."""
from __future__ import annotations

import unittest

from aster.watchdog import StallDetector, watch


class StallDetectorTests(unittest.TestCase):
    def _detector(self, values, **kwargs):
        stream = iter(values)
        return StallDetector(sampler=lambda: next(stream), **kwargs)

    def test_first_sample_never_reports_a_stall(self):
        detector = self._detector([10.0, 60.0])
        self.assertFalse(detector.stalled())
        self.assertFalse(detector.stalled())

    def test_real_work_is_not_a_stall(self):
        detector = self._detector([0.0, 40.0, 80.0, 130.0])
        self.assertFalse(detector.stalled())
        self.assertFalse(detector.stalled())
        self.assertFalse(detector.stalled())
        self.assertFalse(detector.stalled())

    def test_sustained_idle_is_a_stall(self):
        detector = self._detector([100.0, 100.5, 101.0], consecutive=2)
        self.assertFalse(detector.stalled())   # baseline
        self.assertFalse(detector.stalled())   # one quiet sample
        self.assertTrue(detector.stalled())    # second quiet sample -> stall

    def test_one_quiet_sample_is_not_enough(self):
        detector = self._detector([100.0, 100.2, 140.0], consecutive=2)
        self.assertFalse(detector.stalled())
        self.assertFalse(detector.stalled())   # quiet, but only once
        self.assertFalse(detector.stalled())   # work resumed, counter resets

    def test_threshold_is_configurable(self):
        strict = self._detector([0.0, 10.0, 20.0], min_delta_s=50.0, consecutive=1)
        self.assertFalse(strict.stalled())
        self.assertTrue(strict.stalled())

    def test_reset_forgets_the_baseline(self):
        detector = self._detector([0.0])
        detector.stalled()
        detector.reset()
        detector.sampler = lambda: 0.0
        self.assertFalse(detector.stalled())


class WatchLoopTests(unittest.TestCase):
    def test_returns_true_when_the_solver_stalls(self):
        values = iter([0.0, 0.1, 0.2])
        result = watch(sampler=lambda: next(values), window_s=0.0, sleep=lambda _s: None,
                       max_checks=5)
        self.assertTrue(result)

    def test_returns_false_when_work_continues(self):
        values = iter([0.0, 60.0, 120.0, 180.0])
        result = watch(sampler=lambda: next(values), window_s=0.0, sleep=lambda _s: None,
                       max_checks=3)
        self.assertFalse(result)

    def test_reports_each_sample(self):
        seen = []
        values = iter([0.0, 0.1, 0.2])
        watch(sampler=lambda: next(values), window_s=0.0, sleep=lambda _s: None, max_checks=3,
              on_sample=lambda value, stalled: seen.append((value, stalled)))
        self.assertEqual(len(seen), 3)
        self.assertTrue(seen[-1][1])


if __name__ == "__main__":
    unittest.main()
