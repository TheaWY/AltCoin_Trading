"""Regression tests for completed-candle market-data handling."""

from __future__ import annotations

import unittest

from src.data.collectors.binance import _closed_candles


class CompletedCandleTests(unittest.TestCase):
    def test_current_hour_is_excluded_until_interval_ends(self) -> None:
        hour_ms = 3_600_000
        candles = [
            [0, 1, 2, 0.5, 1.5, 10],
            [hour_ms, 1.5, 2.5, 1, 2, 20],
        ]
        closed = _closed_candles(candles, "1h", now_ms=2 * hour_ms - 1)
        self.assertEqual(closed, [candles[0]])

    def test_candle_is_included_at_exact_close_boundary(self) -> None:
        hour_ms = 3_600_000
        candle = [hour_ms, 1.5, 2.5, 1, 2, 20]
        closed = _closed_candles([candle], "1h", now_ms=2 * hour_ms)
        self.assertEqual(closed, [candle])

    def test_unknown_timeframe_fails_closed(self) -> None:
        candle = [0, 1, 2, 0.5, 1.5, 10]
        self.assertEqual(_closed_candles([candle], "bad", now_ms=999999), [])


if __name__ == "__main__":
    unittest.main()
