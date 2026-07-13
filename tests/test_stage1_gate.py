"""T5: GATE -- stage1_pass's rescored denominator (traded windows, not all
windows) plus the new activity floor.

Note on the (a) case: the user's original illustrative numbers were "10/15
traded windows out of 40 total... scores 66.7% and PASSES". That is
internally inconsistent with the 50% activity floor this same change
introduces: 15/40 = 37.5% traded-fraction, which fails the floor BEFORE the
window-consistency check ever runs -- it would not pass. Rather than fudge
the floor or silently reinterpret "40" to make it pass, this test uses 20
total windows (15/20 = 75% traded-fraction, comfortably clears the 50%
floor) so every other stated constraint (10/15 traded positive = 66.7%,
S1_MIN_TRADES, expectancy, PF, passes) is satisfied exactly as specified.
"""

from __future__ import annotations

import json
import unittest

from src.research import promotion


def _windows(traded_count: int, positive_count: int, untraded_count: int, trades_per_traded: int = 3) -> list[dict]:
    windows = []
    for i in range(traded_count):
        windows.append({
            "trade_count": trades_per_traded,
            "total_pnl": 10.0 if i < positive_count else -5.0,
            "trade_pnls": [10.0 / trades_per_traded] * trades_per_traded if i < positive_count
            else [-5.0 / trades_per_traded] * trades_per_traded,
        })
    for _ in range(untraded_count):
        windows.append({"trade_count": 0, "total_pnl": 0.0, "trade_pnls": []})
    return windows


def _experiment(windows: list[dict], trade_count: int, expectancy: float = 5.0, profit_factor: float = 1.5) -> dict:
    metrics = {
        "windows": windows,
        "aggregate": {
            "trade_count": trade_count,
            "expectancy": expectancy,
            "profit_factor": profit_factor,
        },
    }
    return {"metrics_json": json.dumps(metrics)}


class Stage1ActivityFloorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_min_traded_pct = promotion.S1_MIN_TRADED_WINDOW_PCT
        self.old_min_trades = promotion.S1_MIN_TRADES
        self.old_win_fraction = promotion.S1_MIN_WINDOW_WIN_FRACTION
        self.old_min_pf = promotion.S1_MIN_PROFIT_FACTOR
        promotion.S1_MIN_TRADED_WINDOW_PCT = 0.50
        promotion.S1_MIN_TRADES = 30
        promotion.S1_MIN_WINDOW_WIN_FRACTION = 0.66
        promotion.S1_MIN_PROFIT_FACTOR = 1.2

    def tearDown(self) -> None:
        promotion.S1_MIN_TRADED_WINDOW_PCT = self.old_min_traded_pct
        promotion.S1_MIN_TRADES = self.old_min_trades
        promotion.S1_MIN_WINDOW_WIN_FRACTION = self.old_win_fraction
        promotion.S1_MIN_PROFIT_FACTOR = self.old_min_pf

    def test_a_10_of_15_traded_windows_scores_667_pct_and_passes(self) -> None:
        # 15 traded (10 positive, 5 negative) out of 20 total -> 75% traded-
        # fraction (clears the 50% floor) and 10/15 = 66.7% window win
        # fraction (exactly clears S1_MIN_WINDOW_WIN_FRACTION=0.66).
        windows = _windows(traded_count=15, positive_count=10, untraded_count=5, trades_per_traded=3)
        exp = _experiment(windows, trade_count=45, expectancy=5.0, profit_factor=1.5)
        passed, reason = promotion.stage1_pass(exp, champion=None)
        self.assertTrue(passed, reason)
        self.assertIn("10/15", reason)
        self.assertIn("15/20", reason)

    def test_b_trading_in_only_3_windows_fails_on_activity_floor(self) -> None:
        # Same 20-window universe, but only 3 windows traded (all positive,
        # otherwise a "great" strategy) -- 3/20 = 15% traded-fraction, well
        # under the 50% floor. A strategy this selective is a lottery
        # ticket, not a validated edge, and must fail on the ACTIVITY FLOOR
        # specifically (its own named reason), not window consistency.
        windows = _windows(traded_count=3, positive_count=3, untraded_count=17, trades_per_traded=10)
        exp = _experiment(windows, trade_count=30, expectancy=5.0, profit_factor=1.5)
        passed, reason = promotion.stage1_pass(exp, champion=None)
        self.assertFalse(passed)
        self.assertIn("activity floor", reason)
        self.assertIn("3/20", reason)

    def test_c_gate2_and_gate3_thresholds_unchanged(self) -> None:
        """Regression assert: only stage 1's denominator/floor changed --
        gate 2 (DSR) and gate 3 (fresh OOS) constants must be untouched."""
        self.assertEqual(promotion.PROMO_MIN_DSR, 0.95)
        self.assertEqual(promotion.S2_MIN_FRESH_DAYS, 14.0)
        self.assertEqual(promotion.S2_MIN_TRADES, 10)

    def test_zero_trade_window_does_not_count_against_consistency(self) -> None:
        """The core bug being fixed: a strategy that trades in every window
        it's given a chance to and wins all of them must not be penalized
        for windows it had zero opportunity/reason to trade in. Here ALL
        traded windows are positive; passes cleanly."""
        windows = _windows(traded_count=12, positive_count=12, untraded_count=8, trades_per_traded=3)
        exp = _experiment(windows, trade_count=36, expectancy=5.0, profit_factor=1.5)
        passed, reason = promotion.stage1_pass(exp, champion=None)
        self.assertTrue(passed, reason)

    def test_beats_champion_still_enforced_after_rescoring(self) -> None:
        windows = _windows(traded_count=15, positive_count=10, untraded_count=5, trades_per_traded=3)
        exp = _experiment(windows, trade_count=45, expectancy=5.0, profit_factor=1.5)
        champion = _experiment(
            _windows(traded_count=15, positive_count=10, untraded_count=5, trades_per_traded=3),
            trade_count=45, expectancy=10.0, profit_factor=1.5,
        )
        passed, reason = promotion.stage1_pass(exp, champion=champion)
        self.assertFalse(passed)
        self.assertIn("does not beat champion", reason)

    def test_no_windows_at_all_fails_distinctly(self) -> None:
        exp = _experiment([], trade_count=0)
        passed, reason = promotion.stage1_pass(exp, champion=None)
        self.assertFalse(passed)
        self.assertIn("no walk-forward windows", reason)


if __name__ == "__main__":
    unittest.main()
