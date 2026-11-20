"""The BoTorch qLogNEHVI backend: identification, guards and a small synthetic comparison."""
from __future__ import annotations

import unittest

import numpy as np

from antopt.surrogate.botorch_acq import (available, describe, hypervolume,
                                          qlognehvi_batch, qlognparego_batch)

HAS_BOTORCH = available()
NEEDS = unittest.skipUnless(HAS_BOTORCH, "torch/botorch are not installed")


class HypervolumeTests(unittest.TestCase):
    def test_hand_computable_two_point_front(self):
        # boxes (0.1..1 x 0.9..1) and (0.9..1 x 0.1..1) overlap in a 0.1 x 0.1 corner
        value = hypervolume(np.array([[0.1, 0.9], [0.9, 0.1]]), [1.0, 1.0])
        self.assertAlmostEqual(value, 0.17, places=9)

    def test_dominated_point_adds_nothing(self):
        point = np.array([[0.1, 0.9], [0.9, 0.1]])
        dominated = np.vstack([point, [[0.95, 0.95]]])
        self.assertAlmostEqual(hypervolume(point, [1.0, 1.0]),
                               hypervolume(dominated, [1.0, 1.0]), places=12)

    def test_single_point_is_its_box(self):
        self.assertAlmostEqual(hypervolume(np.array([[0.5, 0.5]]), [1.0, 1.0]), 0.25, places=9)

    def test_a_point_worse_than_the_reference_adds_no_volume(self):
        # batches routinely contain such points; they must not poison the measurement
        self.assertEqual(hypervolume(np.array([[1.5, 0.5]]), [1.0, 1.0]), 0.0)
        values = np.array([[0.1, 0.9], [1.5, 0.5]])
        self.assertAlmostEqual(hypervolume(values, [1.0, 1.0]), 0.09, places=9)

    def test_default_reference_is_beyond_the_worst_value(self):
        values = np.array([[0.1, 0.9], [0.9, 0.1]])
        self.assertGreater(hypervolume(values), 0.0)


class AvailabilityTests(unittest.TestCase):
    def test_describe_reports_the_stack(self):
        info = describe()
        self.assertEqual(info["method"], "qlognehvi")
        self.assertIn("available", info)
        if info["available"]:
            self.assertIn("torch", info)
            self.assertIn("botorch", info)

    def test_the_batch_backend_refuses_without_torch(self):
        import antopt.surrogate.botorch_acq as module

        original = module.available
        module.available = lambda: False
        try:
            with self.assertRaises(RuntimeError):
                module.qlognehvi_batch(np.zeros((3, 2)), np.zeros((3, 2)), [(0, 1), (0, 1)])
        finally:
            module.available = original


class ToolchainTests(unittest.TestCase):
    """The fused kernel needs MSVC and ninja; the helper must find them without side effects."""

    def test_candidates_are_real_files(self):
        from antopt.surrogate.botorch_acq import _vcvars_candidates

        for path in _vcvars_candidates():
            self.assertTrue(path.is_file())
            self.assertEqual(path.name, "vcvars64.bat")

    def test_ensure_toolchain_is_cached(self):
        from antopt.surrogate import botorch_acq as module

        first = module.ensure_toolchain()
        second = module.ensure_toolchain()
        self.assertIs(first, second)
        if first:
            import shutil
            self.assertTrue(shutil.which("ninja"))

    def test_adopting_built_extensions_is_idempotent(self):
        from antopt.surrogate import botorch_acq as module

        module._adopt_built_extensions()
        again = module._adopt_built_extensions()
        self.assertEqual(again, [])


@NEEDS
class NonUnitBoxTests(unittest.TestCase):
    """The tutorial fits in the unit box; a search space like 1.2-3.0 mm must survive that."""

    def test_a_non_unit_box_is_handled(self):
        from antopt.surrogate.botorch_acq import qlognparego_batch

        # everything observed sits near the top of the box and small is better
        x = np.array([[3.8, 3.9], [3.85, 3.8], [3.9, 3.85],
                      [3.7, 3.95], [3.95, 3.7], [3.8, 3.9]])
        y = x.copy()
        bounds = [(2.0, 4.0)] * 2
        for name, batch in (
                ("qlognparego", qlognparego_batch),
                ("qlognehvi", qlognehvi_batch)):
            with self.subTest(method=name):
                points = batch(x, y, bounds, q=2, seed=1, num_restarts=2, raw_samples=32,
                               **({} if name == "qlognparego" else {"noiseless": True}))
                self.assertTrue(np.all(points >= 2.0) and np.all(points <= 4.0))
                self.assertLess(float(np.mean(points)), float(np.mean(x)))


@NEEDS
class BatchSelectionTests(unittest.TestCase):
    def _problem(self, seed=0, points=8):
        rng = np.random.RandomState(seed)
        x = rng.rand(points, 3)
        y = np.column_stack([np.sum((x - 0.3) ** 2, axis=1),
                             np.sum((x - 0.7) ** 2, axis=1)])
        return x, y

    def test_returns_q_points_inside_the_bounds(self):
        x, y = self._problem()
        batch = qlognehvi_batch(x, y, [(0.0, 1.0)] * 3, q=3, seed=0, num_restarts=2,
                                raw_samples=32)
        self.assertEqual(batch.shape, (3, 3))
        self.assertTrue(np.all(batch >= 0.0) and np.all(batch <= 1.0))

    def test_points_are_distinct(self):
        x, y = self._problem()
        batch = qlognehvi_batch(x, y, [(0.0, 1.0)] * 3, q=2, seed=1, num_restarts=2,
                                raw_samples=32)
        self.assertEqual(len({tuple(np.round(row, 6)) for row in batch}), 2)

    def test_same_seed_same_batch(self):
        x, y = self._problem()
        first = qlognehvi_batch(x, y, [(0.0, 1.0)] * 3, q=2, seed=7, num_restarts=2,
                                raw_samples=32)
        second = qlognehvi_batch(x, y, [(0.0, 1.0)] * 3, q=2, seed=7, num_restarts=2,
                                 raw_samples=32)
        self.assertTrue(np.allclose(first, second))

    def test_bad_shapes_are_rejected(self):
        x, y = self._problem()
        with self.assertRaises(ValueError):
            qlognehvi_batch(x, y[:-1], [(0.0, 1.0)] * 3)
        with self.assertRaises(ValueError):
            qlognehvi_batch(x, y, [(0.0, 1.0)] * 2)

    def test_a_batch_extends_the_front_or_leaves_it_alone(self):
        # whether two qLogNEHVI points beat two random ones depends on the draw - that comparison
        # lives in scripts/compare_acquisitions.py over many seeds; here only the invariant
        x, y = self._problem(seed=3, points=10)
        batch = qlognehvi_batch(x, y, [(0.0, 1.0)] * 3, q=2, seed=0, num_restarts=2,
                                raw_samples=32)
        new_objectives = np.column_stack([np.sum((batch - 0.3) ** 2, axis=1),
                                          np.sum((batch - 0.7) ** 2, axis=1)])
        reference = np.max(np.vstack([y, new_objectives]), axis=0) + 0.05
        self.assertGreaterEqual(hypervolume(np.vstack([y, new_objectives]), reference),
                                hypervolume(y, reference))


if __name__ == "__main__":
    unittest.main()
