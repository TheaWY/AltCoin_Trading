"""Unit tests for src/engine/hedge.py -- the shared market-neutral hedge
math used identically by PaperTrader (live) and BacktestPortfolio
(backtest). No I/O, no database: pure function behavior only.
"""

from __future__ import annotations

import unittest

from src.engine import hedge
from src.strategies.base import SignalDirection


class HedgeDirectionTests(unittest.TestCase):
    def test_long_primary_hedges_short(self):
        self.assertEqual(hedge.hedge_direction_for(SignalDirection.LONG.value), SignalDirection.SHORT.value)

    def test_short_primary_hedges_long(self):
        self.assertEqual(hedge.hedge_direction_for(SignalDirection.SHORT.value), SignalDirection.LONG.value)


class ExAnteBetaTests(unittest.TestCase):
    def _rows(self, closes: list[float], start_ts: int = 0) -> list[dict]:
        return [{"timestamp": start_ts + i * 3600, "close": c} for i, c in enumerate(closes)]

    def test_perfectly_correlated_beta_is_one(self):
        btc = [100.0 * (1.01 ** i) for i in range(40)]
        alt = list(btc)  # identical returns -> beta 1, corr 1
        beta = hedge.ex_ante_beta(self._rows(alt), self._rows(btc), lookback=40, min_points=24)
        self.assertIsNotNone(beta)
        self.assertAlmostEqual(beta, 1.0, places=6)

    def test_amplified_moves_give_beta_above_one(self):
        btc_rets = [0.01, -0.02, 0.015, -0.005, 0.02, -0.01, 0.005, -0.015] * 5
        btc = [100.0]
        alt = [100.0]
        for r in btc_rets:
            btc.append(btc[-1] * (1 + r))
            alt.append(alt[-1] * (1 + 2.0 * r))  # alt moves 2x BTC's return each step
        beta = hedge.ex_ante_beta(self._rows(alt), self._rows(btc), lookback=len(btc), min_points=24)
        self.assertIsNotNone(beta)
        self.assertGreater(beta, 1.5)

    def test_insufficient_points_returns_none(self):
        btc = [100.0 * (1.01 ** i) for i in range(10)]
        alt = list(btc)
        beta = hedge.ex_ante_beta(self._rows(alt), self._rows(btc), lookback=40, min_points=24)
        self.assertIsNone(beta)

    def test_non_positive_beta_returns_none_not_a_default(self):
        # Inverse correlation -> negative beta. The hedge assumption (short
        # BTC against a long alt) doesn't hold, so this must be None, never
        # a fallback default that would silently size a broken hedge.
        btc = [100.0 * (1.01 ** i) for i in range(40)]
        alt = [100.0 * (0.99 ** i) for i in range(40)]
        beta = hedge.ex_ante_beta(self._rows(alt), self._rows(btc), lookback=40, min_points=24)
        self.assertIsNone(beta)


class PositionCapsTests(unittest.TestCase):
    def test_splits_slot_by_beta_and_recombines_to_the_cap(self):
        primary_cap, hedge_cap = hedge.position_caps(1000.0, 0.20, beta=1.2)
        self.assertAlmostEqual(primary_cap, 200.0 / 2.2, places=6)
        self.assertAlmostEqual(hedge_cap, primary_cap * 1.2, places=6)
        self.assertAlmostEqual(primary_cap + hedge_cap, 200.0, places=6)

    def test_beta_of_one_splits_evenly(self):
        primary_cap, hedge_cap = hedge.position_caps(1000.0, 0.20, beta=1.0)
        self.assertAlmostEqual(primary_cap, hedge_cap, places=6)
        self.assertAlmostEqual(primary_cap + hedge_cap, 200.0, places=6)


class LegPnlAndFeesTests(unittest.TestCase):
    def test_long_leg_profits_when_price_rises(self):
        self.assertEqual(hedge.leg_pnl(SignalDirection.LONG.value, 10.0, 12.0, 5.0), 10.0)

    def test_short_leg_profits_when_price_falls(self):
        self.assertEqual(hedge.leg_pnl(SignalDirection.SHORT.value, 10.0, 8.0, 5.0), 10.0)

    def test_short_leg_loses_when_price_rises(self):
        self.assertEqual(hedge.leg_pnl(SignalDirection.SHORT.value, 10.0, 12.0, 5.0), -10.0)

    def test_leg_position_value_short_is_notional_plus_pnl(self):
        value = hedge.leg_position_value(SignalDirection.SHORT.value, 10.0, 8.0, 5.0)
        self.assertEqual(value, 50.0 + 10.0)

    def test_leg_fees_scale_with_notional(self):
        self.assertAlmostEqual(hedge.leg_fees(1000.0, 0.0016), 1.6, places=8)


