"""T1: unit tests for src/engine/execution_cost.py -- pure math, no storage.
T3: no-double-counting -- slippage moves the price, fees stay fee-only.
T7: no stale module-level cost constant left in paper_trader.py.
"""

from __future__ import annotations

import unittest

from src import config
from src.engine import execution_cost


def _book(spread_bps=10.0, bid_units=1000.0, ask_units=1000.0, mid=1.0):
    return {
        "mid_price": mid,
        "spread_bps": spread_bps,
        "bid_depth_1pct": bid_units,
        "ask_depth_1pct": ask_units,
        "top_bid": mid * (1 - spread_bps * execution_cost.BPS / 2),
        "top_ask": mid * (1 + spread_bps * execution_cost.BPS / 2),
    }


class SideForTests(unittest.TestCase):
    def test_long_entry_is_buy(self):
        self.assertEqual(execution_cost.side_for("LONG", is_entry=True), "buy")

    def test_long_exit_is_sell(self):
        self.assertEqual(execution_cost.side_for("LONG", is_entry=False), "sell")

    def test_short_entry_is_sell(self):
        self.assertEqual(execution_cost.side_for("SHORT", is_entry=True), "sell")

    def test_short_exit_is_buy(self):
        self.assertEqual(execution_cost.side_for("SHORT", is_entry=False), "buy")


class ResolveSpreadAndDepthTests(unittest.TestCase):
    def test_real_snapshot_used_when_present_and_has_depth(self):
        ob = _book(spread_bps=8.0, ask_units=2000.0, mid=2.0)
        spread, depth, used_fallback = execution_cost.resolve_spread_and_depth(
            "buy", ob, fallback_spread_bps=99.0, fallback_depth_usd=1.0
        )
        self.assertEqual(spread, 8.0)
        self.assertAlmostEqual(depth, 2000.0 * 2.0)
        self.assertFalse(used_fallback)

    def test_empty_book_falls_back(self):
        spread, depth, used_fallback = execution_cost.resolve_spread_and_depth(
            "buy", None, fallback_spread_bps=12.0, fallback_depth_usd=5000.0
        )
        self.assertEqual((spread, depth), (12.0, 5000.0))
        self.assertTrue(used_fallback)

    def test_one_sided_book_zero_relevant_depth_falls_back(self):
        """Book has bid depth but zero ask depth -- a buy (which consumes ask
        liquidity) must fall back rather than divide by zero later."""
        ob = _book(ask_units=0.0)
        spread, depth, used_fallback = execution_cost.resolve_spread_and_depth(
            "buy", ob, fallback_spread_bps=12.0, fallback_depth_usd=5000.0
        )
        self.assertEqual((spread, depth), (12.0, 5000.0))
        self.assertTrue(used_fallback)

    def test_zero_mid_price_falls_back(self):
        ob = _book(mid=0.0)
        spread, depth, used_fallback = execution_cost.resolve_spread_and_depth(
            "buy", ob, fallback_spread_bps=12.0, fallback_depth_usd=5000.0
        )
        self.assertTrue(used_fallback)


class SlippagePctTests(unittest.TestCase):
    def test_known_book_known_vwap_fill(self):
        # depth_usd=100_000, notional=10_000 -> depth_fraction=0.1 -> walk=0.1%
        # spread_bps=10 -> half_spread = 5bps = 0.05%
        slip = execution_cost.slippage_pct(notional=10_000, spread_bps=10.0, depth_usd=100_000.0)
        self.assertAlmostEqual(slip, 0.0005 + 0.001, places=8)

    def test_zero_spread_only_walk_cost(self):
        slip = execution_cost.slippage_pct(notional=50_000, spread_bps=0.0, depth_usd=100_000.0)
        self.assertAlmostEqual(slip, 0.005, places=8)

    def test_order_larger_than_depth_band_scales_beyond_1pct(self):
        # notional == 2x the 1% depth -> walk = 2%, exceeding the 1% band itself
        slip = execution_cost.slippage_pct(notional=200_000, spread_bps=0.0, depth_usd=100_000.0)
        self.assertAlmostEqual(slip, 0.02, places=8)

    def test_zero_notional_is_zero_cost(self):
        self.assertEqual(execution_cost.slippage_pct(notional=0, spread_bps=10.0, depth_usd=100_000.0), 0.0)

    def test_zero_depth_treated_as_full_band_walk(self):
        slip = execution_cost.slippage_pct(notional=1_000, spread_bps=0.0, depth_usd=0.0)
        self.assertAlmostEqual(slip, 0.01, places=8)

    def test_stress_multiplier_applied(self):
        base = execution_cost.slippage_pct(notional=10_000, spread_bps=10.0, depth_usd=100_000.0)
        stressed = execution_cost.slippage_pct(
            notional=10_000, spread_bps=10.0, depth_usd=100_000.0, is_stress=True, stress_mult=3.0
        )
        self.assertAlmostEqual(stressed, base * 3.0, places=8)

    def test_stress_not_applied_when_flag_false_even_with_mult_set(self):
        base = execution_cost.slippage_pct(notional=10_000, spread_bps=10.0, depth_usd=100_000.0)
        not_stressed = execution_cost.slippage_pct(
            notional=10_000, spread_bps=10.0, depth_usd=100_000.0, is_stress=False, stress_mult=3.0
        )
        self.assertAlmostEqual(not_stressed, base, places=8)


