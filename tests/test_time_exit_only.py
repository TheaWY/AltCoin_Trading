"""No-stop / time-exit-only execution mode (2026-07-15 execution-model reframe).

When a strategy is listed in TIME_EXIT_ONLY_STRATEGIES, the price-trigger block
(stop / trailing / partial-TP) is skipped in BOTH engines and only the time-stop
can close the trade. Default (flag unset) leaves every strategy's behavior
unchanged -- that parity is the critical guard, since the flag lives on the
shared exit path.
"""

from __future__ import annotations

import os
import time
import unittest
from unittest import mock

from src import config
from scripts.backtest import BacktestPortfolio
from src.engine.paper_trader import PaperTrader
from tests.test_engine_parity import _bt_trade, _live_trade_row, _seed_orderbook, _storage


class TimeExitOnlyConfigTests(unittest.TestCase):
    def test_flag_parses_and_defaults_off(self):
        os.environ.pop("TIME_EXIT_ONLY_STRATEGIES", None)
        self.assertFalse(config.time_exit_only("mean_reversion_long"))
        self.assertFalse(config.time_exit_only(None))
        with mock.patch.dict(os.environ, {"TIME_EXIT_ONLY_STRATEGIES": "mean_reversion_long, foo"}):
            self.assertTrue(config.time_exit_only("mean_reversion_long"))
            self.assertTrue(config.time_exit_only("foo"))
            self.assertFalse(config.time_exit_only("momentum"))


class BacktestTimeExitOnlyTests(unittest.TestCase):
    def setUp(self):
        self._saved = {k: getattr(config, k) for k in ("SCALP_MAX_HOLD_HOURS", "TRAILING_STOP_ENABLED")}
        config.SCALP_MAX_HOLD_HOURS = 6.0
        config.TRAILING_STOP_ENABLED = False
        os.environ.pop("TIME_EXIT_ONLY_STRATEGIES", None)

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(config, k, v)
        os.environ.pop("TIME_EXIT_ONLY_STRATEGIES", None)

    def _portfolio_with_trade(self, opened_at):
        p = BacktestPortfolio(cash=1000.0, storage=_storage())
        bt = _bt_trade("AAA/USDT", "LONG", 100.0, 1.0, stop=95.0, tp=110.0,
                       opened_at=opened_at, style="scalp")
        p.open_trades.append(bt)
        p.cash -= bt.notional
        return p

    def test_default_adverse_bar_stops_out(self):
        # flag off: a price through the stop closes the trade (unchanged behavior)
        now = int(time.time())
        p = self._portfolio_with_trade(now - 60)
        p.check_exits({"AAA/USDT": 94.0}, now)  # 94 < stop 95
        self.assertEqual(len(p.closed_trades), 1)
        self.assertEqual(p.closed_trades[0].exit_reason, "stop_loss")

    def test_time_exit_only_ignores_the_stop(self):
        now = int(time.time())
        p = self._portfolio_with_trade(now - 60)
        with mock.patch.dict(os.environ, {"TIME_EXIT_ONLY_STRATEGIES": "parity_test"}):
            p.check_exits({"AAA/USDT": 90.0}, now)  # deep through the stop
            self.assertEqual(len(p.closed_trades), 0, "no-stop trade must not stop out")
            self.assertEqual(len(p.open_trades), 1)
            # ...but the time-stop still closes it once max-hold elapses
            p.check_exits({"AAA/USDT": 90.0}, now + int(7 * 3600))  # > 6h hold
        self.assertEqual(len(p.closed_trades), 1)
        self.assertEqual(p.closed_trades[0].exit_reason, "time_stop")


class LiveTimeExitOnlyTests(unittest.TestCase):
    def setUp(self):
        self._saved = {k: getattr(config, k) for k in ("SCALP_MAX_HOLD_HOURS", "TRAILING_STOP_ENABLED")}
        config.SCALP_MAX_HOLD_HOURS = 6.0
        config.TRAILING_STOP_ENABLED = False
        os.environ.pop("TIME_EXIT_ONLY_STRATEGIES", None)

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(config, k, v)
        os.environ.pop("TIME_EXIT_ONLY_STRATEGIES", None)

    def _trader_with_trade(self):
        storage = _storage()
        now = int(time.time())
        _seed_orderbook(storage, "AAA/USDT", now - 10)
        storage.init_portfolio_state(1000.0)
        storage.insert_paper_trade(
            _live_trade_row("AAA/USDT", "LONG", 1.0, 100.0, 95.0, 110.0, now - 60)
        )
        return PaperTrader(storage)

    def test_live_default_stops_out(self):
        trader = self._trader_with_trade()
        closed = trader.check_open_trades_for_symbol("AAA/USDT", 94.0)
        self.assertEqual(len(closed), 1, "default: adverse price through the stop closes the trade")

    def test_live_time_exit_only_ignores_stop(self):
        trader = self._trader_with_trade()
        # the seeded row's strategy is "parity_test"
        with mock.patch.dict(os.environ, {"TIME_EXIT_ONLY_STRATEGIES": "parity_test"}):
            closed = trader.check_open_trades_for_symbol("AAA/USDT", 90.0)
        self.assertEqual(len(closed), 0, "no-stop live trade must not stop out")


if __name__ == "__main__":
    unittest.main()