class FundingPnlTests(unittest.TestCase):
    SETTLEMENT = hedge.SETTLEMENT_SECONDS

    def test_short_leg_receives_positive_funding(self):
        rows = [{"timestamp": self.SETTLEMENT, "funding_rate": 0.0004}]
        pnl = hedge.funding_pnl_for_leg(
            SignalDirection.SHORT.value, rows, notional=1000.0, entry_ts=0, exit_ts=self.SETTLEMENT * 2
        )
        self.assertAlmostEqual(pnl, 0.4, places=8)

    def test_short_leg_pays_negative_funding(self):
        rows = [{"timestamp": self.SETTLEMENT, "funding_rate": -0.0003}]
        pnl = hedge.funding_pnl_for_leg(
            SignalDirection.SHORT.value, rows, notional=1000.0, entry_ts=0, exit_ts=self.SETTLEMENT * 2
        )
        self.assertAlmostEqual(pnl, -0.3, places=8)

    def test_long_leg_sign_is_flipped(self):
        rows = [{"timestamp": self.SETTLEMENT, "funding_rate": 0.0004}]
        pnl = hedge.funding_pnl_for_leg(
            SignalDirection.LONG.value, rows, notional=1000.0, entry_ts=0, exit_ts=self.SETTLEMENT * 2
        )
        self.assertAlmostEqual(pnl, -0.4, places=8)

    def test_settlements_outside_window_excluded(self):
        rows = [
            {"timestamp": self.SETTLEMENT, "funding_rate": 0.0004},  # inside
            {"timestamp": self.SETTLEMENT * 5, "funding_rate": 0.01},  # outside, after exit
        ]
        pnl = hedge.funding_pnl_for_leg(
            SignalDirection.SHORT.value, rows, notional=1000.0, entry_ts=0, exit_ts=self.SETTLEMENT * 2
        )
        self.assertAlmostEqual(pnl, 0.4, places=8)

    def test_multiple_collection_prints_same_bucket_use_last_print_only(self):
        # Collection-frequency rows within one 8h window -- only the last
        # print per settlement bucket should count, not a sum of all of them.
        rows = [
            {"timestamp": 100, "funding_rate": 0.0001},
            {"timestamp": 200, "funding_rate": 0.0002},
            {"timestamp": self.SETTLEMENT - 10, "funding_rate": 0.0005},
        ]
        pnl = hedge.funding_pnl_for_leg(
            SignalDirection.SHORT.value, rows, notional=1000.0, entry_ts=0, exit_ts=self.SETTLEMENT
        )
        self.assertAlmostEqual(pnl, 0.5, places=8)

    def test_three_settlements_over_72h(self):
        rows = [
            {"timestamp": self.SETTLEMENT * 1, "funding_rate": 0.0003},
            {"timestamp": self.SETTLEMENT * 2, "funding_rate": 0.0003},
            {"timestamp": self.SETTLEMENT * 3, "funding_rate": -0.0001},
        ]
        pnl = hedge.funding_pnl_for_leg(
            SignalDirection.SHORT.value, rows, notional=1000.0, entry_ts=0, exit_ts=self.SETTLEMENT * 9
        )
        self.assertAlmostEqual(pnl, 0.3 + 0.3 - 0.1, places=8)


class RealizedBetaCorrelationTests(unittest.TestCase):
    def test_realized_beta_uses_only_the_holding_window(self):
        rows_primary = [{"timestamp": i * 3600, "close": 1.0 * (1.02 ** i)} for i in range(50)]
        rows_hedge = [{"timestamp": i * 3600, "close": 100.0 * (1.01 ** i)} for i in range(50)]
        result = hedge.realized_beta_and_correlation(
            rows_primary, rows_hedge, entry_ts=10 * 3600, exit_ts=40 * 3600, min_points=24
        )
        self.assertIsNotNone(result["beta"])
        self.assertGreater(result["points"], 0)

    def test_too_few_points_in_window_returns_none(self):
        rows_primary = [{"timestamp": i * 3600, "close": 1.0 * (1.02 ** i)} for i in range(50)]
        rows_hedge = [{"timestamp": i * 3600, "close": 100.0 * (1.01 ** i)} for i in range(50)]
        result = hedge.realized_beta_and_correlation(
            rows_primary, rows_hedge, entry_ts=0, exit_ts=3 * 3600, min_points=24
        )
        self.assertIsNone(result["beta"])


class BasisRiskStatusTests(unittest.TestCase):
    def test_within_tolerance_is_held(self):
        self.assertEqual(hedge.basis_risk_status(1.0, 1.3, tolerance=0.5), "correlation_held")

    def test_outside_tolerance_is_spiked(self):
        self.assertEqual(hedge.basis_risk_status(1.0, 2.0, tolerance=0.5), "correlation_spiked")

    def test_missing_realized_beta_is_unknown(self):
        self.assertEqual(hedge.basis_risk_status(1.0, None, tolerance=0.5), "unknown")

    def test_missing_ex_ante_beta_is_unknown(self):
        self.assertEqual(hedge.basis_risk_status(None, 1.0, tolerance=0.5), "unknown")


class CombinedTradePnlTests(unittest.TestCase):
    def test_nets_all_five_components(self):
        pnl = hedge.combined_trade_pnl(
            primary_pnl=10.0, primary_fees=1.0, hedge_pnl=3.0, hedge_fees=0.5, funding_pnl=0.4
        )
        self.assertAlmostEqual(pnl, (10.0 - 1.0) + (3.0 - 0.5) + 0.4, places=8)


if __name__ == "__main__":
    unittest.main()
