"""The acquisition dispatcher: auto must fall back, and it must say which backend answered."""
from __future__ import annotations

import unittest

import numpy as np

from antopt.surrogate import backends


def problem(seed: int = 0, points: int = 8) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.RandomState(seed)
    x = rng.rand(points, 3)
    y = np.column_stack([np.sum((x - 0.3) ** 2, axis=1), np.sum((x - 0.7) ** 2, axis=1)])
    return x, y


BOUNDS = [(0.0, 1.0)] * 3


class DispatchTests(unittest.TestCase):
    def test_auto_picks_botorch_when_it_is_installed(self):
        if not backends.available_methods()["qlognehvi"]:
            self.skipTest("torch/botorch are not installed")
        selection = backends.select_batch(*problem(), BOUNDS, count=2, method="auto", seed=0)
        self.assertEqual(selection.method, "qlognehvi")
        self.assertEqual(np.asarray(selection.points).shape, (2, 3))

    def test_auto_falls_back_when_torch_is_missing(self):
        original = backends.available_methods
        backends.available_methods = lambda: {"qlogehvi": False, "qlognehvi": False,
                                              "qlognparego": False, "believer": True}
        try:
            selection = backends.select_batch(*problem(), BOUNDS, count=2, method="auto")
        finally:
            backends.available_methods = original
        self.assertEqual(selection.method, "diverse_ei")
        self.assertIn("not installed", selection.note)
        self.assertEqual(np.asarray(selection.points).shape, (2, 3))

    def test_an_explicit_botorch_request_without_torch_is_an_error(self):
        original = backends.available_methods
        backends.available_methods = lambda: {"qlogehvi": False, "qlognehvi": False,
                                              "qlognparego": False, "believer": True}
        try:
            for method in ("qlognehvi", "qlogehvi", "qlognparego"):
                with self.subTest(method=method):
                    with self.assertRaises(RuntimeError):
                        backends.select_batch(*problem(), BOUNDS, count=2, method=method)
        finally:
            backends.available_methods = original

    def test_qlognparego_is_offered(self):
        self.assertIn("qlognparego", backends.METHODS)
        self.assertIn("qlognparego", backends.available_methods())

    def test_the_believer_backend_always_works(self):
        selection = backends.select_batch(*problem(), BOUNDS, count=3, method="believer")
        self.assertEqual(selection.method, "diverse_ei")
        points = np.asarray(selection.points)
        self.assertEqual(points.shape, (3, 3))
        self.assertTrue(np.all(points >= 0.0) and np.all(points <= 1.0))

    def test_a_broken_botorch_backend_degrades_to_the_believer(self):
        original = backends.available_methods
        backends.available_methods = lambda: {"qlogehvi": True, "qlognehvi": True,
                                              "qlognparego": True, "believer": True}
        try:
            import antopt.surrogate.botorch_acq as module

            original_batch = module.qlognehvi_batch
            module.qlognehvi_batch = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
            try:
                selection = backends.select_batch(*problem(), BOUNDS, count=2, method="auto")
            finally:
                module.qlognehvi_batch = original_batch
        finally:
            backends.available_methods = original
        self.assertEqual(selection.method, "diverse_ei")
        self.assertIn("qlognehvi failed", selection.note)

    def test_bad_arguments_are_rejected(self):
        x, y = problem()
        with self.assertRaises(ValueError):
            backends.select_batch(x, y[:-1], BOUNDS, count=1)
        with self.assertRaises(ValueError):
            backends.select_batch(x, y, BOUNDS[:2], count=1)
        with self.assertRaises(ValueError):
            backends.select_batch(x, y, BOUNDS, count=0)
        with self.assertRaises(ValueError):
            backends.select_batch(x, y, BOUNDS, count=1, method="magic")

    def test_the_selection_serialises_for_logs(self):
        selection = backends.select_batch(*problem(), BOUNDS, count=1, method="believer")
        payload = selection.to_dict()
        self.assertEqual(payload["method"], "diverse_ei")
        self.assertEqual(len(payload["points"]), 1)


class DirectionTests(unittest.TestCase):
    """Minimisation must actually be minimised; the sign convention is easy to invert."""

    def _problem(self):
        x = np.array([[0.90, 0.95], [0.92, 0.91], [0.95, 0.93],
                      [0.88, 0.97], [0.97, 0.90], [0.93, 0.96]])
        return x, x.copy()  # both objectives are simply x: small is good

    def test_the_backend_minimises(self):
        usable = backends.available_methods()
        methods = ["believer"] + [name for name in ("qlogehvi", "qlognehvi",
                                                      "qlognparego")
                                   if usable[name]]
        x, y = self._problem()
        for method in methods:
            with self.subTest(method=method):
                selection = backends.select_batch(x, y, [(0.0, 1.0)] * 2, count=2,
                                                  method=method, seed=1)
                points = np.asarray(selection.points)
                # every observed point is near 1.0 in both coordinates, so a proposal above the
                # observed mean in both is the acquisition chasing the wrong direction
                self.assertLess(points.mean(), x.mean(), "%s proposed worse points" % method)


if __name__ == "__main__":
    unittest.main()
