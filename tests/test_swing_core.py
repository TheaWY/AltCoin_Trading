"""Trend-following core: legs split evenly, a leg below its MA goes to cash,
and cash is conserved through entries and exits."""

from __future__ import annotations

import unittest
from unittest import mock

from src.engine import core_manager as cm


class _Store:
    def __init__(self, cash, px):
        self.cash, self.px, self.trades, self.next_id = cash, dict(px), {}, 1

    def get_portfolio_state(self):
        return {"cash": self.cash}

    def update_portfolio_cash(self, c):
        self.cash = c

    def get_latest_price(self, s):
        return {"close": self.px[s]}

    def get_open_trades(self, symbol=None):
        return [t for t in self.trades.values() if t["status"] == "open"]

    def insert_paper_trade(self, row):
        row = dict(row, id=self.next_id)
        self.trades[self.next_id] = row
        self.next_id += 1
        return row["id"]

    def update_paper_trade(self, tid, fields):
        self.trades[tid].update(fields)

    def equity(self):
        return self.cash + sum(t["quantity"] * self.px[t["symbol"]] for t in self.get_open_trades())


class SwingCoreTests(unittest.TestCase):
    def _run(self, st, up):
        def summary(_self, _price):
            return {"equity": st.equity()}

        def trend(_storage, sym, ma, now=None):
            return {"symbol": sym, "up": up[sym], "ma_days": ma}

        with mock.patch.object(cm.config, "CORE_ENABLED", True, create=True), \
             mock.patch.object(cm.config, "CORE_SYMBOLS", "BTC/USDT,ETH/USDT", create=True), \
             mock.patch.object(cm.config, "CORE_TREND_MA", 50, create=True), \
             mock.patch.object(cm.config, "CORE_PCT", 0.97, create=True), \
             mock.patch.object(cm.config, "CORE_BAND", 0.05, create=True), \
             mock.patch.object(cm, "trend_state", trend), \
             mock.patch("src.engine.signal_book.pct", lambda: 0.0), \
             mock.patch("src.engine.pump_rider.reserve_pct", lambda _s: 0.0), \
             mock.patch("src.engine.paper_trader.PaperTrader.summary", summary), \
             mock.patch("src.engine.paper_trader.PaperTrader.__init__", lambda self, s: None):
            return cm.run_core_cycle(st, now=1000)

    def test_splits_legs_and_skips_downtrend(self):
        st = _Store(1000.0, {"BTC/USDT": 50000.0, "ETH/USDT": 2000.0})
        r = self._run(st, {"BTC/USDT": True, "ETH/USDT": False})
        self.assertEqual(r["legs"]["BTC/USDT"], "trend_entry")
        self.assertEqual(r["legs"]["ETH/USDT"], "flat")
        held = {t["symbol"]: t["quantity"] * st.px[t["symbol"]] for t in st.get_open_trades()}
        self.assertAlmostEqual(held["BTC/USDT"], 485.0, delta=1.0)
        self.assertNotIn("ETH/USDT", held)

    def test_trend_exit_returns_cash(self):
        st = _Store(1000.0, {"BTC/USDT": 50000.0, "ETH/USDT": 2000.0})
        self._run(st, {"BTC/USDT": True, "ETH/USDT": True})
        self.assertEqual(len(st.get_open_trades()), 2)
        st.px["BTC/USDT"] = 45000.0
        before = st.equity()
        r = self._run(st, {"BTC/USDT": False, "ETH/USDT": True})
        self.assertEqual(r["legs"]["BTC/USDT"], "trend_exit")
        self.assertEqual([t["symbol"] for t in st.get_open_trades()], ["ETH/USDT"])
        closed = [t for t in st.trades.values() if t["status"] == "closed"][0]
        self.assertAlmostEqual(st.equity(), before - closed["quantity"] * 45000.0 * 0.0005, places=6)


if __name__ == "__main__":
    unittest.main()
