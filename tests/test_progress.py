"""Progress telemetry: four separate signals, and a classifier that is not allowed to kill."""
from __future__ import annotations

import os
import time
import unittest

from antopt.progress import Telemetry, classify, sample_process_tree


def sample(at: float, cpu: float, pid: int = 1) -> object:
    from antopt.progress import ResourceSample

    return ResourceSample(at=at, pid=pid, cpu_seconds=cpu, memory_mb=100.0, children=2)


class SamplerTests(unittest.TestCase):
    def test_a_real_process_yields_numbers(self):
        result = sample_process_tree(os.getpid())
        self.assertIsNotNone(result)
        self.assertGreaterEqual(result.cpu_seconds, 0.0)
        self.assertGreater(result.memory_mb, 0.0)

    def test_an_unknown_pid_is_none_not_zero(self):
        self.assertIsNone(sample_process_tree(999999))

    def test_an_invalid_pid_is_none(self):
        self.assertIsNone(sample_process_tree(-5))


class TelemetryTests(unittest.TestCase):
    def test_heartbeat_is_stamped(self):
        telemetry = Telemetry()
        telemetry.touch()
        self.assertTrue(telemetry.heartbeat_utc.endswith("Z"))
        self.assertEqual(len(telemetry.heartbeat_utc), 20)

    def test_budgets_add_per_phase_and_ignore_unknown_phases(self):
        telemetry = Telemetry()
        telemetry.add_budget("solve", 12.5)
        telemetry.add_budget("solve", 7.5)
        telemetry.add_budget("export", 2.0)
        telemetry.add_budget("nonsense", 99.0)
        self.assertEqual(telemetry.budgets, {"solve": 20.0, "export": 2.0})

    def test_resources_are_capped(self):
        telemetry = Telemetry()
        for index in range(300):
            telemetry.add_resource(sample(float(index), float(index)))
        self.assertEqual(len(telemetry.resources), 240)

    def test_the_payload_is_json_shaped(self):
        import json

        telemetry = Telemetry()
        telemetry.touch()
        telemetry.add_resource(sample(1.0, 5.0))
        telemetry.add_budget("solve", 3.0)
        json.dumps(telemetry.to_dict())


class ClassifierTests(unittest.TestCase):
    def test_no_heartbeat_is_no_signal(self):
        verdict = classify(Telemetry())
        self.assertEqual(verdict["state"], "no_signal")

    def test_cpu_growth_is_progressing(self):
        telemetry = Telemetry(heartbeat_utc="2026-11-15T00:00:00Z")
        telemetry.add_resource(sample(1.0, 10.0))
        telemetry.add_resource(sample(2.0, 30.0))
        self.assertEqual(classify(telemetry)["state"], "progressing")

    def test_flat_cpu_is_only_suspected_slow(self):
        telemetry = Telemetry(heartbeat_utc="2026-11-15T00:00:00Z")
        telemetry.add_resource(sample(1.0, 10.0))
        telemetry.add_resource(sample(120.0, 10.0))
        verdict = classify(telemetry)
        self.assertEqual(verdict["state"], "suspected_slow")
        self.assertIn("not proven", verdict["reason"])
        self.assertNotIn("kill", str(verdict).lower())

    def test_the_verdict_never_asks_for_termination(self):
        for state in ("no_signal", "progressing", "suspected_slow"):
            telemetry = Telemetry(heartbeat_utc="2026-11-15T00:00:00Z")
            if state != "no_signal":
                telemetry.add_resource(sample(1.0, 10.0))
                telemetry.add_resource(sample(2.0, 10.0 if state == "suspected_slow" else 40.0))
            verdict = classify(telemetry)
            self.assertIn("state", verdict)
            self.assertNotIn("terminate", str(verdict).lower())
            self.assertNotIn("kill", str(verdict).lower())


if __name__ == "__main__":
    unittest.main()
