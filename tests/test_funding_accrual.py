"""Safety tests for perpetual funding accrual on directional positions.

Before src/engine/funding.py, funding was applied only to the delta-neutral
carry trade. A directional swing could be held 720h — 90 settlements — and
accrue nothing, which always biased PnL optimistically. These tests are
network-free and DB-free.
"""

from __future__ import annotations

import unittest

from src import config
from src.engine import funding as funding_engine
from src.engine.paper_trader import PaperTrader

SETTLEMENT = funding_engine.SETTLEMENT_SECONDS


def _rows(rate: float, count: int, *, start: int = 0, step: int = SETTLEMENT):
    """One funding print per settlement bucket, starting at `start`."""
    return [
        {"timestamp": start + i * step, "funding_rate": rate}
        for i in range(count)
    ]


class _FundingStorage:
    """Storage stub serving a fixed funding series with since/before honored."""

    def __init__(self, rows=None):
        self.rows = rows or []

    def get_signal(self, _signal_id: int):
        return None

    def get_funding_rates(self, _symbol, limit=100, since=None, before=None):
        rows = self.rows
        if since is not None:
            rows = [r for r in rows if r["timestamp"] >= since]
        if before is not None:
            rows = [r for r in rows if r["timestamp"] <= before]
        return rows[-limit:] if limit else rows


