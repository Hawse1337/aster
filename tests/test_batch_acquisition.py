"""Batch acquisition: the Kriging-believer picks must not collapse onto one region."""
from __future__ import annotations

import unittest

import numpy as np

from antopt.surrogate.batch import kriging_believer
from antopt.surrogate.selection import select_candidates


def pool(n=20):
    # a simple landscape: a single optimum at index 7, rising uncertainty to the right
    mean = np.array([6.0 + 0.1 * np.sin(i) for i in range(n)])
    if n > 7:
        mean[7] = 6.6
    std = np.linspace(0.05, 0.5, n)
    return mean, std


class KrigingBelieverTests(unittest.TestCase):
    def test_first_pick_matches_plain_ei(self):
        mean, std = pool()
        batch = kriging_believer(mean, std, count=1, strategy="ei")
        plain = select_candidates(mean, std, strategy="ei", count=1)
        self.assertEqual(batch[0].index, plain[0].index)

    def test_batch_points_are_distinct(self):
        mean, std = pool()
        picks = kriging_believer(mean, std, count=5, strategy="ei", best=6.6, points=np.arange(len(mean))[:, None] / len(mean))
        indices = [pick.index for pick in picks]
        self.assertEqual(len(indices), len(set(indices)))

    def test_fantasies_push_the_batch_away_from_the_first_pick(self):
        mean = np.full(12, 6.0)
        std = np.full(12, 0.4)
        mean[3] = 6.8  # one clear optimum
        single = select_candidates(mean, std, strategy="ei", count=3, best=6.8)
        batch = kriging_believer(mean, std, count=3, strategy="ei", best=6.8, points=np.arange(len(mean))[:, None] / len(mean))
        self.assertEqual(batch[0].index, single[0].index)
        # plain top-3 by EI keeps clustering near the optimum; the believer spreads out
        self.assertGreater(len({pick.index for pick in batch}), 1)
        self.assertNotEqual(batch[1].index, batch[0].index)

    def test_ucb_and_explore_are_supported(self):
        mean, std = pool()
        for strategy in ("ucb", "explore"):
            picks = kriging_believer(mean, std, count=3, strategy=strategy, points=np.arange(len(mean))[:, None] / len(mean))
            self.assertEqual(len(picks), 3)
            self.assertEqual(len({pick.index for pick in picks}), 3)

    def test_unknown_strategy_and_bad_sizes_are_rejected(self):
        mean, std = pool(5)
        with self.assertRaises(ValueError):
            kriging_believer(mean, std, count=2, strategy="qlognehvi")
        with self.assertRaises(ValueError):
            kriging_believer(mean, std[:-1], count=2)

    def test_zero_count_returns_nothing(self):
        mean, std = pool(4)
        self.assertEqual(kriging_believer(mean, std, count=0), [])


if __name__ == "__main__":
    unittest.main()
