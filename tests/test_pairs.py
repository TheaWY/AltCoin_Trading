"""Unit tests for the pairs stat-arb shared core (src/engine/pairs.py).

Locks the entry/exit rule, spread math, half-life, PIT universe, and cost model
that BOTH the backtest driver and the live runner depend on. If these drift,
backtest and live diverge silently — exactly what the one-code-path rule forbids.
"""

from __future__ import annotations

import unittest

import numpy as np

from src.engine import pairs


class SpreadAndHalfLife(unittest.TestCase):
    def test_spread_is_log_a_minus_beta_log_b(self):
        la = np.array([1.0, 2.0, 3.0])
        lb = np.array([0.5, 1.0, 1.5])
        np.testing.assert_allclose(pairs.spread(la, lb, 2.0), la - 2.0 * lb)

    def test_half_life_of_mean_reverting_series_is_finite_positive(self):
        rng = np.random.default_rng(0)
        x = np.zeros(2000)
        for i in range(1, len(x)):
            x[i] = 0.95 * x[i - 1] + rng.normal(0, 0.1)
        hl = pairs.half_life(x)
        self.assertTrue(np.isfinite(hl) and hl > 0)

    def test_half_life_random_walk_much_slower_than_reverter(self):
        # A finite random-walk sample can show spurious weak reversion, so
        # half-life need not be exactly inf -- but it must be far slower than a
        # genuine fast mean-reverter (the property selection actually relies on).
        rng = np.random.default_rng(1)
        rw = np.cumsum(rng.normal(0, 1, 3000))
        ou = np.zeros(3000)
        for i in range(1, len(ou)):
            ou[i] = 0.9 * ou[i - 1] + rng.normal(0, 0.1)
        self.assertGreater(pairs.half_life(rw), 5 * pairs.half_life(ou))

    def test_half_life_too_little_data_is_inf(self):
        self.assertEqual(pairs.half_life(np.arange(10.0)), float("inf"))


class EntryExitRule(unittest.TestCase):
    def test_entry_side_shorts_rich_spread_longs_cheap(self):
        self.assertEqual(pairs.entry_side(3.0), -1)
        self.assertEqual(pairs.entry_side(-3.0), 1)

    def test_should_open_only_beyond_z_in(self):
        self.assertTrue(pairs.should_open(pairs.Z_IN + 0.01))
        self.assertTrue(pairs.should_open(-(pairs.Z_IN + 0.01)))
        self.assertFalse(pairs.should_open(pairs.Z_IN - 0.01))
        self.assertFalse(pairs.should_open(None))

    def test_should_close_only_within_z_out(self):
        self.assertTrue(pairs.should_close(pairs.Z_OUT - 0.01))
        self.assertTrue(pairs.should_close(0.0))
        self.assertFalse(pairs.should_close(pairs.Z_OUT + 0.01))
        self.assertFalse(pairs.should_close(None))

    def test_zscore_none_on_degenerate_window(self):
        flat = np.full(pairs.ZWIN_HOURS, 5.0)
        self.assertIsNone(pairs.zscore(flat, 5.0))
        self.assertIsNone(pairs.zscore(np.array([1.0, 2.0]), 1.5))
        self.assertIsNone(pairs.zscore(np.full(pairs.ZWIN_HOURS, 1.0), np.nan))

    def test_zscore_value_matches_standardization(self):
        w = np.arange(float(pairs.ZWIN_HOURS))
        z = pairs.zscore(w, w.mean() + w.std())
        self.assertIsNotNone(z)
        np.testing.assert_allclose(z, 1.0, atol=1e-9)

    def test_round_trip_cost_is_four_legs_plus_funding(self):
        self.assertEqual(pairs.round_trip_cost(0.0), 4 * pairs.COST_LEG)
        self.assertAlmostEqual(
            pairs.round_trip_cost(100.0), 4 * pairs.COST_LEG + 100.0 * pairs.FUND_HR
        )


class Selection(unittest.TestCase):
    def test_liquid_universe_pit_filters_coverage_and_volume(self):
        n = pairs.SEL_HOURS
        sel = slice(0, n)
        logp = {
            "LIQ/USDT": np.zeros(n),
            "THIN/USDT": np.zeros(n),
            "GAPPY/USDT": np.concatenate([np.zeros(n // 3), np.full(n - n // 3, np.nan)]),
        }
        dvol = {
            "LIQ/USDT": np.full(n, pairs.MIN_DVOL * 2),
            "THIN/USDT": np.full(n, pairs.MIN_DVOL * 0.1),
            "GAPPY/USDT": np.full(n, pairs.MIN_DVOL * 2),
        }
        live = pairs.liquid_universe(logp, dvol, sel)
        self.assertIn("LIQ/USDT", live)
        self.assertNotIn("THIN/USDT", live)
        self.assertNotIn("GAPPY/USDT", live)

    def test_select_pairs_keeps_mean_reverting_and_sorts_by_half_life(self):
        n = pairs.SEL_HOURS
        sel = slice(0, n)
        rng = np.random.default_rng(2)
        base = np.cumsum(rng.normal(0, 0.02, n))
        ou = np.zeros(n)
        for i in range(1, n):
            # phi=0.97 -> half-life ~23h, inside the [12h, 20d] selection band
            ou[i] = 0.97 * ou[i - 1] + rng.normal(0, 0.05)
        logp = {
            "A/USDT": base + ou,
            "B/USDT": base,
            "Z/USDT": np.cumsum(rng.normal(0, 0.02, n)),
        }
        specs = pairs.select_pairs(list(logp), logp, sel)
        self.assertTrue(any({s.a, s.b} == {"A/USDT", "B/USDT"} for s in specs))
        # ranked by spread_std / half_life DESCENDING (max-return: wide + fast)
        keys = [s.spread_std / s.half_life for s in specs]
        self.assertEqual(keys, sorted(keys, reverse=True))


if __name__ == "__main__":
    unittest.main()