class SettlementSumTests(unittest.TestCase):
    def test_entry_bucket_settlement_is_not_charged(self):
        # Opened inside bucket 0; only buckets 1..3 are complete after entry.
        rows = _rows(0.0001, 4)
        self.assertAlmostEqual(funding_engine.settlement_sum(rows, 0), 0.0003)

    def test_duplicate_prints_in_one_bucket_count_once(self):
        # funding_rates rows are collection-frequency, not settlement-frequency.
        rows = _rows(0.0001, 3, step=SETTLEMENT // 3)
        # All three prints land in bucket 0 (the entry bucket) -> nothing due.
        self.assertEqual(funding_engine.settlement_sum(rows, 0), 0.0)

    def test_no_rows_is_zero_not_a_guess(self):
        self.assertEqual(funding_engine.settlement_sum([], 0), 0.0)
        self.assertEqual(funding_engine.settlement_sum(None, 0), 0.0)

    def test_malformed_rows_are_skipped_not_fatal(self):
        rows = [
            {"timestamp": SETTLEMENT, "funding_rate": 0.0001},
            {"timestamp": "bad", "funding_rate": 0.0001},
            {"timestamp": 2 * SETTLEMENT},
        ]
        self.assertAlmostEqual(funding_engine.settlement_sum(rows, 0), 0.0001)


class FundingSignTests(unittest.TestCase):
    """Binance convention: funding_rate > 0 means longs pay shorts."""

    def test_long_pays_positive_funding(self):
        pnl = funding_engine.funding_pnl(_rows(0.0001, 4), "LONG", 1000.0, 0)
        self.assertAlmostEqual(pnl, -0.3)

    def test_short_receives_positive_funding(self):
        pnl = funding_engine.funding_pnl(_rows(0.0001, 4), "SHORT", 1000.0, 0)
        self.assertAlmostEqual(pnl, 0.3)

    def test_negative_funding_reverses_both_sides(self):
        self.assertAlmostEqual(
            funding_engine.funding_pnl(_rows(-0.0001, 4), "LONG", 1000.0, 0), 0.3
        )
        self.assertAlmostEqual(
            funding_engine.funding_pnl(_rows(-0.0001, 4), "SHORT", 1000.0, 0), -0.3
        )

    def test_direction_is_case_insensitive(self):
        self.assertEqual(funding_engine.direction_sign("long"), -1.0)
        self.assertEqual(funding_engine.direction_sign("SHORT"), 1.0)

    def test_unknown_direction_raises_rather_than_defaulting(self):
        # Defaulting an unknown side to short would flip the sign of a real cost.
        with self.assertRaises(ValueError):
            funding_engine.direction_sign("sideways")
        with self.assertRaises(ValueError):
            funding_engine.direction_sign(None)

    def test_month_long_hold_is_material(self):
        """A 30-day swing at 0.01%/8h costs ~0.9% of notional."""
        rows = _rows(0.0001, 91)  # 90 complete settlements = 30 days
        pnl = funding_engine.funding_pnl(rows, "LONG", 10_000.0, 0)
        self.assertAlmostEqual(pnl, -90.0, places=6)


class FetchClampTests(unittest.TestCase):
    def test_before_clamps_to_the_simulated_clock(self):
        storage = _FundingStorage(_rows(0.0001, 10))
        # Only settlements up to bucket 3 are visible at t = 3 * SETTLEMENT.
        pnl = funding_engine.fetch_funding_pnl(
            storage, "TEST/USDT", "LONG", 1000.0, 0, before=3 * SETTLEMENT
        )
        self.assertAlmostEqual(pnl, -0.3)

    def test_missing_storage_costs_nothing(self):
        self.assertEqual(
            funding_engine.fetch_funding_pnl(None, "TEST/USDT", "LONG", 1000.0, 0), 0.0
        )

    def test_storage_failure_never_breaks_the_caller(self):
        class _Broken:
            def get_funding_rates(self, *_a, **_k):
                raise RuntimeError("db down")

        self.assertEqual(
            funding_engine.fetch_funding_pnl(_Broken(), "T/USDT", "LONG", 1000.0, 0), 0.0
        )


class PaperTraderFundingTests(unittest.TestCase):
    """Funding must reach realized PnL and mark-to-market, not just carry."""

    def _trader(self, rows):
        trader = PaperTrader.__new__(PaperTrader)
        trader.storage = _FundingStorage(rows)
        return trader

    def _trade(self, direction: str):
        return {
            "symbol": "TEST/USDT",
            "direction": direction,
            "entry_price": 100.0,
            "quantity": 2.0,
            "opened_at": 0,
            "strategy": "unit_test",
            "signal_id": None,
        }

    def test_long_realized_pnl_is_reduced_by_funding(self):
        # notional 200; 3 settlements at 0.01% = 0.06 paid by the long.
        trader = self._trader(_rows(0.0001, 4))
        pnl = trader._realized_pnl(self._trade("LONG"), 110.0)
        self.assertAlmostEqual(pnl, 20.0 - 0.06)

    def test_short_realized_pnl_is_increased_by_funding(self):
        trader = self._trader(_rows(0.0001, 4))
        pnl = trader._realized_pnl(self._trade("SHORT"), 90.0)
        self.assertAlmostEqual(pnl, 20.0 + 0.06)

    def test_mark_to_market_carries_accrued_funding(self):
        trader = self._trader(_rows(0.0001, 4))
        self.assertAlmostEqual(
            trader._position_value(self._trade("LONG"), 110.0), 220.0 - 0.06
        )
        self.assertAlmostEqual(
            trader._position_value(self._trade("SHORT"), 90.0), 220.0 + 0.06
        )

    def test_no_funding_history_matches_the_old_behaviour(self):
        trader = self._trader([])
        self.assertEqual(trader._realized_pnl(self._trade("LONG"), 110.0), 20.0)
        self.assertEqual(trader._position_value(self._trade("SHORT"), 90.0), 220.0)

    def test_toggle_off_restores_funding_free_pnl(self):
        trader = self._trader(_rows(0.0001, 4))
        original = config.FUNDING_PNL_ENABLED
        config.FUNDING_PNL_ENABLED = False
        try:
            self.assertEqual(trader._realized_pnl(self._trade("LONG"), 110.0), 20.0)
        finally:
            config.FUNDING_PNL_ENABLED = original


class ResearchSpaceFitsBudgetTests(unittest.TestCase):
    """The enumerated space must fit inside the lifetime trial budget.

    If it does not, the runner stops partway and the winner is the best of an
    arbitrary prefix of the queue — the selection bias the stack exists to stop.
    """

    def test_space_is_within_budget(self):
        import yaml
        from pathlib import Path
        from src.research import runner
        from src.research.robust_stats import max_trials_for_history

        space = yaml.safe_load(
            (Path(__file__).resolve().parents[1] / "research_space.yaml").read_text()
        )
        total = 1
        for values in space["axes"].values():
            total *= len(values)

        history_years = (
            (runner.WINDOW_COUNT - 1) * runner.WINDOW_STEP_DAYS + runner.WINDOW_TEST_DAYS
        ) / 365.0
        budget = max_trials_for_history(history_years)

        self.assertLessEqual(
            total,
            budget,
            f"research_space has {total} combos but history ({history_years:.2f}y) "
            f"only supports {budget} trials — remove an axis or load more history",
        )

    def test_research_universe_matches_the_trading_universe(self):
        from src.research import runner

        research = [s.strip() for s in runner.SYMBOLS.split(",") if s.strip()]
        self.assertTrue(research, "research universe must not be empty")
        # Every researched symbol must be one the live cycle actually trades.
        self.assertTrue(
            set(research).issubset(set(config.TRADING_SYMBOLS)),
            f"research symbols outside the trading universe: "
            f"{sorted(set(research) - set(config.TRADING_SYMBOLS))}",
        )
        self.assertGreater(
            len(research), 2, "an altcoin system cannot be validated on majors alone"
        )


if __name__ == "__main__":
    unittest.main()
