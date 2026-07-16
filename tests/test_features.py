"""Point-in-time feature store tests. The PIT guard (ascending rows) and the
candle-shape / order-flow math are the load-bearing parts."""

from __future__ import annotations

import math
import unittest

from src.research import features


def _rows(n=800, start=1_600_000_000, base=100.0):
    """Deterministic ascending 1h candles with order-flow fields."""
    out = []
    price = base
    for i in range(n):
        price *= 1 + 0.001 * math.sin(i / 7.0)
        o = price
        c = price * (1 + 0.002 * math.cos(i / 5.0))
        hi = max(o, c) * 1.003
        lo = min(o, c) * 0.997
        vol = 1000.0 + 50 * (i % 10)
        out.append({
            "timestamp": start + i * 3600, "open": o, "high": hi, "low": lo,
            "close": c, "volume": vol,
            "taker_buy_base": vol * 0.55, "num_trades": 200 + i % 50,
            "quote_volume": c * vol,
        })
    return out


class ComputeFeaturesTests(unittest.TestCase):
    def test_returns_all_added_features_plus_indicators(self):
        rows = _rows()
        feats = features.compute_features(rows)
        for name in features.ADDED_FEATURES:
            self.assertIn(name, feats, f"missing feature {name}")
        # a few from indicators.compute_all must also be present (one source of truth)
        for name in ("pct_24h", "rsi_14", "atr_pct", "realized_vol_7d"):
            self.assertIn(name, feats)
        # >= 24 (indicators) + 35 (added) distinct feature keys
        self.assertGreaterEqual(len(feats), 50)

    def test_pit_guard_rejects_descending_rows(self):
        rows = _rows(n=100)
        rows_desc = list(reversed(rows))
        with self.assertRaises(AssertionError):
            features.compute_features(rows_desc)

    def test_candle_shape_math(self):
        rows = [
            {"timestamp": 0, "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1},
            {"timestamp": 3600, "open": 100, "high": 110, "low": 90, "close": 95, "volume": 1},
        ]
        s = features.candle_shape(rows)
        # range 20, body |95-100|=5 -> 0.25; close pos (95-90)/20 = 0.25
        self.assertAlmostEqual(s["body_pct"], 0.25)
        self.assertAlmostEqual(s["close_position_in_range"], 0.25)
        self.assertAlmostEqual(s["upper_wick_pct"], (110 - 100) / 20)   # upper wick above max(o,c)=100
        self.assertAlmostEqual(s["lower_wick_pct"], (95 - 90) / 20)     # lower wick below min(o,c)=95

    def test_order_flow_imbalance(self):
        rows = _rows(n=50)
        vf = features.volume_features(rows)
        # taker_buy_base = 0.55*vol -> OFI = 2*0.55 - 1 = 0.10
        self.assertAlmostEqual(vf["order_flow_imbalance"], 0.10, places=6)

    def test_order_flow_none_on_pre_a2_bars(self):
        rows = _rows(n=50)
        for r in rows:  # simulate historical bars without order flow
            r.pop("taker_buy_base"); r.pop("num_trades")
        vf = features.volume_features(rows)
        self.assertIsNone(vf["order_flow_imbalance"])
        self.assertIsNone(vf["avg_trade_size"])

    def test_time_features_cyclical(self):
        rows = [{"timestamp": 0, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]
        t = features.time_features(rows)
        self.assertEqual(t["hour_of_day"], 0.0)
        self.assertEqual(t["day_of_week"], 3.0)   # 1970-01-01 = Thursday -> 3 (0=Mon)
        self.assertAlmostEqual(t["hour_sin"], 0.0)
        self.assertAlmostEqual(t["hour_cos"], 1.0)


if __name__ == "__main__":
    unittest.main()
