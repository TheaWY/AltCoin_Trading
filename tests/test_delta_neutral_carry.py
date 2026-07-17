"""Delta-neutral funding-carry P&L spec (short perp + long spot, same coin).

Proves the EXISTING shared hedge primitives price the carry correctly, so the
funding_carry execution build is plumbing (feed the short leg 1h_perp prices +
the long leg 1h spot prices + remove the FakeDeltaNeutralError guards), NOT new
P&L math. This test is the integration spec:

  primary leg = SHORT perp   -> pnl = N*(perp_entry - perp_exit)/perp_entry
  hedge   leg = LONG  spot    -> pnl = N*(spot_exit  - spot_entry)/spot_entry
  funding = funding_pnl_for_leg(SHORT, ...)   (short RECEIVES when rate>0)
  total   = combined_trade_pnl(primary_pnl, primary_fees, hedge_pnl, hedge_fees, funding)
          = N*(spot_ret - perp_ret) + funding - fees

See [[funding-carry-execution-plan]] / research memory.
"""
from __future__ import annotations

import unittest

from src.engine.hedge import (
    combined_trade_pnl,
    delta_neutral_hedge_leg,
    funding_pnl_for_leg,
)
from src.strategies.base import SignalDirection

N = 1000.0
HOUR = 3600
S8 = 8 * HOUR


def _short_perp_pnl(entry, exit_, notional=N):
    return notional * (entry - exit_) / entry


def _long_spot_pnl(entry, exit_, notional=N):
    return notional * (exit_ - entry) / entry


def _funding_rows(t0, rates):
    # one row per 8h settlement starting at t0
    return [{"timestamp": t0 + i * S8, "funding_rate": r} for i, r in enumerate(rates)]


class DeltaNeutralCarryPnL(unittest.TestCase):
    def test_short_receives_positive_funding(self):
        rows = _funding_rows(1000, [0.001, 0.001])  # 2 settlements @ 0.1%
        # entry/exit bracket both settlements
        f = funding_pnl_for_leg(SignalDirection.SHORT.value, rows, N, 0, 1000 + 3 * S8)
        self.assertAlmostEqual(f, 2 * 0.001 * N)     # short RECEIVES => +2.0
        # a long on the same leg PAYS it
        fl = funding_pnl_for_leg(SignalDirection.LONG.value, rows, N, 0, 1000 + 3 * S8)
        self.assertAlmostEqual(fl, -2 * 0.001 * N)

    def test_basis_widens_carry_still_nets_by_formula(self):
        # perp rose 2%, spot rose 1% -> basis widened; short perp -20, long spot +10
        primary = _short_perp_pnl(100, 102)          # -20
        hedge = _long_spot_pnl(100, 101)             # +10
        funding = 2 * 0.001 * N                       # +2 (two settlements)
        fees_per_leg = 0.0002 * N * 2                 # 2bps/side, open+close -> 0.4
        total = combined_trade_pnl(primary, fees_per_leg, hedge, fees_per_leg, funding)
        # N*(spot_ret - perp_ret) + funding - fees = 1000*(0.01-0.02) + 2 - 0.8
        self.assertAlmostEqual(total, -10 + 2 - 0.8)
        self.assertAlmostEqual(primary + hedge, N * (0.01 - 0.02))  # price = -Δbasis*N

    def test_basis_convergence_is_the_edge(self):
        # perp entered at a premium (102) and converged to spot (100->100).
        # short perp gains on convergence; that + funding IS the carry.
        primary = _short_perp_pnl(102, 100)          # +19.607...
        hedge = _long_spot_pnl(100, 100)             # 0
        funding = 3 * 0.0005 * N                       # +1.5
        total = combined_trade_pnl(primary, 0.0, hedge, 0.0, funding)
        self.assertGreater(total, 0)
        self.assertAlmostEqual(total, N * (102 - 100) / 102 + 1.5)

    def test_market_move_cancels_when_delta_neutral(self):
        # both legs move together (perp & spot both +10%): price P&L ~0, only
        # funding remains -- the whole point of delta-neutral.
        primary = _short_perp_pnl(100, 110)          # -100
        hedge = _long_spot_pnl(100, 110)             # +100
        funding = 5 * 0.001 * N
        total = combined_trade_pnl(primary, 0.0, hedge, 0.0, funding)
        self.assertAlmostEqual(primary + hedge, 0.0)  # market move fully hedged
        self.assertAlmostEqual(total, funding)         # pure carry


class DeltaNeutralLegSizing(unittest.TestCase):
    """The shared sizing primitive: primary LONG spot + hedge SHORT perp, same
    symbol, EQUAL notional (beta=1, dollar+delta neutral), hedge priced off perp."""

    def test_equal_notional_short_perp_hedge(self):
        leg = delta_neutral_hedge_leg(
            "KAITO/USDT", SignalDirection.LONG.value, spot_price=1.00, perp_price=1.02,
            cash=1000.0, portfolio_value=1000.0, max_position_pct=0.03)
        self.assertIsNotNone(leg)
        self.assertEqual(leg["hedge_symbol"], "KAITO/USDT")          # SAME symbol
        self.assertEqual(leg["hedge_direction"], SignalDirection.SHORT.value)  # short the perp
        self.assertEqual(leg["hedge_entry_price"], 1.02)              # priced off PERP
        self.assertEqual(leg["hedge_beta"], 1.0)
        self.assertAlmostEqual(leg["primary_notional"], leg["hedge_notional"])  # EQUAL
        # one 3% slot split across two beta=1 legs -> each = 1.5% of portfolio
        self.assertAlmostEqual(leg["primary_notional"], 1000.0 * 0.03 / 2)
        self.assertAlmostEqual(leg["hedge_quantity"], leg["hedge_notional"] / 1.02)

    def test_none_when_no_perp_price(self):
        self.assertIsNone(delta_neutral_hedge_leg(
            "X/USDT", SignalDirection.LONG.value, spot_price=1.0, perp_price=None,
            cash=1000, portfolio_value=1000, max_position_pct=0.03))
        self.assertIsNone(delta_neutral_hedge_leg(
            "X/USDT", SignalDirection.LONG.value, spot_price=1.0, perp_price=0.0,
            cash=1000, portfolio_value=1000, max_position_pct=0.03))

    def test_none_when_no_cash(self):
        self.assertIsNone(delta_neutral_hedge_leg(
            "X/USDT", SignalDirection.LONG.value, spot_price=1.0, perp_price=1.0,
            cash=0.0, portfolio_value=1000, max_position_pct=0.03))


if __name__ == "__main__":
    unittest.main()
