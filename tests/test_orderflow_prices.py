"""Order-flow capture on the prices path (A2).

Verifies the raw-kline -> price-row mapping and that insert_prices persists the
order-flow columns WITHOUT clobbering them when a later backfill upsert (which
uses fetch_ohlcv and supplies no order-flow keys) touches the same bar.

The OHLCV byte-for-byte parity between the raw klines endpoint and
ccxt.fetch_ohlcv is validated live (they hit the same endpoint); this suite
covers the pure mapping + storage semantics with no network.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.data.collectors.binance import _kline_to_price_row
from src.data.storage import Storage


def _storage() -> Storage:
    return Storage(Path(tempfile.mkdtemp()) / "test.db", database_url="")


# a raw Binance futures kline (12 fields)
_KLINE = [
    1_700_000_000_000,  # openTime ms
    "100.0", "110.0", "90.0", "105.0",  # o h l c
    "1000.0",           # volume (base)
    1_700_003_599_999,  # closeTime
    "105000.0",         # quoteVolume
    500,                # numTrades
    "600.0",            # takerBuyBase
    "63000.0",          # takerBuyQuote
    "0",                # ignore
]


class KlineMappingTests(unittest.TestCase):
    def test_maps_ohlcv_and_orderflow(self):
        row = _kline_to_price_row("BTC/USDT", _KLINE, "1h")
        self.assertEqual(row["timestamp"], 1_700_000_000)  # ms -> s
        self.assertEqual(row["open"], 100.0)
        self.assertEqual(row["close"], 105.0)
        self.assertEqual(row["volume"], 1000.0)
        self.assertEqual(row["quote_volume"], 105000.0)
        self.assertEqual(row["num_trades"], 500)
        self.assertEqual(row["taker_buy_base"], 600.0)
        self.assertEqual(row["taker_buy_quote"], 63000.0)

    def test_orderflow_enables_ofi(self):
        row = _kline_to_price_row("BTC/USDT", _KLINE, "1h")
        # OFI = (taker_buy - taker_sell)/total = (2*taker_buy_base - vol)/vol
        ofi = (2 * row["taker_buy_base"] - row["volume"]) / row["volume"]
        self.assertAlmostEqual(ofi, 0.2)  # 600 buy vs 400 sell of 1000


class InsertPricesOrderflowTests(unittest.TestCase):
    def test_round_trips_orderflow_columns(self):
        st = _storage()
        st.insert_prices([_kline_to_price_row("BTC/USDT", _KLINE, "1h")], timeframe="1h")
        latest = st.get_latest_price("BTC/USDT", "1h")
        self.assertEqual(latest["num_trades"], 500)
        self.assertEqual(latest["taker_buy_base"], 600.0)
        self.assertEqual(latest["quote_volume"], 105000.0)

    def test_backfill_upsert_does_not_clobber_orderflow(self):
        st = _storage()
        # 1) live collector writes a bar WITH order flow
        st.insert_prices([_kline_to_price_row("BTC/USDT", _KLINE, "1h")], timeframe="1h")
        # 2) a backfill (fetch_ohlcv path) re-writes the SAME bar with no order-flow keys
        st.insert_prices([{
            "symbol": "BTC/USDT", "timestamp": 1_700_000_000, "timeframe": "1h",
            "open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0, "volume": 1000.0,
        }], timeframe="1h")
        latest = st.get_latest_price("BTC/USDT", "1h")
        # order flow must survive the backfill upsert (COALESCE, not NULL clobber)
        self.assertEqual(latest["num_trades"], 500)
        self.assertEqual(latest["taker_buy_base"], 600.0)

    def test_columns_exist_on_fresh_db(self):
        st = _storage()
        with st._connect() as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(prices)").fetchall()}
        for c in ("quote_volume", "num_trades", "taker_buy_base", "taker_buy_quote"):
            self.assertIn(c, cols)


if __name__ == "__main__":
    unittest.main()
