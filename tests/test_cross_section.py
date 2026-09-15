"""Safety tests for cross-sectional (relative-value) entry selection.

The point of the mode is that market beta cancels because both legs are taken.
These tests pin the ranking, the refusal conditions, and the one-legged case
that quietly turns the whole thing back into a directional book.
Network-free and DB-free.
"""

from __future__ import annotations

import unittest

from src import config
from src.engine import cross_section
from src.strategies.base import SignalDirection

LONG = SignalDirection.LONG.value
SHORT = SignalDirection.SHORT.value


def _obs(rates: dict[str, float]):
    return [{"symbol": sym, "metric": rate} for sym, rate in rates.items()]


def _spread_cohort(n: int = 10, step: float = 0.0001):
    """n names with evenly spread funding, highest first."""
    return {f"C{i:02d}/USDT": step * (n - i) for i in range(n)}


class _Storage:
    def __init__(self, rates, fail_on=()):
        self.rates = rates
        self.fail_on = set(fail_on)

    def get_latest_funding_rate(self, symbol):
        if symbol in self.fail_on:
            raise RuntimeError("boom")
        if symbol not in self.rates:
            return None
        return {"funding_rate": self.rates[symbol]}


class ModeTests(unittest.TestCase):
    def test_off_produces_no_legs(self):
        ranking = cross_section.rank_cohort(_obs(_spread_cohort()), mode="off")
        self.assertTrue(ranking.is_empty)
        self.assertEqual(ranking.skipped_reason, "mode_off")

    def test_unknown_mode_raises(self):
        with self.assertRaises(ValueError):
            cross_section.rank_cohort(_obs(_spread_cohort()), mode="vibes")

    def test_default_mode_is_off(self):
        # Turning this on must be a deliberate config change, never a default.
        self.assertEqual(config.CROSS_SECTIONAL_MODE, "off")


class RankingTests(unittest.TestCase):
    def test_highest_funding_is_shorted_and_lowest_is_longed(self):
        ranking = cross_section.rank_cohort(
            _obs(_spread_cohort(10)), mode="funding_rank"
        )
        # C00 has the highest funding (crowded longs paying) -> short it.
        self.assertEqual(ranking.direction_for("C00/USDT"), SHORT)
        # C09 has the lowest (shorts paying) -> long it.
        self.assertEqual(ranking.direction_for("C09/USDT"), LONG)

    def test_middle_of_the_cohort_is_not_traded(self):
        ranking = cross_section.rank_cohort(
            _obs(_spread_cohort(10)), mode="funding_rank"
        )
        self.assertIsNone(ranking.direction_for("C05/USDT"))

    def test_both_legs_are_present_so_beta_cancels(self):
        ranking = cross_section.rank_cohort(
            _obs(_spread_cohort(10)), mode="funding_rank"
        )
        self.assertTrue(cross_section.beta_cancelling(ranking))
        directions = [leg.direction for leg in ranking.legs.values()]
        self.assertEqual(directions.count(SHORT), directions.count(LONG))

    def test_ties_are_broken_deterministically(self):
        # A replay must pick the same book the live cycle did.
        rates = {f"C{i}/USDT": 0.0 for i in range(10)}
        rates["HIGH/USDT"] = 0.01
        rates["LOW/USDT"] = -0.01
        first = cross_section.rank_cohort(_obs(rates), mode="funding_rank")
        second = cross_section.rank_cohort(
            _obs(dict(reversed(list(rates.items())))), mode="funding_rank"
        )
        self.assertEqual(sorted(first.legs), sorted(second.legs))
        for symbol in first.legs:
            self.assertEqual(
                first.legs[symbol].direction, second.legs[symbol].direction
            )

    def test_legs_never_overlap_on_one_name(self):
        ranking = cross_section.rank_cohort(
            _obs(_spread_cohort(8)), mode="funding_rank"
        )
        symbols = list(ranking.legs)
        self.assertEqual(len(symbols), len(set(symbols)))


