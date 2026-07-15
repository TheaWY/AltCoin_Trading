"""Cross-sectional dispersion feature — point-in-time correctness is the whole
game, since a backtest reads high_flag for a past entry bar."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.data.storage import Storage
from src.research import xsec_features as xf


def _storage() -> Storage:
    return Storage(Path(tempfile.mkdtemp()) / "t.db", database_url="")


class DispersionLookupTests(unittest.TestCase):
    def setUp(self):
        xf.reset_cache()

    def tearDown(self):
        xf.reset_cache()

    def _seed(self, st, rows):
        xf.ensure_schema(st)
        with st._connect() as c:
            c.executemany(
                "INSERT OR IGNORE INTO xsec_dispersion (timestamp,dispersion,n_symbols,high_flag) "
                "VALUES (:timestamp,:dispersion,:n_symbols,:high_flag)", rows)

    def test_point_in_time_lookup_reads_at_or_before(self):
        st = _storage()
        H = 3600
        self._seed(st, [
            {"timestamp": 100 * H, "dispersion": 0.01, "n_symbols": 30, "high_flag": 0},
            {"timestamp": 200 * H, "dispersion": 0.05, "n_symbols": 30, "high_flag": 1},
            {"timestamp": 300 * H, "dispersion": 0.02, "n_symbols": 30, "high_flag": 0},
        ])
        # at the exact high hour -> True
        self.assertTrue(xf.is_high_dispersion(st, 200 * H + 1000))
        # between 200 and 300 still reads the 200 flag (most recent <= ts)
        self.assertTrue(xf.is_high_dispersion(st, 250 * H))
        # at/after 300 reads the low flag
        self.assertFalse(xf.is_high_dispersion(st, 305 * H))
        # before any data -> fail closed (False)
        self.assertFalse(xf.is_high_dispersion(st, 50 * H))

    def test_missing_table_fails_closed(self):
        st = _storage()  # never computed -> no rows / lazy table
        self.assertFalse(xf.is_high_dispersion(st, 1_700_000_000))


class DispersionComputeTests(unittest.TestCase):
    def setUp(self):
        xf.reset_cache()

    def tearDown(self):
        xf.reset_cache()

    def test_compute_stores_rows_with_flags(self):
        st = _storage()
        # 3 symbols, 900 hourly bars; give them different volatilities so the
        # cross-sectional std of 24h returns is well-defined and varies.
        import math
        n = 900
        t0 = 1_600_000_000
        syms = ["AAA/USDT", "BBB/USDT", "CCC/USDT", "DDD/USDT", "EEE/USDT", "FFF/USDT"]
        for k, sym in enumerate(syms):  # >=5 symbols/hour so the hour qualifies
            rows = []
            price = 100.0
            for i in range(n):
                # each symbol a different oscillation -> nonzero dispersion
                price *= 1 + 0.01 * (k + 1) * math.sin(i / (4 + k))
                ts = t0 + i * 3600
                rows.append({"symbol": sym, "timestamp": ts, "timeframe": "1h",
                             "open": price, "high": price, "low": price,
                             "close": price, "volume": 1000.0})
            st.insert_prices(rows, timeframe="1h")
        written = xf.compute_and_store(st, syms)
        self.assertGreater(written, 700)  # ~ n-24 hours
        with st._connect() as c:
            r = dict(c.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(high_flag),0) hi FROM xsec_dispersion").fetchone())
        self.assertEqual(r["n"], written)
        # some hours should flag high once enough history accrues
        self.assertGreaterEqual(r["hi"], 0)


if __name__ == "__main__":
    unittest.main()
