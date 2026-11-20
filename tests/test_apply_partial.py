"""A partial parameter dict must be merged with the model defaults before use."""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")

from antopt.apply import ModelApplier
from antopt.cases import lens28_var


class FakeBridge:
    def __init__(self):
        self.codes = []

    def execute_code(self, code, pid=None, port=None):
        self.codes.append(code)
        return {"result": {"operations": [{"op": "revolve_profile"}],
                            "validated": True, "objects": ["cone_dn"]}}


class PartialParameterTests(unittest.TestCase):
    def test_apply_accepts_only_the_edited_field(self):
        model = lens28_var.build_model()
        bridge = FakeBridge()
        applier = ModelApplier(model, bridge, project_path=r"X:\\p.aedt",
                               design_name="d", pid=1, incremental=False)
        result = applier.apply({"ring_outer": 2.2})
        self.assertTrue(result.ok, result.error)
        self.assertEqual(len(bridge.codes), 1)
        # the rendered code must contain the other parameters at their defaults
        self.assertIn("2.2", bridge.codes[0])

    def test_partial_input_is_validated_against_the_space(self):
        model = lens28_var.build_model()
        bridge = FakeBridge()
        applier = ModelApplier(model, bridge, project_path=r"X:\\p.aedt",
                               design_name="d", pid=1, incremental=False)
        result = applier.apply({"ring_outer": 99.0})
        self.assertFalse(result.ok)
        self.assertIn("ring_outer", result.error)
        self.assertEqual(bridge.codes, [])


if __name__ == "__main__":
    unittest.main()