class RefusalTests(unittest.TestCase):
    def test_cohort_below_minimum_is_refused(self):
        ranking = cross_section.rank_cohort(
            _obs(_spread_cohort(3)), mode="funding_rank"
        )
        self.assertTrue(ranking.is_empty)
        self.assertIn("min", ranking.skipped_reason)

    def test_flat_funding_is_refused_as_noise(self):
        # Every name at the same rate: the ranking orders nothing real.
        rates = {f"C{i:02d}/USDT": 0.0001 for i in range(12)}
        ranking = cross_section.rank_cohort(_obs(rates), mode="funding_rank")
        self.assertTrue(ranking.is_empty)
        self.assertIn("dispersion", ranking.skipped_reason)

    def test_dispersion_just_above_the_floor_is_accepted(self):
        rates = {f"C{i:02d}/USDT": 0.0 for i in range(11)}
        rates["TOP/USDT"] = config.CROSS_MIN_DISPERSION * 1.01
        ranking = cross_section.rank_cohort(_obs(rates), mode="funding_rank")
        self.assertFalse(ranking.is_empty)

    def test_malformed_observations_are_skipped(self):
        rows = _obs(_spread_cohort(10))
        rows += [{"symbol": None, "metric": 1.0}, {"symbol": "X/USDT", "metric": None}]
        rows += [{"symbol": "Y/USDT", "metric": "nan-ish"}]
        ranking = cross_section.rank_cohort(rows, mode="funding_rank")
        self.assertEqual(ranking.cohort_size, 10)


class OneLeggedTests(unittest.TestCase):
    """ALLOW_LONG=false drops the long leg, and the beta comes straight back."""

    def test_short_only_book_is_flagged_as_not_beta_cancelling(self):
        ranking = cross_section.rank_cohort(
            _obs(_spread_cohort(10)), mode="funding_rank"
        )
        short_only = cross_section.CohortRanking(
            legs={
                sym: leg
                for sym, leg in ranking.legs.items()
                if leg.direction == SHORT
            },
            cohort_size=ranking.cohort_size,
            dispersion=ranking.dispersion,
        )
        self.assertFalse(cross_section.beta_cancelling(short_only))

    def test_empty_ranking_is_not_beta_cancelling(self):
        self.assertFalse(cross_section.beta_cancelling(cross_section.CohortRanking()))


class GatherTests(unittest.TestCase):
    def test_observations_come_from_the_storage_view_the_caller_passes(self):
        storage = _Storage({"A/USDT": 0.001, "B/USDT": -0.001})
        rows = cross_section.gather_observations(
            storage, ["A/USDT", "B/USDT", "MISSING/USDT"], mode="funding_rank"
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["symbol"] for r in rows}, {"A/USDT", "B/USDT"})

    def test_one_broken_symbol_does_not_lose_the_cohort(self):
        storage = _Storage({"A/USDT": 0.001, "B/USDT": -0.001}, fail_on=["A/USDT"])
        rows = cross_section.gather_observations(
            storage, ["A/USDT", "B/USDT"], mode="funding_rank"
        )
        self.assertEqual([r["symbol"] for r in rows], ["B/USDT"])

    def test_mode_off_reads_nothing(self):
        storage = _Storage({"A/USDT": 0.001})
        self.assertEqual(
            cross_section.gather_observations(storage, ["A/USDT"], mode="off"), []
        )


class MetadataTests(unittest.TestCase):
    def test_leg_metadata_records_the_cohort_it_came_from(self):
        ranking = cross_section.rank_cohort(
            _obs(_spread_cohort(10)), mode="funding_rank"
        )
        meta = ranking.leg_for("C00/USDT").as_metadata()
        self.assertTrue(meta["cross_sectional"])
        self.assertEqual(meta["cohort_size"], 10)
        self.assertEqual(meta["cohort_rank"], 1)
        self.assertGreater(meta["cohort_dispersion"], 0)


class ResearchAxisTests(unittest.TestCase):
    def test_axis_values_match_the_modes_the_engine_accepts(self):
        import yaml
        from pathlib import Path

        space = yaml.safe_load(
            (Path(__file__).resolve().parents[1] / "research_space.yaml").read_text()
        )
        values = space["axes"]["CROSS_SECTIONAL_MODE"]
        for value in values:
            self.assertIn(value, cross_section.VALID_MODES)

    def test_axis_key_is_promotable(self):
        # An axis the promotion whitelist rejects can win and never be applied.
        from src.research.promotion import OVERRIDE_KEY_PREFIXES, OVERRIDE_KEY_EXACT

        key = "CROSS_SECTIONAL_MODE"
        self.assertTrue(
            key in OVERRIDE_KEY_EXACT or key.startswith(OVERRIDE_KEY_PREFIXES),
            f"{key} is not promotable — a winning config could never be applied",
        )


if __name__ == "__main__":
    unittest.main()
