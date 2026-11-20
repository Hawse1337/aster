"""ModelApplier tests with a recording bridge (no AEDT)."""
from __future__ import annotations

import os
import unittest

from aster.apply import ModelApplier
from aster.cases import lens28_builder

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")

PARAMS = {"step_L": 2.0, "step_ro": 0.8, "wedge": 1, "cone_dz": 0.0}


class RecordingBridge:
    def __init__(self, result=None):
        self.codes = []
        self.result = result or {"validated": True, "objects": ["disc", "step_out"],
                                 "operations": [{"op": "validate_simple", "validated": True}]}
        self.stdout = "ok"

    def execute_code(self, code, *, pid=None, port=None):
        self.codes.append(code)
        return {"executed": True, "result": self.result, "stdout": self.stdout, "stderr": ""}


class ModelApplierTests(unittest.TestCase):
    def _applier(self, bridge, pid=1, port=None):
        model = lens28_builder.build_model()
        return ModelApplier(model, bridge, project_path=r"D:\sandbox\b0_copy.aedt",
                            design_name="B0_f24_thin", pid=pid, port=port)

    def test_apply_renders_and_sends_one_snippet(self):
        bridge = RecordingBridge()
        result = self._applier(bridge).apply(PARAMS)
        self.assertTrue(result.ok)
        self.assertTrue(result.validated)
        self.assertIn("disc", result.objects)
        self.assertEqual(len(bridge.codes), 1)
        self.assertIn("B0_f24_thin", bridge.codes[0])
        self.assertNotIn("Analyze", bridge.codes[0])

    def test_invalid_candidate_never_reaches_aedt(self):
        bridge = RecordingBridge()
        applier = self._applier(bridge)
        result = applier.apply({"step_L": 2.0, "step_ro": 1.19, "wedge": 1, "cone_dz": 0.0})
        self.assertFalse(result.ok)
        # the space rejects the value before the geometry check can complain about step_gap
        self.assertIn("step_ro", result.error)
        self.assertEqual(bridge.codes, [])

    def test_same_parameters_twice_only_applies_once(self):
        bridge = RecordingBridge()
        applier = self._applier(bridge)
        applier.apply(PARAMS)
        applier.apply(PARAMS)
        self.assertEqual(len(bridge.codes), 1)

    def test_apply_requires_explicit_target(self):
        bridge = RecordingBridge()
        with self.assertRaises(ValueError):
            ModelApplier(lens28_builder.build_model(), bridge,
                         project_path="x", design_name="y")

    def test_unexpected_payload_is_reported_as_error(self):
        class BadBridge(RecordingBridge):
            def execute_code(self, code, *, pid=None, port=None):
                return {"executed": True, "result": "not-a-dict"}

        result = self._applier(BadBridge()).apply(PARAMS)
        self.assertFalse(result.ok)
        self.assertIn("unexpected execute_code payload", result.error)


if __name__ == "__main__":
    unittest.main()