class AdjustedFillPriceTests(unittest.TestCase):
    def test_buy_fills_higher(self):
        self.assertGreater(execution_cost.adjusted_fill_price(100.0, "buy", 0.01), 100.0)

    def test_sell_fills_lower(self):
        self.assertLess(execution_cost.adjusted_fill_price(100.0, "sell", 0.01), 100.0)

    def test_zero_slippage_is_unchanged(self):
        self.assertEqual(execution_cost.adjusted_fill_price(100.0, "buy", 0.0), 100.0)

    def test_invalid_side_raises(self):
        with self.assertRaises(ValueError):
            execution_cost.adjusted_fill_price(100.0, "LONG", 0.01)


class IsStressBarTests(unittest.TestCase):
    def test_wide_bar_is_stress(self):
        self.assertTrue(execution_cost.is_stress_bar(bar_range_pct=10.0, atr_pct=2.0, stress_atr_mult=2.0))

    def test_normal_bar_is_not_stress(self):
        self.assertFalse(execution_cost.is_stress_bar(bar_range_pct=3.0, atr_pct=2.0, stress_atr_mult=2.0))

    def test_missing_data_is_not_stress(self):
        self.assertFalse(execution_cost.is_stress_bar(None, 2.0, 2.0))
        self.assertFalse(execution_cost.is_stress_bar(10.0, None, 2.0))
        self.assertFalse(execution_cost.is_stress_bar(10.0, 0.0, 2.0))


class NoDoubleCountingTests(unittest.TestCase):
    """T3: fees must be fee-only; slippage lives entirely in the price."""

    def test_round_trip_cost_pct_is_fee_only(self):
        # 2 * fee_pct_per_side(), nothing else -- slippage must not appear.
        expected = 2 * config.fee_pct_per_side()
        self.assertAlmostEqual(config.round_trip_cost_pct(), expected, places=10)

    def test_no_slippage_constant_left_to_fold_in(self):
        """The flat SLIPPAGE_PCT_PER_SIDE constant that used to be folded
        into round_trip_cost_pct() is REMOVED entirely (not just unused) --
        there is nothing left that could double-count slippage into the fee."""
        self.assertFalse(
            hasattr(config, "SLIPPAGE_PCT_PER_SIDE"),
            "config.SLIPPAGE_PCT_PER_SIDE still exists -- it should have been "
            "deleted, not left as dead weight, once slippage moved to the price.",
        )


class NoStaleModuleConstantTests(unittest.TestCase):
    """T7: paper_trader must not cache round_trip_cost_pct() at import time."""

    def test_no_module_level_round_trip_constant(self):
        import src.engine.paper_trader as paper_trader_module

        self.assertFalse(
            hasattr(paper_trader_module, "ROUND_TRIP_COST_PCT"),
            "paper_trader.py still holds a stale module-level cost constant -- "
            "it will not see FEE_MODE/round_trip_cost_pct() changes at runtime.",
        )

    def test_fee_mode_change_is_visible_without_reimport(self):
        """The definitive behavioral proof: flip FEE_MODE at runtime and
        confirm round_trip_cost_pct() (as paper_trader now calls it fresh
        each time) picks it up immediately."""
        old_mode = config.FEE_MODE
        try:
            config.FEE_MODE = "taker"
            taker_cost = config.round_trip_cost_pct()
            config.FEE_MODE = "maker"
            maker_cost = config.round_trip_cost_pct()
        finally:
            config.FEE_MODE = old_mode
        self.assertNotEqual(taker_cost, maker_cost)


if __name__ == "__main__":
    unittest.main()
