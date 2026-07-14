"""Unit tests for src/engine/exits.py -- the shared exit-level math both
PaperTrader and BacktestPortfolio must go through (rule #2). The
cross-engine parity tests live in tests/test_exit_parity.py; these cover
the pure functions in isolation.
"""

from __future__ import annotations

import unittest

from src.engine import exits

MULTS = {"atr_stop_mult": 1.5, "atr_tp_mult": 2.5,
         "fallback_stop_pct": 0.03, "fallback_tp_pct": 0.06}


class ExitLevelsTests(unittest.TestCase):
    def test_long_atr_scaled(self):
        stop, tp = exits.exit_levels("LONG", 100.0, 2.0, **MULTS)
        # stop = 1.5 x 2% = 3% below; tp = 2.5 x 2% = 5% above
        self.assertAlmostEqual(stop, 97.0)
        self.assertAlmostEqual(tp, 105.0)

    def test_short_atr_scaled(self):
        stop, tp = exits.exit_levels("SHORT", 100.0, 2.0, **MULTS)
        self.assertAlmostEqual(stop, 103.0)
        self.assertAlmostEqual(tp, 95.0)

    def test_gross_rr_is_constant_across_volatility(self):
        """The core property: R:R == atr_tp_mult/atr_stop_mult for EVERY
        atr_pct -- a low-vol symbol can never end up with target < stop."""
        for atr in (0.3, 0.5, 1.0, 2.0, 5.0):
            stop, tp = exits.exit_levels("LONG", 100.0, atr, **MULTS)
            rr = (tp - 100.0) / (100.0 - stop)
            self.assertAlmostEqual(rr, 2.5 / 1.5, places=9)

    def test_no_atr_falls_back_to_fixed_pct(self):
        stop, tp = exits.exit_levels("LONG", 100.0, None, **MULTS)
        self.assertAlmostEqual(stop, 97.0)
        self.assertAlmostEqual(tp, 106.0)

    def test_zero_atr_treated_as_missing(self):
        stop, tp = exits.exit_levels("LONG", 100.0, 0.0, **MULTS)
        self.assertAlmostEqual(stop, 97.0)
        self.assertAlmostEqual(tp, 106.0)


class PositionNotionalTests(unittest.TestCase):
    SIZING = {"risk_per_trade_pct": 0.01, "max_position_pct": 0.20}

    def test_risk_based_below_cap(self):
        # 7.5% stop distance: 0.01*1000/0.075 = 133.33 < 200 cap
        n = exits.position_notional(1000.0, 1000.0, 100.0, 92.5, **self.SIZING)
        self.assertAlmostEqual(n, 1000.0 * 0.01 / 0.075)

    def test_caps_at_max_position_pct(self):
        # 3% stop: 333.33 risk-based -> capped at 200
        n = exits.position_notional(1000.0, 1000.0, 100.0, 97.0, **self.SIZING)
        self.assertAlmostEqual(n, 200.0)

    def test_caps_at_available_cash(self):
        n = exits.position_notional(1000.0, 150.0, 100.0, 97.0, **self.SIZING)
        self.assertAlmostEqual(n, 150.0)

    def test_degenerate_stop_distance_sizes_zero(self):
        self.assertEqual(exits.position_notional(1000.0, 1000.0, 100.0, 100.0, **self.SIZING), 0.0)
        self.assertEqual(exits.position_notional(1000.0, 1000.0, 0.0, 97.0, **self.SIZING), 0.0)

    def test_short_direction_stop_above_price(self):
        # SHORT: stop above entry; |distance| logic must be direction-agnostic
        n = exits.position_notional(1000.0, 1000.0, 100.0, 107.5, **self.SIZING)
        self.assertAlmostEqual(n, 1000.0 * 0.01 / 0.075)


class TrailingStopUpdateTests(unittest.TestCase):
    def test_no_atr_means_no_trailing(self):
        self.assertEqual(
            exits.trailing_stop_update("LONG", 100.0, 105.0, 100.0, 97.0, None, trail_atr_mult=2.0),
            {},
        )

    def test_long_new_high_updates_trail_price(self):
        updates = exits.trailing_stop_update("LONG", 100.0, 100.5, 100.0, 97.0, 2.0, trail_atr_mult=2.0)
        self.assertEqual(updates, {"trail_price": 100.5})

    def test_long_ratchets_stop_after_one_atr_move(self):
        # price 103 = entry + 1.5 ATR (ATR 2%) -> trail arms; stop trails
        # 2 x 2% = 4% behind best: 103 * 0.96 = 98.88 > 97 -> ratchet up.
        updates = exits.trailing_stop_update("LONG", 100.0, 103.0, 100.0, 97.0, 2.0, trail_atr_mult=2.0)
        self.assertAlmostEqual(updates["trail_price"], 103.0)
        self.assertAlmostEqual(updates["stop_loss"], 103.0 * 0.96)

    def test_long_stop_never_widens(self):
        # Stop already ABOVE where the trail formula would put it -> no change
        # (rule #6: risk-reducing direction only).
        updates = exits.trailing_stop_update("LONG", 100.0, 103.0, 103.0, 99.5, 2.0, trail_atr_mult=2.0)
        self.assertNotIn("stop_loss", updates)

    def test_long_price_retreat_does_not_lower_trail(self):
        updates = exits.trailing_stop_update("LONG", 100.0, 101.0, 104.0, 99.84, 2.0, trail_atr_mult=2.0)
        self.assertNotIn("trail_price", updates)

    def test_short_mirror_ratchet(self):
        # SHORT from 100, price falls to 97 (= entry - 1.5 ATR), stop trails
        # 4% ABOVE best: 97 * 1.04 = 100.88 < 103 -> ratchet down.
        updates = exits.trailing_stop_update("SHORT", 100.0, 97.0, 100.0, 103.0, 2.0, trail_atr_mult=2.0)
        self.assertAlmostEqual(updates["trail_price"], 97.0)
        self.assertAlmostEqual(updates["stop_loss"], 97.0 * 1.04)

    def test_short_stop_never_widens(self):
        updates = exits.trailing_stop_update("SHORT", 100.0, 97.0, 97.0, 100.5, 2.0, trail_atr_mult=2.0)
        self.assertNotIn("stop_loss", updates)

    def test_not_armed_before_one_atr_move(self):
        # Best is only +0.5 ATR in favor -> trail not armed, stop untouched
        # even though a new best is recorded.
        updates = exits.trailing_stop_update("LONG", 100.0, 101.0, 100.0, 97.0, 2.0, trail_atr_mult=2.0)
        self.assertEqual(updates, {"trail_price": 101.0})


