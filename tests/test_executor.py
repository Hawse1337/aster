"""GuardExecutor tests with a fake bridge (decoupled from the guard package)."""
from __future__ import annotations

import os
import unittest

from aster.cases.lens28 import build_case
from aster.executor import GuardConfig, GuardExecutor

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")

LENS_PARAMS = {"step_L": 2.0, "step_ro": 0.8, "wedge": 1, "cone_dz": 0.0}


class FakeBridge:
    def __init__(self, metrics=None, run_status="COMPLETED", cached=None):
        self.run_status = run_status
        self.metrics = metrics if metrics is not None else {"s11": {"value": -14.0, "passed": 1},
                                   "peak_gain": {"value": 6.2, "passed": 1},
                                   "ripple": {"value": 1.2, "passed": 1}}
        self.cached = cached
        self.submissions = []
        self.specs = []

    def create_spec(self, campaign_dir, spec):
        self.specs.append((campaign_dir, spec))
        return {"spec_path": "spec.json"}

    def submit(self, **kwargs):
        self.submissions.append(kwargs)
        if self.cached:
            return {"run_id": self.cached["run_id"], "status": "COMPLETED",
                    "cached": True, "metrics": self.cached["metrics"]}
        return {"run_id": "run-1", "status": "QUEUED"}

    def status(self, campaign_dir, run_id):
        return {"run_id": run_id, "status": self.run_status,
                "metrics": self.metrics, "terminal": True}

    def archive(self, campaign_dir):
        return {"best": None, "pareto_front": [], "ranked": []}


def _case():
    return build_case()


class GuardExecutorTests(unittest.TestCase):
    def test_evaluate_maps_metrics_by_kind(self):
        case = _case()
        bridge = FakeBridge()
        config = GuardConfig(campaign_dir="camp", project_name="lens",
                             design_name="Lens", pid=1, poll_interval_s=0.01,
                             timeout_s=1.0)
        executor = GuardExecutor(case, config, bridge=bridge)
        candidate = case.make_candidate(LENS_PARAMS)
        entry = executor.evaluate(candidate)
        self.assertEqual(entry.status, "COMPLETED")
        self.assertTrue(entry.feasible, entry.violations)
        self.assertAlmostEqual(entry.metrics["s11"], -14.0)
        self.assertAlmostEqual(entry.metrics["peak_gain"], 6.2)
        self.assertEqual(entry.run_id, "run-1")
        self.assertEqual(len(bridge.submissions), 1)
        self.assertEqual(bridge.submissions[0]["params"], LENS_PARAMS)

    def test_failed_run_is_infeasible(self):
        case = _case()
        bridge = FakeBridge(run_status="FAILED", metrics={})
        config = GuardConfig(campaign_dir="camp", project_name="lens",
                             design_name="Lens", pid=1, poll_interval_s=0.01,
                             timeout_s=1.0)
        entry = GuardExecutor(case, config, bridge=bridge).evaluate(
            case.make_candidate(LENS_PARAMS)
        )
        self.assertEqual(entry.status, "FAILED")
        self.assertFalse(entry.feasible)
        self.assertGreater(entry.total_violation, 0)

    def test_cached_result_is_reused_without_waiting(self):
        case = _case()
        bridge = FakeBridge(cached={"run_id": "run-old",
                                    "metrics": {"s11": -15.0, "peak_gain": -1.0}})
        config = GuardConfig(campaign_dir="camp", project_name="lens",
                             design_name="Lens", pid=1, poll_interval_s=0.01,
                             timeout_s=1.0)
        entry = GuardExecutor(case, config, bridge=bridge).evaluate(
            case.make_candidate(dict(LENS_PARAMS, step_L=3.0))
        )
        self.assertEqual(entry.run_id, "run-old")
        self.assertFalse(entry.feasible)  # gain below target
        self.assertIn("peak_gain", entry.violations)

    def test_config_requires_explicit_target(self):
        case = _case()
        config = GuardConfig(campaign_dir="camp", project_name="lens",
                             design_name="Lens")
        with self.assertRaises(ValueError):
            GuardExecutor(case, config, bridge=FakeBridge())


if __name__ == "__main__":
    unittest.main()




class GuardConfigExtraSetupTests(unittest.TestCase):
    def test_farfield_and_extra_setups_are_submitted(self):
        case = build_case()
        bridge = FakeBridge()
        config = GuardConfig(campaign_dir="camp", project_name="lens", design_name="Lens",
                             pid=1, poll_interval_s=0.01, timeout_s=1.0,
                             farfield_setup_name="FF_Swp", extra_setups=["FF_Swp"])
        GuardExecutor(case, config, bridge=bridge).evaluate(case.make_candidate(LENS_PARAMS))
        submitted = bridge.submissions[0]
        self.assertEqual(submitted["farfield_setup_name"], "FF_Swp")
        self.assertEqual(submitted["extra_setups"], ["FF_Swp"])




class RecommendedPassesTests(unittest.TestCase):
    """Pass budget grows with the size of the geometry change (measured rule)."""

    BASE = {"ring_outer": 2.2, "disc_factor": 1.0, "tip_offset": 1.0,
            "feed_radius": 0.4, "ring2_outer": 0.0, "ring_height": 0.6}

    def _passes(self, **changes):
        from aster.executor import recommended_passes
        params = dict(self.BASE)
        params.update(changes)
        return recommended_passes(params, self.BASE)

    def test_knob_tweaks_need_eight_passes(self):
        self.assertEqual(self._passes(ring_outer=2.3), 8)
        self.assertEqual(self._passes(tip_offset=1.1), 8)
        self.assertEqual(self._passes(), 8)

    def test_structural_changes_need_twelve_passes(self):
        self.assertEqual(self._passes(disc_factor=1.1), 12)
        self.assertEqual(self._passes(ring2_outer=3.4), 12)
        self.assertEqual(self._passes(feed_radius=0.6), 12)
        self.assertEqual(self._passes(ring_shift=-0.3), 12)
