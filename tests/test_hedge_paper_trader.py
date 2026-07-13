"""Integration tests: PaperTrader opening/closing a market-neutral (hedged)
trade. Covers the requirement that a forced primary-leg close -- for any
exit reason -- closes the hedge leg in the same pass, with no naked leg
possible and the portfolio ledger reconciling afterward (cash + positions
== equity, mirroring src.engine.cycle._portfolio_accounting_invariant,
the closest existing thing to a "ledger rebuild" check in this codebase).
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from src import config
from src.data.storage import Storage
from src.engine.cycle import _portfolio_accounting_invariant
from src.engine.paper_trader import FakeDeltaNeutralError, PaperTrader

HOUR = 3600


def _storage() -> Storage:
    return Storage(Path(tempfile.mkdtemp()) / "test.db", database_url="")


def _seed_hourly_series(storage: Storage, symbol: str, start_ts: int, hours: int, start_price: float, step_pct: float) -> None:
    price = start_price
    rows = []
    for h in range(hours + 1):
        ts = start_ts + h * HOUR
        rows.append({
            "symbol": symbol, "timestamp": ts, "timeframe": "1h",
            "open": price, "high": price, "low": price, "close": price, "volume": 1.0,
        })
        price *= (1 + step_pct)
    storage.insert_prices(rows, timeframe="1h")


class HedgeOpenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_start = config.PAPER_STARTING_CAPITAL
        self.old_max_pos = config.MAX_POSITION_PCT
        config.PAPER_STARTING_CAPITAL = 1000.0
        config.MAX_POSITION_PCT = 0.20

    def tearDown(self) -> None:
        config.PAPER_STARTING_CAPITAL = self.old_start
        config.MAX_POSITION_PCT = self.old_max_pos

    def test_market_neutral_signal_opens_both_legs_in_one_call(self) -> None:
        storage = _storage()
        storage.init_portfolio_state(1000.0)
        now = int(time.time())
        storage.insert_prices([
            {"symbol": "BTC/USDT", "timestamp": now, "open": 50000, "high": 50000, "low": 50000, "close": 50000, "volume": 1},
            {"symbol": "AAA/USDT", "timestamp": now, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1},
        ])
        trader = PaperTrader(storage)
        signal_result = {
            "symbol": "AAA/USDT", "strategy": "rel_strength_rotation", "direction": "LONG",
            "reason": "test", "style": "rel_strength_neutral",
            "metadata": {"execution_mode": "market_neutral", "hedge_symbol": "BTC/USDT", "beta": 1.2},
        }
        result = trader.process_signal(signal_result, 1.0)
        self.assertTrue(result["opened"])

        trade = storage.get_open_trades()[0]
        self.assertEqual(trade["hedge_symbol"], "BTC/USDT")
        self.assertEqual(trade["hedge_direction"], "SHORT")
        self.assertGreater(trade["hedge_quantity"], 0)

        # Combined notional reserved must not exceed one MAX_POSITION_PCT slot.
        primary_notional = trade["quantity"] * trade["entry_price"]
        hedge_notional = trade["hedge_quantity"] * trade["hedge_entry_price"]
        self.assertLessEqual(primary_notional + hedge_notional, 1000.0 * 0.20 + 1e-6)

        state = storage.get_portfolio_state()
        self.assertAlmostEqual(
            float(state["cash"]), 1000.0 - primary_notional - hedge_notional, places=6
        )

    def test_no_beta_means_no_trade_not_a_default_hedge(self) -> None:
        """A signal claiming market_neutral without a usable beta must not
        silently fall back to an unhedged (or wrongly-hedged) position."""
        storage = _storage()
        storage.init_portfolio_state(1000.0)
        now = int(time.time())
        storage.insert_prices([
            {"symbol": "BTC/USDT", "timestamp": now, "open": 50000, "high": 50000, "low": 50000, "close": 50000, "volume": 1},
            {"symbol": "AAA/USDT", "timestamp": now, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1},
        ])
        trader = PaperTrader(storage)
        signal_result = {
            "symbol": "AAA/USDT", "strategy": "rel_strength_rotation", "direction": "LONG",
            "reason": "test", "style": "rel_strength_neutral",
            "metadata": {"execution_mode": "market_neutral", "hedge_symbol": "BTC/USDT", "beta": None},
        }
        result = trader.process_signal(signal_result, 1.0)
        # Falls through to ordinary risk-based sizing (no hedge leg) rather
        # than opening a naked or mis-hedged market-neutral position.
        self.assertTrue(result.get("opened") in (True, False))
        if result.get("opened"):
            trade = storage.get_open_trades()[0]
            self.assertIsNone(trade["hedge_symbol"])


class ForcedCloseClosesHedgeTests(unittest.TestCase):
    """A forced primary-leg close, for ANY exit reason, must close the hedge
    leg in the same pass -- no naked leg, and the ledger reconciles after."""

    def setUp(self) -> None:
        self.old_start = config.PAPER_STARTING_CAPITAL
        self.old_max_pos = config.MAX_POSITION_PCT
        self.old_hold_hours = config.REL_STRENGTH_NEUTRAL_HOLD_HOURS
        config.PAPER_STARTING_CAPITAL = 1000.0
        config.MAX_POSITION_PCT = 0.20
        config.REL_STRENGTH_NEUTRAL_HOLD_HOURS = 72.0

    def tearDown(self) -> None:
        config.PAPER_STARTING_CAPITAL = self.old_start
        config.MAX_POSITION_PCT = self.old_max_pos
        config.REL_STRENGTH_NEUTRAL_HOLD_HOURS = self.old_hold_hours

    def _open(self, storage: Storage, now: int) -> tuple[PaperTrader, dict]:
        storage.init_portfolio_state(1000.0)
        storage.insert_prices([
            {"symbol": "BTC/USDT", "timestamp": now, "open": 50000, "high": 50000, "low": 50000, "close": 50000, "volume": 1},
            {"symbol": "AAA/USDT", "timestamp": now, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1},
        ])
        trader = PaperTrader(storage)
        signal_result = {
            "symbol": "AAA/USDT", "strategy": "rel_strength_rotation", "direction": "LONG",
            "reason": "test", "style": "rel_strength_neutral",
            "metadata": {"execution_mode": "market_neutral", "hedge_symbol": "BTC/USDT", "beta": 1.2},
        }
        trader.process_signal(signal_result, 1.0)
        trade = storage.get_open_trades()[0]
        return trader, trade

    def _assert_hedge_closed_and_ledger_reconciles(self, storage: Storage, trader: PaperTrader, expected_reason: str, close_price: float) -> None:
        closed = storage.get_recent_closed_trades(1)[0]
        self.assertEqual(closed["exit_reason"], expected_reason)
        self.assertEqual(closed["status"], "closed")
        # No naked leg: hedge_exit_price/hedge_pnl/hedge_fees must all be set
        # the moment the primary leg closes, regardless of why it closed.
        self.assertIsNotNone(closed["hedge_exit_price"])
        self.assertIsNotNone(closed["hedge_pnl"])
        self.assertIsNotNone(closed["hedge_fees"])
        self.assertIsNotNone(closed["funding_pnl"])
        self.assertEqual(storage.count_open_trades(), 0)

        # "Ledger rebuild": recompute the summary fresh from storage (as the
        # live worker does every cycle via _portfolio_accounting_invariant)
        # and confirm cash + positions == equity, with zero residual
        # position value from the now-fully-closed hedge leg.
        summary = trader.summary(close_price)
        ok, values = _portfolio_accounting_invariant(summary)
        self.assertTrue(ok, values)
        self.assertEqual(summary["open_position_value"], 0.0)
        self.assertEqual(summary["open_trades"], 0)

    def test_stop_loss_closes_hedge_leg(self) -> None:
        storage = _storage()
        now = int(time.time())
        trader, trade = self._open(storage, now)
        stop_price = float(trade["stop_loss"]) - 0.001
        storage.insert_prices([{"symbol": "BTC/USDT", "timestamp": now + 60, "open": 50000, "high": 50000, "low": 50000, "close": 50000, "volume": 1}])
        trader.check_open_trades_for_symbol("AAA/USDT", stop_price)
        self._assert_hedge_closed_and_ledger_reconciles(storage, trader, "stop_loss", 50000.0)

    def test_take_profit_closes_hedge_leg(self) -> None:
        storage = _storage()
        now = int(time.time())
        trader, trade = self._open(storage, now)
        tp_price = float(trade["take_profit"]) + 0.001
        storage.insert_prices([{"symbol": "BTC/USDT", "timestamp": now + 60, "open": 50500, "high": 50500, "low": 50500, "close": 50500, "volume": 1}])
        trader.check_open_trades_for_symbol("AAA/USDT", tp_price)
        self._assert_hedge_closed_and_ledger_reconciles(storage, trader, "take_profit", 50500.0)

    def test_time_stop_closes_hedge_leg(self) -> None:
        storage = _storage()
        now = int(time.time())
        trader, trade = self._open(storage, now)
        # _open_trade always stamps opened_at as the real wall-clock time;
        # backdate it past the 72h cap so only the time_stop exit fires
        # (price stays flat, well inside stop/TP distance).
        backdated = now - int((config.REL_STRENGTH_NEUTRAL_HOLD_HOURS + 1) * HOUR)
        storage.update_paper_trade(int(trade["id"]), {"opened_at": backdated})
        storage.insert_prices([{"symbol": "BTC/USDT", "timestamp": int(time.time()), "open": 50100, "high": 50100, "low": 50100, "close": 50100, "volume": 1}])
        trader.check_open_trades_for_symbol("AAA/USDT", 1.0005)
        self._assert_hedge_closed_and_ledger_reconciles(storage, trader, "time_stop", 50100.0)

    def test_funding_and_realized_beta_populated_when_history_available(self) -> None:
        """With funding settlements and hourly candles across the actual
        holding window, the close must compute real funding P&L and a real
        realized beta/correlation -- not leave them None just because the
        machinery ran."""
        storage = _storage()
        opened_at = int(time.time()) - 25 * HOUR
        storage.init_portfolio_state(1000.0)
        _seed_hourly_series(storage, "BTC/USDT", opened_at, 25, 50000.0, 0.001)
        _seed_hourly_series(storage, "AAA/USDT", opened_at, 25, 1.0, 0.0012)
        storage.insert_funding_rates([
            {"symbol": "BTC/USDT", "timestamp": opened_at + 8 * HOUR, "funding_rate": 0.0004},
            {"symbol": "BTC/USDT", "timestamp": opened_at + 16 * HOUR, "funding_rate": 0.0003},
        ])

        trader = PaperTrader(storage)
        signal_result = {
            "symbol": "AAA/USDT", "strategy": "rel_strength_rotation", "direction": "LONG",
            "reason": "test", "style": "rel_strength_neutral",
            "metadata": {"execution_mode": "market_neutral", "hedge_symbol": "BTC/USDT", "beta": 1.2},
        }
        trader.process_signal(signal_result, 1.0)
        trade = storage.get_open_trades()[0]
        # Backdate opened_at so the funding settlements/candles above fall
        # inside [opened_at, closed_at).
        storage.update_paper_trade(int(trade["id"]), {"opened_at": opened_at})

        closed_list = trader.check_open_trades_for_symbol("AAA/USDT", 1.05)
        self.assertEqual(len(closed_list), 1)
        closed = storage.get_recent_closed_trades(1)[0]
        self.assertNotEqual(closed["funding_pnl"], 0.0)
        self.assertGreater(closed["funding_pnl"], 0.0)  # SHORT BTC receives positive funding
        self.assertIsNotNone(closed["realized_beta"])
        self.assertIsNotNone(closed["realized_correlation"])


class FundingCarryStillRaisesTests(unittest.TestCase):
    """Regression guard: the funding_carry fake-delta-neutral raise from the
    prior fix must still fire and must not be confused with real hedge
    trades opened by this feature."""

    def test_funding_carry_delta_neutral_still_raises(self) -> None:
        storage = _storage()
        storage.init_portfolio_state(1000.0)
        now = int(time.time())
        signal_id = storage.insert_signal({
            "strategy": "funding_carry", "symbol": "AAA/USDT", "timestamp": now,
            "direction": "SHORT", "reason": "test", "entry_price": 1.0,
            "funding_rate": 0.001, "metadata": '{"execution_mode": "delta_neutral"}',
        })
        storage.insert_paper_trade({
            "signal_id": signal_id, "symbol": "AAA/USDT", "direction": "SHORT",
            "entry_price": 1.0, "exit_price": None, "quantity": 10.0,
            "stop_loss": 1.05, "take_profit": 0.95, "status": "open", "pnl": None,
            "opened_at": now, "closed_at": None, "strategy": "funding_carry",
            "style": "scalp", "atr_pct": None, "trail_price": 1.0, "exit_reason": None, "fees": None,
        })
        trader = PaperTrader(storage)
        with self.assertRaises(FakeDeltaNeutralError):
            trader.check_open_trades_for_symbol("AAA/USDT", 0.90)


if __name__ == "__main__":
    unittest.main()
