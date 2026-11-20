"""The step-ring operation consumes the lower cone, so one design can only be
parameterised once; this file pins that known limitation and the mitigation.

History
-------
* 2026-11-12: uniting ``coax_outer``, the ring and ``cone_dn`` absorbed ``cone_dn``;
  the next apply failed with ``'NoneType' object has no attribute 'bounding_box'``.
* Fix attempt: subtract the ring volume from the hosts. The first apply produced a
  clean 9-object design (validation passed), but the second apply failed inside AEDT
  with ``Failed to execute gRPC AEDT command: Subtract`` because the notch already
  existed and there was nothing left to subtract.
* Mitigation kept: uniting (which preserves the measured geometry) plus a pristine
  project copy per run in the GUI. The in-place rework - recreate ``cone_dn`` from
  stored dimensions before the unite - is tracked in docs/live_runs.md.
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")

from antopt.cases import lens28_ref
from antopt.parametric.aedt import render


class StepRingTests(unittest.TestCase):
    def _code(self) -> str:
        model = lens28_ref.build_model()
        params = model.space.default()
        params["ring_outer"] = 2.2
        return render(model.build(params), project_path=r"X:\p.aedt",
                      design_name="f24g05", port=1)

    def test_ring_is_created_and_united(self):
        code = self._code()
        self.assertIn("app.modeler.subtract('antopt_ring_out', 'antopt_ring_in'", code)
        self.assertIn("app.modeler.unite(['coax_outer', 'antopt_ring_out', 'cone_dn'])",
                      code)

    def test_unite_still_names_the_lower_cone(self):
        # the unite must keep targeting cone_dn: dropping it would leave the ring
        # overlapping the bowl and HFSS rejects overlapping PEC
        code = self._code()
        self.assertIn("'cone_dn'", code.split("app.modeler.unite(")[-1])

    def test_ring_arguments_are_still_validated(self):
        from antopt.parametric.aedt import OperationError, validate_operations

        with self.assertRaises(OperationError):
            validate_operations([{"op": "add_step_ring",
                                  "args": {"inner_radius": 1.2, "outer_radius": 1.0,
                                           "height": 0.6}}])


if __name__ == "__main__":
    unittest.main()
