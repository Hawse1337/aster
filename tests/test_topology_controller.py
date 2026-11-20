"""Module D + controller tests: operators, families, triggers, strategy rules."""
from __future__ import annotations

import os
import unittest

from aster.controller import (
    GLOBAL,
    LOCAL,
    PHYSICS,
    STOP,
    SURROGATE,
    TOPOLOGY,
    ControllerContext,
    StrategyController,
)
from aster.core import ParamSpace, ParamSpec, SpecError
from aster.topology.operators import (
    FamilyManager,
    FamilyVersion,
    StructureOperator,
    build_operator_registry,
)
from aster.topology.triggers import TriggerConfig, should_search_topology

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")


def _space() -> ParamSpace:
    return ParamSpace([ParamSpec(name="a", unit="mm", low=0.0, high=10.0, default=5.0)])


def _operator(legality=None) -> StructureOperator:
    return StructureOperator(
        operator_id="break_symmetry",
        name="break unit symmetry",
        antenna_types=("lens28", "synthetic"),
        changes="replace the symmetric unit cell with an asymmetric one",
        mechanism="suppress the open-stopband and shift the leakage distribution",
        risked_metrics=("s11",),
        extra_params=(ParamSpec(name="skew", unit="mm", low=0.0, high=2.0, default=0.5),),
        legality=legality,
    )


class OperatorTests(unittest.TestCase):
    def test_operator_input_validation(self):
        operator = _operator()
        clean = operator.validate_inputs({"skew": 1.0})
        self.assertEqual(clean, {"skew": 1.0})
        with self.assertRaises(SpecError):
            operator.validate_inputs({})
        with self.assertRaises(SpecError):
            operator.validate_inputs({"skew": 9.0})

    def test_legality_hook_rejects(self):
        operator = _operator(legality=lambda values: (values["skew"] < 1.0, "skew too large"))
        with self.assertRaises(SpecError):
            operator.validate_inputs({"skew": 1.5})

    def test_registry_rejects_duplicates(self):
        with self.assertRaises(SpecError):
            build_operator_registry([_operator(), _operator()])


class FamilyTests(unittest.TestCase):
    def _manager(self) -> FamilyManager:
        manager = FamilyManager()
        manager.add_root(FamilyVersion(family_id="lens28_sym", antenna_type="lens28",
                                       parent=None, operator=None, param_space=_space()))
        return manager

    def test_fork_inherits_and_extends_parameters(self):
        manager = self._manager()
        version = manager.fork("lens28_sym", _operator(), {"skew": 1.0},
                               new_id="lens28_asym", description="asymmetric units")
        names = [spec.name for spec in version.param_space]
        self.assertIn("a", names)
        self.assertIn("skew", names)
        self.assertEqual(version.parent, "lens28_sym")
        self.assertEqual(version.operator, "break_symmetry")

    def test_lineage_and_rollback(self):
        manager = self._manager()
        manager.fork("lens28_sym", _operator(), {"skew": 0.5}, new_id="lens28_asym")
        self.assertEqual(manager.lineage("lens28_asym"), ["lens28_sym", "lens28_asym"])
        self.assertEqual(manager.rollback_target("lens28_asym"), "lens28_sym")

    def test_unknown_parent_and_duplicate_family_rejected(self):
        manager = self._manager()
        with self.assertRaises(SpecError):
            manager.fork("nope", _operator(), {"skew": 0.5}, new_id="x")
        manager.fork("lens28_sym", _operator(), {"skew": 0.5}, new_id="lens28_asym")
        with self.assertRaises(SpecError):
            manager.fork("lens28_sym", _operator(), {"skew": 0.5}, new_id="lens28_asym")

    def test_operator_type_mismatch_rejected(self):
        manager = FamilyManager()
        manager.add_root(FamilyVersion(family_id="lwa", antenna_type="leaky_wave",
                                       parent=None, operator=None, param_space=_space()))
        with self.assertRaises(SpecError):
            manager.fork("lwa", _operator(), {"skew": 0.5}, new_id="lwa_asym")

    def test_summary(self):
        manager = self._manager()
        manager.fork("lens28_sym", _operator(), {"skew": 0.5}, new_id="lens28_asym")
        summary = manager.summary()
        self.assertEqual(summary["families"], ["lens28_asym", "lens28_sym"])


