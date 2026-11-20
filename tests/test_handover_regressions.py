"""Offline regressions found while reviewing the November handover."""
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from antopt.runtime.runner import RealJob, RealRunManager
from antopt.stall import StallWatch, system_cpu_seconds
from antopt.touchstone import contiguous_bandwidth, read_touchstone


class TouchstoneRegressionTests(unittest.TestCase):
    def read(self, body):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.s1p"
            path.write_text(body, encoding="utf-8")
            return read_touchstone(path)

    def test_frequency_units_are_normalised_to_ghz(self):
        for unit, freq in [("Hz", "28000000000"), ("kHz", "28000000"),
                           ("MHz", "28000"), ("GHz", "28")]:
            with self.subTest(unit=unit):
                self.assertEqual(self.read(f"# {unit} S DB R 50\n{freq} -12 0\n"),
                                 [(28.0, -12.0)])

    def test_option_defaults_and_inline_comments(self):
        self.assertEqual(self.read("# ghz s db ! optional impedance\n28 -12 0 ! row\n"),
                         [(28.0, -12.0)])
        self.assertEqual(self.read("#\n28 0.1 0\n"), [(28.0, -20.0)])

    def test_nonfinite_rows_do_not_count_as_samples(self):
        self.assertEqual(self.read("# GHz S DB R 50\nnan -10 0\n28 nan 0\n"
                                   "29 -12 inf\n30 -12 0\n"), [(30.0, -12.0)])

    def test_failed_frequency_breaks_a_fine_sweep_band(self):
        points = [(24.0, -12), (24.5, -12), (25.0, -4), (25.5, -12), (26.0, -12)]
        self.assertEqual(contiguous_bandwidth(points)["value"], 0.5)

    def test_conflicting_duplicate_breaks_the_band(self):
        points = [(24, -12), (24.5, -12), (24.5, -4), (25, -12)]
        self.assertEqual(contiguous_bandwidth(points)["value"], 0.0)

    def test_unsorted_points_and_band_filter(self):
        points = [(27, -12), (25, -12), (24, -12), (26, -12)]
        self.assertEqual(contiguous_bandwidth(points, band=(25, 27))["value"], 2.0)

    def test_unknown_sample_breaks_band(self):
        self.assertEqual(contiguous_bandwidth([(24, -12), (24.5, math.nan),
                                              (25, -12)])["value"], 0.0)


class MonitorRegressionTests(unittest.TestCase):
    def test_unavailable_cpu_probe_returns_unknown(self):
        with patch.dict("sys.modules", {"psutil": None}):
            self.assertIsNone(system_cpu_seconds())

    def test_cpu_probe_failure_returns_unknown(self):
        with patch.dict("sys.modules", {"psutil": SimpleNamespace(
                cpu_times=Mock(side_effect=OSError("access denied")))}):
            self.assertIsNone(system_cpu_seconds())

    def test_invalid_observation_cannot_trigger_a_stall(self):
        for invalid in [None, math.nan, math.inf, -1]:
            with self.subTest(value=invalid):
                values = iter([100.0, invalid, 100.0, 100.0])
                ticks = iter([0.0, 61.0, 62.0, 123.0])
                watch = StallWatch(sampler=lambda: next(values), clock=lambda: next(ticks),
                                   window_s=60)
                self.assertEqual([watch.sample() for _ in range(4)],
                                 [False, False, False, True])

    def test_counter_reset_starts_new_window(self):
        values, ticks = iter([100.0, 1.0, 2.0]), iter([0.0, 61.0, 122.0])
        watch = StallWatch(sampler=lambda: next(values), clock=lambda: next(ticks), window_s=60)
        self.assertEqual([watch.sample() for _ in range(3)], [False, False, True])

    def test_sampler_exception_does_not_abort_run(self):
        watch = StallWatch(sampler=Mock(side_effect=OSError("probe unavailable")))
        self.assertFalse(watch.sample())


class ConnectionRegressionTests(unittest.TestCase):
    def check_connection_failure(self, connect):
        with tempfile.TemporaryDirectory() as tmp:
            manager = RealRunManager(campaign_dir=Path(tmp), pid_provider=lambda: 123,
                                     bridge_factory=lambda pid: SimpleNamespace(connect=connect))
            manager._prepare = Mock(side_effect=AssertionError("must not model after failure"))
            job = RealJob(run_id="connection-failure", case="lens28_var", params={})
            manager._run_job(job)
            manager._prepare.assert_not_called()
            self.assertEqual(job.status, "FAILED")
            self.assertEqual(job.error_code, "AEDT_CONNECT_FAILED")
            self.assertEqual(job.failure["title"], "AEDT 连接失败")
            self.assertIn("connection", job.error_detail.lower())

    def test_connection_exception_stops_before_modelling(self):
        self.check_connection_failure(Mock(side_effect=RuntimeError("connection refused")))

    def test_explicit_connection_failure_stops_before_modelling(self):
        self.check_connection_failure(Mock(return_value={"connected": False}))

    def test_false_connection_result_stops_before_modelling(self):
        self.check_connection_failure(Mock(return_value=False))


if __name__ == "__main__":
    unittest.main()
