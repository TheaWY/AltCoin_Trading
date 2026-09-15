"""Generator must stream the hypothesis space, not materialize it.

research_space.yaml is ~10^9 combos. Listing them OOMs nightly research
(previously SIGKILL 137). These tests lock the streaming / round-robin
contract on a tiny space.
"""

from __future__ import annotations

import unittest

from src.research.generator import (
    _iter_combos,
    _ordered_candidates,
    _space_size,
)
from src.research.promotion import config_hash


class SpaceSize(unittest.TestCase):
    def test_product_of_axis_lengths(self):
        self.assertEqual(_space_size({"A": [1, 2], "B": [3, 4, 5]}), 6)

    def test_billion_scale_does_not_materialize(self):
        axes = {"A": list(range(10_000)), "B": list(range(10_000)), "C": list(range(10))}
        self.assertEqual(_space_size(axes), 1_000_000_000)
        n = 0
        for _ in _iter_combos(axes):
            n += 1
            if n >= 5:
                break
        self.assertEqual(n, 5)


class OrderedCandidates(unittest.TestCase):
    def test_round_robin_strategies_and_skips_existing(self):
        axes = {
            "ACTIVE_STRATEGY": ["rel_strength_rotation", "momentum", "volume_spike"],
            "ALLOW_LONG": [True, False],
            "MIN_CONFIDENCE": [0.70, 0.75],
        }
        families = [
            {"name": "rs_long", "match": {"ACTIVE_STRATEGY": "rel_strength_rotation", "ALLOW_LONG": True}},
        ]
        champ = {"ACTIVE_STRATEGY": "momentum", "ALLOW_LONG": True, "MIN_CONFIDENCE": 0.70}
        champ_h = config_hash(champ)
        existing = {config_hash({"ACTIVE_STRATEGY": "volume_spike", "ALLOW_LONG": True, "MIN_CONFIDENCE": 0.70})}
        got = _ordered_candidates(axes, families, existing, champ_h, limit=4)
        self.assertEqual(len(got), 4)
        strategies = [c["ACTIVE_STRATEGY"] for c in got]
        self.assertEqual(strategies[0], "rel_strength_rotation")
        self.assertTrue(got[0]["ALLOW_LONG"])
        self.assertNotIn("volume_spike", strategies[:1])
        self.assertEqual(len({config_hash(c) for c in got}), 4)
        self.assertNotIn(champ_h, {config_hash(c) for c in got})

    def test_limit_zero_is_empty(self):
        self.assertEqual(_ordered_candidates({"A": [1]}, [], set(), "", 0), [])


if __name__ == "__main__":
    unittest.main()
