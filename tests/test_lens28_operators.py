"""Structure-operator tests for the lens28 plugin (offline)."""
from __future__ import annotations

import os
import unittest

from antopt.cases.lens28 import build_case
from antopt.cases.lens28_operators import aperture_operator, operator_operations
from antopt.core import SpecError
from antopt.topology.operators import FamilyManager, FamilyVersion

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")


class ApertureOperatorTests(unittest.TestCase):
    def test_operator_applies_to_the_lens_type(self):
        operator = aperture_operator()
        self.assertTrue(operator.applies_to("metal_lens_periodic_disc"))
        self.assertFalse(operator.applies_to("leaky_wave"))

    def test_inputs_are_validated(self):
        operator = aperture_operator()
        clean = operator.validate_inputs({"aperture_scale": 1.05, "aperture_z": 0.5})
        self.assertEqual(clean["aperture_scale"], 1.05)
        with self.assertRaises(SpecError):
            operator.validate_inputs({"aperture_scale": 1.5, "aperture_z": 0.0})
        with self.assertRaises(SpecError):
            operator.validate_inputs({"aperture_scale": 1.0})

    def test_operations_are_declarative_and_bounded(self):
        operations = operator_operations("aperture_scale",
                                         {"aperture_scale": 1.08, "aperture_z": -0.4})
        names = [op["op"] for op in operations]
        self.assertEqual(names[0], "scale_objects")
        self.assertEqual(names[-1], "validate_simple")
        self.assertEqual(operations[0]["args"]["factor"], 1.08)
        self.assertAlmostEqual(operations[1]["args"]["delta"][2], -0.4)
        with self.assertRaises(SpecError):
            operator_operations("unknown", {})

    def test_fork_creates_a_child_family_with_lineage(self):
        case = build_case()
        manager = FamilyManager()
        manager.add_root(FamilyVersion(family_id="lens28_disc_v1",
                                       antenna_type="metal_lens_periodic_disc",
                                       parent=None, operator=None,
                                       param_space=case.family.param_space))
        child = manager.fork("lens28_disc_v1", aperture_operator(),
                             {"aperture_scale": 1.06, "aperture_z": 0.0},
                             new_id="lens28_aperture_v2")
        names = [spec.name for spec in child.param_space]
        self.assertIn("step_L", names)
        self.assertIn("aperture_scale", names)
        self.assertEqual(child.operator, "aperture_scale")
        self.assertEqual(manager.lineage("lens28_aperture_v2"),
                         ["lens28_disc_v1", "lens28_aperture_v2"])
        self.assertEqual(manager.rollback_target("lens28_aperture_v2"), "lens28_disc_v1")

    def test_risked_metrics_are_declared(self):
        operator = aperture_operator()
        self.assertIn("s11", operator.risked_metrics)
        self.assertTrue(operator.rollback)


if __name__ == "__main__":
    unittest.main()