class TriggerTests(unittest.TestCase):
    def test_declines_without_evidence(self):
        decision = should_search_topology(
            history=[{"feasible": 1}, {"feasible": 2}], hypervolumes=[1.0, 1.5],
            constraint_violation_ratios={"s11": 0.2}, sensitivity_fixable=True,
            budget_left=20,
        )
        self.assertFalse(decision["decision"])
        self.assertEqual(decision["reasons"], [])

    def test_triggers_on_stall_plus_persistent_violation(self):
        decision = should_search_topology(
            history=[{"feasible": 0}, {"feasible": 0}, {"feasible": 0}],
            hypervolumes=[0.0, 0.0],
            constraint_violation_ratios={"s11": 0.95},
            sensitivity_fixable=False,
            budget_left=20,
        )
        self.assertTrue(decision["decision"])
        self.assertGreaterEqual(len(decision["reasons"]), 2)
        self.assertEqual(decision["persistent_constraints"], ["s11"])

    def test_budget_guard_blocks_topology(self):
        decision = should_search_topology(
            history=[{"feasible": 0}, {"feasible": 0}, {"feasible": 0}],
            hypervolumes=[0.0, 0.0], constraint_violation_ratios={"s11": 0.95},
            sensitivity_fixable=False, budget_left=1,
        )
        self.assertFalse(decision["decision"])

    def test_custom_config(self):
        config = TriggerConfig(stall_rounds=2, violation_ratio=0.5)
        decision = should_search_topology(
            history=[{"feasible": 0}, {"feasible": 0}], hypervolumes=[],
            constraint_violation_ratios={"s11": 0.6}, sensitivity_fixable=None,
            budget_left=10, config=config,
        )
        self.assertTrue(decision["decision"])


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.controller = StrategyController()

    def test_stop_when_budget_exhausted(self):
        decision = self.controller.select(ControllerContext(budget_left=0))
        self.assertEqual(decision.strategy, STOP)

    def test_all_satisfied_prefers_surrogate_when_usable(self):
        decision = self.controller.select(ControllerContext(surrogate_usable=True, budget_left=10))
        self.assertEqual(decision.strategy, SURROGATE)
        decision = self.controller.select(ControllerContext(budget_left=10))
        self.assertEqual(decision.strategy, LOCAL)

    def test_missing_sensitivity_goes_physics(self):
        decision = self.controller.select(
            ControllerContext(failing_constraints=["gain_h"], budget_left=10)
        )
        self.assertEqual(decision.strategy, PHYSICS)

    def test_conflict_goes_global(self):
        decision = self.controller.select(
            ControllerContext(failing_constraints=["gain_h", "s11"], has_sensitivity=True,
                              sensitivity_fixable=True, budget_left=10)
        )
        self.assertEqual(decision.strategy, GLOBAL)

    def test_stall_with_sensitivity_goes_physics(self):
        decision = self.controller.select(
            ControllerContext(failing_constraints=["gain_h"], has_sensitivity=True,
                              sensitivity_fixable=True, stalled_rounds=4, budget_left=10)
        )
        self.assertEqual(decision.strategy, PHYSICS)

    def test_single_failure_goes_local(self):
        decision = self.controller.select(
            ControllerContext(failing_constraints=["gain_h"], passing_constraints=["s11"],
                              has_sensitivity=True, sensitivity_fixable=True, budget_left=10)
        )
        self.assertEqual(decision.strategy, LOCAL)
        self.assertIn("protect", " ".join(decision.reasons))

    def test_topology_decision_and_log(self):
        decision = self.controller.select(
            ControllerContext(failing_constraints=["gain_h"], topology_worth_trying=True,
                              families=2, budget_left=10)
        )
        self.assertEqual(decision.strategy, TOPOLOGY)
        self.assertEqual(len(self.controller.log), 1)
        self.assertEqual(self.controller.log[0]["decision"]["strategy"], TOPOLOGY)

    def test_unfixable_sensitivity_goes_topology(self):
        decision = self.controller.select(
            ControllerContext(failing_constraints=["gain_h"], has_sensitivity=True,
                              sensitivity_fixable=False, budget_left=10)
        )
        self.assertEqual(decision.strategy, TOPOLOGY)


if __name__ == "__main__":
    unittest.main()

class FineScanRuleTests(unittest.TestCase):
    """A sharp optimum must trigger deterministic refinement, not surrogate EI."""

    def _controller(self):
        from aster.controller import StrategyController
        return StrategyController()

    def test_spiky_neighbourhood_selects_fine_scan(self):
        from aster.controller import ControllerContext, FINE_SCAN
        decision = self._controller().select(ControllerContext(
            passing_constraints=["peak_gain", "ripple", "bandwidth"],
            neighbourhood_probes=6, neighbourhood_feasible=2,
            refinement_step=0.05,
        ))
        self.assertEqual(decision.strategy, FINE_SCAN)
        self.assertIn("sharp optimum", " ".join(decision.reasons))
        self.assertEqual(decision.details["refinement_step"], 0.05)

    def test_healthy_neighbourhood_keeps_surrogate(self):
        from aster.controller import ControllerContext, SURROGATE
        decision = self._controller().select(ControllerContext(
            passing_constraints=["peak_gain"],
            surrogate_usable=True,
            neighbourhood_probes=6, neighbourhood_feasible=5,
        ))
        self.assertEqual(decision.strategy, SURROGATE)

    def test_too_few_probes_is_not_evidence(self):
        from aster.controller import ControllerContext, SURROGATE
        decision = self._controller().select(ControllerContext(
            passing_constraints=["peak_gain"],
            surrogate_usable=True,
            neighbourhood_probes=2, neighbourhood_feasible=0,
        ))
        self.assertEqual(decision.strategy, SURROGATE)
