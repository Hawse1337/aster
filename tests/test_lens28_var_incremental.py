"""Incremental updates must rebuild only the bodies whose parameters changed."""
from __future__ import annotations

import os
import unittest
from collections import Counter

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")

from antopt.cases import lens28_var


class IncrementalTests(unittest.TestCase):
    BASE = dict(lens28_var.build_space().default(), ring_outer=2.2)

    def _update(self, **changes):
        model = lens28_var.build_model()
        model.build(dict(self.BASE))
        return model.update(dict(self.BASE, **changes))

    def test_disc_only_change_rebuilds_one_body(self):
        counts = Counter(op["op"] for op in self._update(disc_factor=1.02))
        self.assertEqual(counts["revolve_profile"], 1)
        self.assertEqual(counts["create_cylinder"], 0)
        self.assertEqual(counts["subtract"], 0)
        self.assertEqual(counts["validate_simple"], 1)

    def test_bowl_change_rebuilds_both_bowls_and_the_holes(self):
        counts = Counter(op["op"] for op in self._update(tip_offset=1.05))
        self.assertEqual(counts["revolve_profile"], 2)
        self.assertEqual(counts["create_cylinder"], 36)
        self.assertEqual(counts["subtract"], 2)

    def test_ring_change_counts_as_a_bowl_change(self):
        counts = Counter(op["op"] for op in self._update(ring_outer=2.3))
        self.assertEqual(counts["revolve_profile"], 2)
        self.assertEqual(counts["create_cylinder"], 36)

    def test_unchanged_parameters_emit_nothing(self):
        self.assertEqual(self._update(), [])

    def test_disc_and_bowl_change_together_rebuild_everything(self):
        counts = Counter(op["op"] for op in self._update(disc_factor=1.02, tip_offset=1.05))
        self.assertEqual(counts["revolve_profile"], 3)


if __name__ == "__main__":
    unittest.main()