class TrailArmTests(unittest.TestCase):
    def test_lower_arm_engages_earlier(self):
        # +0.6 ATR move: default arm (1.0) does nothing, arm 0.5 ratchets.
        base = exits.trailing_stop_update("LONG", 100.0, 101.2, 100.0, 97.0, 2.0,
                                          trail_atr_mult=1.0, trail_arm_atr=1.0)
        self.assertNotIn("stop_loss", base)
        early = exits.trailing_stop_update("LONG", 100.0, 101.2, 100.0, 97.0, 2.0,
                                           trail_atr_mult=1.0, trail_arm_atr=0.5)
        self.assertIn("stop_loss", early)
        self.assertAlmostEqual(early["stop_loss"], 101.2 * 0.98)

    def test_higher_arm_engages_later(self):
        # +1.2 ATR: arm 1.0 fires, arm 1.5 does not.
        fired = exits.trailing_stop_update("LONG", 100.0, 102.4, 100.0, 97.0, 2.0,
                                           trail_atr_mult=1.0, trail_arm_atr=1.0)
        self.assertIn("stop_loss", fired)
        held = exits.trailing_stop_update("LONG", 100.0, 102.4, 100.0, 97.0, 2.0,
                                          trail_atr_mult=1.0, trail_arm_atr=1.5)
        self.assertNotIn("stop_loss", held)


class PartialTpLevelTests(unittest.TestCase):
    def test_long_level_is_entry_plus_r(self):
        self.assertAlmostEqual(exits.partial_tp_level("LONG", 100.0, 3.0, 1.0), 103.0)

    def test_short_level_is_entry_minus_r(self):
        self.assertAlmostEqual(exits.partial_tp_level("SHORT", 100.0, 3.0, 1.0), 97.0)

    def test_zero_at_r_disables(self):
        self.assertIsNone(exits.partial_tp_level("LONG", 100.0, 3.0, 0.0))

    def test_degenerate_r_disables(self):
        self.assertIsNone(exits.partial_tp_level("LONG", 100.0, 0.0, 1.0))


class MfeCaptureTests(unittest.TestCase):
    def test_full_capture(self):
        mfe, cap = exits.mfe_capture("LONG", 100.0, 105.0, 105.0)
        self.assertAlmostEqual(mfe, 5.0)
        self.assertAlmostEqual(cap, 1.0)

    def test_dexe_case_gave_it_all_back(self):
        # +9.5% MFE, exited -0.31%: capture ~ -0.03 -- the finding that
        # motivated the exit-geometry axes.
        mfe, cap = exits.mfe_capture("SHORT", 100.0, 100.31, 91.32)
        self.assertAlmostEqual(mfe, 9.5, delta=0.1)
        self.assertLess(cap, 0.0)

    def test_never_in_profit_has_no_capture(self):
        mfe, cap = exits.mfe_capture("LONG", 100.0, 95.0, 100.0)
        self.assertIsNone(cap)

    def test_missing_hwm_returns_none(self):
        self.assertEqual(exits.mfe_capture("LONG", 100.0, 105.0, None), (None, None))

    def test_immaterial_wiggle_has_no_capture(self):
        """A straight loser with a 0.2%-of-price wiggle (0.1 ATR at 2% ATR)
        must NOT produce a capture ratio -- dividing by near-zero MFE makes
        -10.0 outliers that pollute the distribution."""
        mfe, cap = exits.mfe_capture("SHORT", 100.0, 107.3, 99.8, atr_pct=2.0)
        self.assertAlmostEqual(mfe, 0.2, delta=0.01)
        self.assertIsNone(cap)

    def test_material_move_still_captured(self):
        # 1.5 ATR favorable move clears the 0.5-ATR materiality floor.
        mfe, cap = exits.mfe_capture("LONG", 100.0, 101.0, 103.0, atr_pct=2.0)
        self.assertIsNotNone(cap)
        self.assertAlmostEqual(cap, 1.0 / 3.0, places=3)


if __name__ == "__main__":
    unittest.main()
