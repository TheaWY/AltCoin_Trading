"""Forced-liquidation collector tests.

parse_force_order() and aggregate_1h() are pure (no I/O); store_events() and
the getter are tested against a real temp SQLite Storage. No network.

The load-bearing correctness points, each pinned by a test:
  - Binance's order side is INVERSE the liquidated position (SELL order =>
    a LONG was liquidated). Getting this backwards inverts every downstream
    liq_imbalance signal.
  - The 1h aggregate is additive across flushes within the same hour bucket.
  - The point-in-time guard drops future-dated events.
  - A malformed message never raises.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.data.collectors.liquidations import (
    aggregate_1h,
    parse_force_order,
    store_events,
)
from src.data.storage import Storage


def _storage() -> Storage:
    return Storage(Path(tempfile.mkdtemp()) / "test.db", database_url="")


def _force_order(symbol="BTCUSDT", side="SELL", price="100.0", qty="2.0", T=3_600_000):
    """A !forceOrder@arr event envelope (the 'o' payload is what Binance sends)."""
    return {"e": "forceOrder", "E": T, "o": {
        "s": symbol, "S": side, "ap": price, "q": qty, "T": T,
    }}


class ParseTests(unittest.TestCase):
    def test_sell_order_is_a_long_liquidation(self):
        row = parse_force_order(_force_order(side="SELL"))
        self.assertIsNotNone(row)
        # SELL liquidation order => a LONG position was force-closed.
        self.assertEqual(row["side"], "long")
        self.assertEqual(row["symbol"], "BTC/USDT")
        self.assertAlmostEqual(row["notional"], 200.0)  # 100 * 2
        self.assertEqual(row["timestamp"], 3600)  # ms -> s

    def test_buy_order_is_a_short_liquidation(self):
        row = parse_force_order(_force_order(side="BUY"))
        self.assertEqual(row["side"], "short")

    def test_malformed_message_returns_none(self):
        self.assertIsNone(parse_force_order({"o": {"s": "BTCUSDT"}}))  # missing fields
        self.assertIsNone(parse_force_order({}))
        self.assertIsNone(parse_force_order({"o": {"s": "X", "S": "SELL",
                                                    "ap": "0", "q": "1", "T": 1000}}))  # price<=0

    def test_avg_price_preferred_over_order_price(self):
        msg = {"o": {"s": "ETHUSDT", "S": "BUY", "ap": "50.0", "p": "999", "q": "1", "T": 1000}}
        row = parse_force_order(msg)
        self.assertAlmostEqual(row["price"], 50.0)


class AggregateTests(unittest.TestCase):
    def test_splits_long_and_short_and_tracks_largest(self):
        events = [
            {"symbol": "BTC/USDT", "timestamp": 3601, "side": "long", "notional": 100.0, "price": 1, "qty": 1},
            {"symbol": "BTC/USDT", "timestamp": 3602, "side": "long", "notional": 300.0, "price": 1, "qty": 1},
            {"symbol": "BTC/USDT", "timestamp": 3603, "side": "short", "notional": 50.0, "price": 1, "qty": 1},
        ]
        aggs = aggregate_1h(events)
        self.assertEqual(len(aggs), 1)  # all in the same hour bucket (3600)
        a = aggs[0]
        self.assertEqual(a["timestamp"], 3600)
        self.assertAlmostEqual(a["long_liq_notional"], 400.0)
        self.assertAlmostEqual(a["short_liq_notional"], 50.0)
        self.assertEqual(a["liq_count"], 3)
        self.assertAlmostEqual(a["largest_single_liq"], 300.0)

    def test_separate_hour_buckets(self):
        events = [
            {"symbol": "BTC/USDT", "timestamp": 3600, "side": "long", "notional": 100.0, "price": 1, "qty": 1},
            {"symbol": "BTC/USDT", "timestamp": 7200, "side": "long", "notional": 100.0, "price": 1, "qty": 1},
        ]
        self.assertEqual(len(aggregate_1h(events)), 2)


class StoreTests(unittest.TestCase):
    def test_additive_across_flushes_in_same_bucket(self):
        st = _storage()
        # two separate flushes landing in the same hour bucket must ACCUMULATE.
        store_events(st, [parse_force_order(_force_order(price="100", qty="1", T=3_600_000))],
                     now_fn=lambda: 10_000)
        store_events(st, [parse_force_order(_force_order(price="100", qty="3", T=3_610_000))],
                     now_fn=lambda: 10_000)
        rows = st.get_liquidation_agg("BTC/USDT")
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["long_liq_notional"], 400.0)  # 100 + 300
        self.assertEqual(rows[0]["liq_count"], 2)

    def test_point_in_time_guard_drops_future_events(self):
        st = _storage()
        # event stamped at t=3600s but "now" is 100s => far in the future, drop it.
        n = store_events(st, [parse_force_order(_force_order(T=3_600_000))], now_fn=lambda: 100.0)
        self.assertEqual(n, 0)
        self.assertEqual(st.get_liquidation_agg("BTC/USDT"), [])

    def test_before_gives_point_in_time_read(self):
        st = _storage()
        store_events(st, [parse_force_order(_force_order(T=3_600_000))], now_fn=lambda: 10_000)
        store_events(st, [parse_force_order(_force_order(T=7_200_000))], now_fn=lambda: 10_000)
        # as-of t=3600: only the first bucket is visible
        rows = st.get_liquidation_agg("BTC/USDT", before=3600)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["timestamp"], 3600)

    def test_getter_before_stream_ever_ran_returns_empty(self):
        # table created lazily; a reader must see "no data", not crash.
        st = _storage()
        self.assertEqual(st.get_liquidation_agg("BTC/USDT"), [])


class FaultIsolationTests(unittest.TestCase):
    """The collector must never be able to halt trading. On the Mac mini it is
    disabled by default (runs on a remote host instead); if enabled, a stream
    that dies must not propagate into the scheduler startup path."""

    def test_disabled_by_default_starts_no_thread(self):
        from src import config
        from src.runtime import start_liquidation_stream
        self.assertFalse(config.LIQUIDATION_STREAM_ENABLED)  # off on the mini
        self.assertIsNone(start_liquidation_stream())

    def test_stream_crash_does_not_propagate(self):
        import time as _time
        from unittest import mock

        from src import runtime

        boom_called = {"n": 0}

        def _boom(*a, **k):
            boom_called["n"] += 1
            raise RuntimeError("simulated stream failure")

        with mock.patch.object(runtime.config, "LIQUIDATION_STREAM_ENABLED", True), \
             mock.patch("src.data.collectors.liquidations.run_liquidation_stream", _boom):
            # must return a thread and NOT raise, even though the stream explodes
            thread = runtime.start_liquidation_stream()
            self.assertIsNotNone(thread)
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        # the failure happened inside the thread and was swallowed
        self.assertEqual(boom_called["n"], 1)


if __name__ == "__main__":
    unittest.main()
