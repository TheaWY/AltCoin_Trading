"""Integration tests: BacktestPortfolio opening/closing a market-neutral
(hedged) trade -- the backtest counterpart to
tests/test_hedge_paper_trader.py, exercising the SAME shared hedge module
(src.engine.hedge) so live and backtest sizing/close math can never diverge.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src import config  # noqa: E402
from src.data.storage import Storage  # noqa: E402
from src.engine.paper_trader import FakeDeltaNeutralError  # noqa: E402

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import backtest as backtest_module  # noqa: E402

BacktestPortfolio = backtest_module.BacktestPortfolio
HOUR = 3600


def _storage() -> Storage:
    return Storage(Path(tempfile.mkdtemp()) / "test.db", database_url="")


class BacktestHedgeOpenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_start = config.PAPER_STARTING_CAPITAL
        self.old_max_pos = config.MAX_POSITION_PCT
        config.PAPER_STARTING_CAPITAL = 1000.0
        config.MAX_POSITION_PCT = 0.20

    def tearDown(self) -> None:
        config.PAPER_STARTING_CAPITAL = self.old_start
        config.MAX_POSITION_PCT = self.old_max_pos

    def test_market_neutral_metadata_opens_both_legs(self) -> None:
        portfolio = BacktestPortfolio(cash=1000.0, storage=_storage())
        prices = {"AAA/USDT": 1.0, "BTC/USDT": 50000.0}
        trade = portfolio.open_trade(
            "AAA/USDT", "LONG", 1.0, 1_700_000_000,
            strategy="rel_strength_rotation",
            metadata={"execution_mode": "market_neutral", "hedge_symbol": "BTC/USDT", "beta": 1.2},
            prices=prices,
        )
        self.assertIsNotNone(trade)
        self.assertEqual(trade.hedge_symbol, "BTC/USDT")
        self.assertEqual(trade.hedge_direction, "SHORT")
        self.assertGreater(trade.hedge_quantity, 0)

        combined = trade.notional + trade.hedge_quantity * trade.hedge_entry_price
        self.assertLessEqual(combined, 1000.0 * 0.20 + 1e-6)
        self.assertAlmostEqual(portfolio.cash, 1000.0 - combined, places=6)

    def test_no_prices_dict_means_no_hedge_leg_not_a_crash(self) -> None:
        """If the caller doesn't pass the current BTC price, the hedge can't
        be sized -- this must fall through to no-hedge behavior, not open a
        naked or default-sized hedge."""
        portfolio = BacktestPortfolio(cash=1000.0, storage=_storage())
        trade = portfolio.open_trade(
            "AAA/USDT", "LONG", 1.0, 1_700_000_000,
            strategy="rel_strength_rotation",
            metadata={"execution_mode": "market_neutral", "hedge_symbol": "BTC/USDT", "beta": 1.2},
        )
        self.assertIsNotNone(trade)
        self.assertIsNone(trade.hedge_symbol)


class BacktestForcedCloseClosesHedgeTests(unittest.TestCase):
    """A forced primary-leg close, for ANY exit reason including
    end_of_backtest (backtest's version of a forced flatten), must close
    the hedge leg in the same pass."""

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

    def _open(self, storage: Storage, ts: int) -> tuple:
        portfolio = BacktestPortfolio(cash=1000.0, storage=storage)
        prices = {"AAA/USDT": 1.0, "BTC/USDT": 50000.0}
        trade = portfolio.open_trade(
            "AAA/USDT", "LONG", 1.0, ts,
            strategy="rel_strength_rotation",
            metadata={"execution_mode": "market_neutral", "hedge_symbol": "BTC/USDT", "beta": 1.2, "style": "rel_strength_neutral"},
            prices=prices,
        )
        return portfolio, trade

    def _assert_closed_no_naked_leg_and_cash_conserved(self, portfolio: BacktestPortfolio, expected_reason: str) -> None:
        self.assertEqual(len(portfolio.open_trades), 0)
        self.assertEqual(len(portfolio.closed_trades), 1)
        trade = portfolio.closed_trades[0]
        self.assertEqual(trade.exit_reason, expected_reason)
        self.assertIsNotNone(trade.hedge_exit_price)
        self.assertIsNotNone(trade.hedge_pnl)
        self.assertIsNotNone(trade.hedge_fees)
        self.assertIsNotNone(trade.funding_pnl)
        # "Ledger rebuild": recompute portfolio value fresh from cash + the
        # (now empty) open_trades list and confirm no residual hedge value
        # is floating around uncounted.
        self.assertEqual(portfolio.value({}, trade.closed_at), portfolio.cash)

    def test_stop_loss_closes_hedge_leg(self) -> None:
        storage = _storage()
        portfolio, trade = self._open(storage, 1_700_000_000)
        stop_price = trade.stop_loss - 0.001
        portfolio.check_exits({"AAA/USDT": stop_price, "BTC/USDT": 50000.0}, 1_700_000_100)
        self._assert_closed_no_naked_leg_and_cash_conserved(portfolio, "stop_loss")

    def test_take_profit_closes_hedge_leg(self) -> None:
        storage = _storage()
        portfolio, trade = self._open(storage, 1_700_000_000)
        tp_price = trade.take_profit + 0.001
        portfolio.check_exits({"AAA/USDT": tp_price, "BTC/USDT": 50500.0}, 1_700_000_100)
        self._assert_closed_no_naked_leg_and_cash_conserved(portfolio, "take_profit")

    def test_time_stop_closes_hedge_leg(self) -> None:
        storage = _storage()
        portfolio, trade = self._open(storage, 1_700_000_000)
        later = 1_700_000_000 + int((config.REL_STRENGTH_NEUTRAL_HOLD_HOURS + 1) * HOUR)
        portfolio.check_exits({"AAA/USDT": 1.0005, "BTC/USDT": 50100.0}, later)
        self._assert_closed_no_naked_leg_and_cash_conserved(portfolio, "time_stop")

    def test_end_of_backtest_close_all_closes_hedge_leg(self) -> None:
        storage = _storage()
        portfolio, trade = self._open(storage, 1_700_000_000)
        portfolio.close_all({"AAA/USDT": 1.01, "BTC/USDT": 50100.0}, 1_700_010_000)
        self._assert_closed_no_naked_leg_and_cash_conserved(portfolio, "end_of_backtest")

    def test_funding_and_realized_beta_populated_when_history_available(self) -> None:
        storage = _storage()
        opened_at = 1_700_000_000
        rows_btc, rows_aaa = [], []
        btc_price, aaa_price = 50000.0, 1.0
        for h in range(25):
            ts = opened_at + h * HOUR
            rows_btc.append({"symbol": "BTC/USDT", "timestamp": ts, "timeframe": "1h", "open": btc_price, "high": btc_price, "low": btc_price, "close": btc_price, "volume": 1.0})
            rows_aaa.append({"symbol": "AAA/USDT", "timestamp": ts, "timeframe": "1h", "open": aaa_price, "high": aaa_price, "low": aaa_price, "close": aaa_price, "volume": 1.0})
            btc_price *= 1.001
            aaa_price *= 1.0012
        storage.insert_prices(rows_btc, timeframe="1h")
        storage.insert_prices(rows_aaa, timeframe="1h")
        storage.insert_funding_rates([
            {"symbol": "BTC/USDT", "timestamp": opened_at + 8 * HOUR, "funding_rate": 0.0004},
            {"symbol": "BTC/USDT", "timestamp": opened_at + 16 * HOUR, "funding_rate": 0.0003},
        ])

        portfolio, trade = self._open(storage, opened_at)
        closed_ts = opened_at + 24 * HOUR
        take_profit_price = trade.take_profit + 0.001
        portfolio.check_exits({"AAA/USDT": take_profit_price, "BTC/USDT": 50600.0}, closed_ts)
        closed = portfolio.closed_trades[0]
        self.assertNotEqual(closed.funding_pnl, 0.0)
        self.assertGreater(closed.funding_pnl, 0.0)  # SHORT BTC receives positive funding
        self.assertIsNotNone(closed.realized_beta)
        self.assertIsNotNone(closed.realized_correlation)


class BacktestFundingCarryDeltaNeutralTests(unittest.TestCase):
    """funding_carry delta-neutral is now IMPLEMENTED (was FakeDeltaNeutralError).
    primary = LONG spot ('1h'), hedge = SHORT perp ('1h_perp'); the short leg
    collects funding = the carry. Verify it opens both legs, collects funding,
    and closes cleanly with no naked leg."""

    def test_delta_neutral_collects_funding_and_closes_cleanly(self) -> None:
        storage = _storage()
        opened_at = 1_700_000_000
        spot_rows, perp_rows = [], []
        sp, pp = 1.0, 1.002                      # perp at a small premium (basis)
        for h in range(400):                     # cover the hold + funding window
            ts = opened_at + h * HOUR
            spot_rows.append({"symbol": "AAA/USDT", "timestamp": ts, "timeframe": "1h",
                              "open": sp, "high": sp, "low": sp, "close": sp, "volume": 1.0})
            perp_rows.append({"symbol": "AAA/USDT", "timestamp": ts, "timeframe": "1h_perp",
                              "open": pp, "high": pp, "low": pp, "close": pp, "volume": 1.0})
            sp *= 1.0005; pp *= 1.0005            # both legs move together -> price P&L cancels
        storage.insert_prices(spot_rows, timeframe="1h")
        storage.insert_prices(perp_rows, timeframe="1h_perp")
        # persistent positive funding -> the SHORT perp leg RECEIVES it
        storage.insert_funding_rates([
            {"symbol": "AAA/USDT", "timestamp": opened_at + i * 8 * HOUR, "funding_rate": 0.001}
            for i in range(1, 45)])

        portfolio = BacktestPortfolio(cash=1000.0, storage=storage)
        trade = portfolio.open_trade(
            "AAA/USDT", "LONG", 1.0, opened_at, strategy="funding_carry",
            metadata={"execution_mode": "delta_neutral", "hedge_symbol": "AAA/USDT",
                      "style": "funding_carry"},
            prices={"AAA/USDT": 1.0},
        )
        self.assertIsNotNone(trade)
        self.assertEqual(trade.hedge_symbol, "AAA/USDT")          # short perp of same symbol
        self.assertEqual(trade.hedge_direction, "SHORT")

        closed_ts = opened_at + 300 * HOUR
        portfolio.close_all({"AAA/USDT": spot_rows[300]["close"]}, closed_ts)
        self.assertEqual(len(portfolio.open_trades), 0)
        closed = portfolio.closed_trades[0]
        self.assertGreater(closed.funding_pnl, 0.0)               # carry collected on the short
        self.assertIsNotNone(closed.hedge_pnl)
        self.assertAlmostEqual(portfolio.value({}, closed_ts), portfolio.cash)  # no naked leg


if __name__ == "__main__":
    unittest.main()
