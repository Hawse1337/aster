"""In-place parameter updates must not repeat the destructive ring union.

``add_step_ring`` unites the ring into the lower cone, which absorbs that cone and
makes a second apply fail. ``update()`` therefore drops the ring operation when every
ring parameter is unchanged, so non-ring tweaks can be applied repeatedly on one design.
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")

from aster.cases import lens28_ref


class IncrementalUpdateTests(unittest.TestCase):
    def setUp(self):
        self.model = lens28_ref.build_model()
        self.base = self.model.space.default()
        self.base["ring_outer"] = 2.2
        self.model.build(dict(self.base))

    def test_ring_operation_is_dropped_when_ring_is_unchanged(self):
        ops = self.model.update(dict(self.base, tip_offset=1.1))
        names = [op["op"] for op in ops]
        self.assertNotIn("add_step_ring", names)
        self.assertIn("gap_bowls", names)
        self.assertIn("validate_simple", names)

    def test_ring_operation_returns_when_the_ring_changes(self):
        for change in ({"ring_outer": 2.3}, {"ring_height": 0.7},
                       {"ring_shift": -0.1}, {"ring2_outer": 3.4}):
            model = lens28_ref.build_model()
            model.build(dict(self.base))
            ops = model.update(dict(self.base, **change))
            names = [op["op"] for op in ops]
            self.assertIn("add_step_ring", names, change)

    def test_unchanged_parameters_return_no_operations(self):
        self.assertEqual(self.model.update(dict(self.base)), [])

    def test_repeated_non_ring_updates_never_emit_the_union(self):
        for tip in (1.02, 1.04, 1.06, 1.08):
            ops = self.model.update(dict(self.base, tip_offset=tip))
            self.assertNotIn("add_step_ring", [op["op"] for op in ops])


if __name__ == "__main__":
    unittest.main()
