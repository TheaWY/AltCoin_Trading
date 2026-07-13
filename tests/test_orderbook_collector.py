"""Orderbook collector tests.

_derive_features is pure (no I/O), so these test it directly against
mocked ccxt fetch_order_book() response shapes -- no network, no database.
"""

from __future__ import annotations

import unittest

from src.data.collectors.orderbook import _derive_features


def book(bids, asks):
    """ccxt fetch_order_book() shape: {"bids": [[price, qty], ...], "asks": [...]}."""
    return {"bids": bids, "asks": asks}


class DeriveFeaturesTests(unittest.TestCase):
    def test_balanced_book(self):
        b = book(
            bids=[[100.0, 10.0], [99.5, 5.0]],
            asks=[[100.5, 10.0], [101.0, 5.0]],
        )
        row = _derive_features("BTC/USDT", 1000, b)
        self.assertIsNotNone(row)
        self.assertEqual(row["symbol"], "BTC/USDT")
        self.assertEqual(row["timestamp"], 1000)
        self.assertAlmostEqual(row["top_bid"], 100.0)
        self.assertAlmostEqual(row["top_ask"], 100.5)
        self.assertAlmostEqual(row["mid_price"], 100.25)
        # both sides within 1% of mid (99.25-101.25) -> full depth counted
        self.assertAlmostEqual(row["bid_depth_1pct"], 15.0)
        self.assertAlmostEqual(row["ask_depth_1pct"], 15.0)
        self.assertAlmostEqual(row["imbalance_ratio"], 0.0)
        self.assertGreater(row["spread_bps"], 0)

    def test_imbalanced_book_positive_when_bids_heavier(self):
        b = book(bids=[[100.0, 100.0]], asks=[[100.5, 10.0]])
        row = _derive_features("ETH/USDT", 2000, b)
        self.assertGreater(row["imbalance_ratio"], 0)
        self.assertAlmostEqual(row["imbalance_ratio"], (100.0 - 10.0) / (100.0 + 10.0), places=6)

    def test_depth_excludes_prices_beyond_1pct(self):
        # mid = 100.25; 1% band is roughly [99.2475, 101.2525]
        b = book(
            bids=[[100.0, 10.0], [90.0, 999.0]],  # 90 is far outside the band
            asks=[[100.5, 10.0], [200.0, 999.0]],  # 200 is far outside the band
        )
        row = _derive_features("BTC/USDT", 3000, b)
        self.assertAlmostEqual(row["bid_depth_1pct"], 10.0)
        self.assertAlmostEqual(row["ask_depth_1pct"], 10.0)

    def test_empty_book_returns_none(self):
        self.assertIsNone(_derive_features("BTC/USDT", 1000, book(bids=[], asks=[])))

    def test_one_sided_book_returns_none(self):
        self.assertIsNone(_derive_features("BTC/USDT", 1000, book(bids=[[100.0, 1.0]], asks=[])))
        self.assertIsNone(_derive_features("BTC/USDT", 1000, book(bids=[], asks=[[100.0, 1.0]])))

    def test_zero_spread_book(self):
        b = book(bids=[[100.0, 5.0]], asks=[[100.0, 5.0]])
        row = _derive_features("BTC/USDT", 1000, b)
        self.assertIsNotNone(row)
        self.assertEqual(row["spread_bps"], 0.0)
        self.assertAlmostEqual(row["mid_price"], 100.0)

    def test_crossed_book_returns_none(self):
        """Best bid above best ask should never happen; treat as unusable
        rather than emit a nonsensical negative spread."""
        b = book(bids=[[101.0, 1.0]], asks=[[100.0, 1.0]])
        self.assertIsNone(_derive_features("BTC/USDT", 1000, b))

    def test_zero_or_negative_price_returns_none(self):
        self.assertIsNone(_derive_features("BTC/USDT", 1000, book(bids=[[0.0, 1.0]], asks=[[1.0, 1.0]])))

    def test_no_depth_within_band_gives_zero_imbalance_not_error(self):
        """All liquidity sits outside the 1% band on both sides -- depth
        sums are both zero; imbalance must not divide by zero."""
        b = book(bids=[[50.0, 10.0]], asks=[[150.0, 10.0]])
        row = _derive_features("BTC/USDT", 1000, b)
        self.assertIsNotNone(row)
        self.assertEqual(row["bid_depth_1pct"], 0.0)
        self.assertEqual(row["ask_depth_1pct"], 0.0)
        self.assertEqual(row["imbalance_ratio"], 0.0)


if __name__ == "__main__":
    unittest.main()
