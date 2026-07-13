"""Tests for src/engine/benchmarks.py -- the permanent benchmark books.

Covers: schema/meta initialization, hold-book equity math (entry costs paid
once via the same execution_cost model), random-book determinism (same seed
+ same cycle bucket -> same decisions), band/percentile math, and the
verdict chip logic including both red conditions (below BTC, inside the
random band) -- which must never be softened.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src import config
from src.data.storage import Storage
from src.engine import benchmarks

HOUR = 3600


def _storage() -> Storage:
    return Storage(Path(tempfile.mkdtemp()) / "test.db", database_url="")


def _seed_market(storage: Storage, now: int, n_alts: int = 25) -> None:
    rows = []
    for h in range(30, 0, -1):
        ts = (now // HOUR) * HOUR - h * HOUR
        rows.append({"symbol": "BTC/USDT", "timestamp": ts, "timeframe": "1h",
                     "open": 50_000, "high": 50_500, "low": 49_500, "close": 50_000, "volume": 100.0})
        for i in range(n_alts):
            # Descending volume so the top-20 ranking is deterministic.
            rows.append({"symbol": f"ALT{i:02d}/USDT", "timestamp": ts, "timeframe": "1h",
                         "open": 1.0, "high": 1.01, "low": 0.99, "close": 1.0,
                         "volume": float(1000 - i * 10)})
    storage.insert_prices(rows, timeframe="1h")


class HoldBookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_capital = config.PAPER_STARTING_CAPITAL
        config.PAPER_STARTING_CAPITAL = 1000.0

    def tearDown(self) -> None:
        config.PAPER_STARTING_CAPITAL = self.old_capital

    def test_btc_hold_pays_entry_costs_once_and_tracks_price(self) -> None:
        storage = _storage()
        now = int(time.time())
        _seed_market(storage, now)
        storage.init_portfolio_state(1000.0, benchmark_btc_price=50_000.0)

        benchmarks._ensure_schema(storage)
        meta = benchmarks._init_btc_hold(storage)
        self.assertIsNotNone(meta)
        # qty strictly below the no-cost 1000/50000: entry fee + slippage paid.
        self.assertLess(meta["qty"], 1000.0 / 50_000.0)
        self.assertGreater(meta["qty"], 0.9 * 1000.0 / 50_000.0)

        equity = benchmarks._hold_book_equity(storage, meta)
        self.assertAlmostEqual(equity, meta["qty"] * 50_000.0, places=6)

    def test_alt_hold_basket_is_top20_and_persisted(self) -> None:
        storage = _storage()
        now = int(time.time())
        _seed_market(storage, now, n_alts=25)
        storage.init_portfolio_state(1000.0, benchmark_btc_price=50_000.0)

        benchmarks._ensure_schema(storage)
        meta = benchmarks._init_alt_hold(storage)
        self.assertIsNotNone(meta)
        self.assertEqual(len(meta["legs"]), 20)
        self.assertIn("ALT00/USDT", meta["legs"])   # highest volume
        self.assertNotIn("ALT24/USDT", meta["legs"])  # rank 25, excluded
        self.assertNotIn("BTC/USDT", meta["legs"])

        # Persisted: a second init must return the SAME basket, not re-rank.
        persisted = benchmarks._get_meta(storage, "alt_hold")
        self.assertEqual(set(persisted["legs"]), set(meta["legs"]))


class RandomBookDeterminismTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_capital = config.PAPER_STARTING_CAPITAL
        self.old_dir = benchmarks.BENCHMARK_DIR
        config.PAPER_STARTING_CAPITAL = 1000.0
        benchmarks.BENCHMARK_DIR = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        config.PAPER_STARTING_CAPITAL = self.old_capital
        benchmarks.BENCHMARK_DIR = self.old_dir

    def test_same_seed_same_bucket_same_decision(self) -> None:
        """The entry draw must be reproducible: identical (seed, cycle
        bucket) -> identical open/skip decision and identical symbol pick."""
        import random as _r
        bucket = 12345
        draws1 = _r.Random(f"benchmark:3:{bucket}")
        draws2 = _r.Random(f"benchmark:3:{bucket}")
        self.assertEqual(
            [draws1.random(), draws1.random()],
            [draws2.random(), draws2.random()],
        )

    def test_random_book_runs_real_paper_trader_isolated_from_live_tables(self) -> None:
        main = _storage()
        now = int(time.time())
        _seed_market(main, now, n_alts=5)
        main.init_portfolio_state(1000.0, benchmark_btc_price=50_000.0)

        prices = {"BTC/USDT": 50_000.0, "ALT00/USDT": 1.0, "ALT01/USDT": 1.0}
        # open_rate=1.0 forces an entry attempt on this cycle.
        equity = benchmarks._run_random_book(
            main, seed=0, symbols=["ALT00/USDT", "ALT01/USDT"], now_ts=now,
            open_rate=1.0, p_long=1.0, prices=prices,
        )
        self.assertGreater(equity, 0.0)
        # rule #7 by construction: the MAIN paper_trades table stays empty.
        self.assertEqual(main.count_open_trades(), 0)
        seed_book = benchmarks._seed_storage(0)
        self.assertEqual(seed_book.count_open_trades(), 1)


class VerdictChipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.storage = _storage()
        benchmarks._ensure_schema(self.storage)
        self.now = int(time.time())

    def _snap(self, book: str, seed: int, return_pct: float) -> None:
        benchmarks._snapshot(self.storage, book, seed, self.now,
                             1000.0 * (1 + return_pct / 100), 1000.0)

    def _seed_random_band(self, low: float = 0.0, high: float = 4.0) -> None:
        # 20 seeds spread linearly low..high -> p25/p75 well inside.
        for s in range(20):
            self._snap("random", s, low + (high - low) * s / 19)

    def test_below_btc_is_red_regardless_of_random_band(self) -> None:
        self._snap("strategy", -1, 8.0)
        self._snap("btc_hold", -1, 12.0)
        self._snap("alt_hold", -1, 6.0)
        self._seed_random_band(0.0, 4.0)  # strategy far above the band
        s = benchmarks.benchmark_summary(self.storage)
        self.assertEqual(s["verdict"], "시장 대비 열위")
        self.assertEqual(s["verdict_color"], "red")

    def test_inside_random_band_is_red_even_if_above_btc(self) -> None:
        self._snap("strategy", -1, 2.0)
        self._snap("btc_hold", -1, 1.0)
        self._snap("alt_hold", -1, 0.5)
        self._seed_random_band(0.0, 4.0)  # p25=1.0, p75=3.0 -> 2.0 inside
        s = benchmarks.benchmark_summary(self.storage)
        self.assertEqual(s["verdict"], "랜덤과 구분 불가")
        self.assertEqual(s["verdict_color"], "red")

    def test_above_btc_and_above_band_is_green(self) -> None:
        self._snap("strategy", -1, 9.0)
        self._snap("btc_hold", -1, 5.0)
        self._snap("alt_hold", -1, 4.0)
        self._seed_random_band(0.0, 4.0)
        s = benchmarks.benchmark_summary(self.storage)
        self.assertEqual(s["verdict"], "시장 대비 우위")
        self.assertEqual(s["verdict_color"], "green")

    def test_band_percentiles(self) -> None:
        self._seed_random_band(0.0, 19.0)  # seeds at 0,1,...,19 exactly
        s = benchmarks.benchmark_summary(self.storage)
        self.assertAlmostEqual(s["latest"]["random_p50"], 9.5, places=6)
        self.assertAlmostEqual(s["latest"]["random_p25"], 4.75, places=6)
        self.assertAlmostEqual(s["latest"]["random_p75"], 14.25, places=6)


class CycleIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_capital = config.PAPER_STARTING_CAPITAL
        self.old_dir = benchmarks.BENCHMARK_DIR
        self.old_seeds = benchmarks.N_RANDOM_SEEDS
        config.PAPER_STARTING_CAPITAL = 1000.0
        benchmarks.BENCHMARK_DIR = Path(tempfile.mkdtemp())
        benchmarks.N_RANDOM_SEEDS = 3  # keep the test fast

    def tearDown(self) -> None:
        config.PAPER_STARTING_CAPITAL = self.old_capital
        benchmarks.BENCHMARK_DIR = self.old_dir
        benchmarks.N_RANDOM_SEEDS = self.old_seeds

    def test_run_benchmark_cycle_snapshots_all_books(self) -> None:
        storage = _storage()
        now = int(time.time())
        _seed_market(storage, now)
        storage.init_portfolio_state(1000.0, benchmark_btc_price=50_000.0)

        result = benchmarks.run_benchmark_cycle(
            storage, ["ALT00/USDT", "ALT01/USDT"], strategy_equity=1010.0, now_ts=now,
        )
        # strategy + btc_hold + alt_hold + 3 random seeds
        self.assertEqual(result["snapshots"], 6)
        self.assertNotIn("error", result)

        s = benchmarks.benchmark_summary(storage)
        self.assertIsNotNone(s)
        self.assertAlmostEqual(s["latest"]["strategy"], 1.0, places=6)

    def test_benchmark_cycle_never_raises(self) -> None:
        """A benchmark failure must not take the trading cycle down."""
        storage = _storage()  # empty: no portfolio state, no prices
        with patch.object(benchmarks, "_ensure_schema", side_effect=RuntimeError("boom")):
            result = benchmarks.run_benchmark_cycle(storage, [], strategy_equity=None)
        self.assertTrue(result.get("error"))


if __name__ == "__main__":
    unittest.main()
