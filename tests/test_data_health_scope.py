"""Health-gate scoping tests.

Encodes the 2026-07-13 incident: LINK/USDT rotated out of the active
universe, its feed went 12 days stale, and that alone froze entries for
symbols with perfectly fresh data because data_quality.verdict() checked
every symbol ever collected instead of the active universe + open positions.

Three cases, all required:
  1. stale symbol outside scope -> reported, never halts
  2. stale symbol that IS an open position -> always halts, no exception
  3. symbol newly rotated into the universe -> excluded from scope until
     48h of continuous history exists, so its (expected) sparse data can't
     halt entries either

No network, no real database -- fake storage objects only.
"""

from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from src.research import data_quality
from src.symbols import BACKFILL_MIN_HOURS, health_gate_scope, symbols_in_backfill


class _FakeStorage:
    def __init__(self, open_symbols=None, first_seen=None):
        self._open_symbols = open_symbols or []
        self._first_seen = first_seen or {}

    def get_open_trades(self):
        return [{"symbol": s} for s in self._open_symbols]

    def get_symbols_first_seen(self, symbols, timeframe="1h"):
        return {s: self._first_seen.get(s) for s in symbols}


class HealthGateScopeTests(unittest.TestCase):
    def setUp(self):
        self.now = int(time.time())

    # ---- case 1: stale symbol outside scope never halts -----------------

    def test_stale_symbol_outside_scope_is_excluded(self):
        universe = ["BTC/USDT", "ETH/USDT"]
        storage = _FakeStorage(
            first_seen={
                "BTC/USDT": self.now - 1000 * 3600,
                "ETH/USDT": self.now - 1000 * 3600,
            }
        )
        scope, _ = health_gate_scope(storage, universe)
        self.assertEqual(scope, {"BTC/USDT", "ETH/USDT"})
        self.assertNotIn("LINK/USDT", scope)  # rotated out, not in universe/positions

    # ---- case 2: open position always halts, non-negotiable -------------

    def test_open_position_always_in_scope_even_if_outside_universe(self):
        universe = ["BTC/USDT", "ETH/USDT"]
        storage = _FakeStorage(
            open_symbols=["SKL/USDT"],  # not in `universe` at all
            first_seen={
                "BTC/USDT": self.now - 1000 * 3600,
                "ETH/USDT": self.now - 1000 * 3600,
            },
        )
        scope, _ = health_gate_scope(storage, universe)
        self.assertIn("SKL/USDT", scope)

    def test_open_position_in_scope_even_if_it_looks_like_backfill(self):
        universe = ["BTC/USDT"]
        storage = _FakeStorage(
            open_symbols=["NEWCOIN/USDT"],
            first_seen={
                "BTC/USDT": self.now - 1000 * 3600,
                "NEWCOIN/USDT": self.now - 3600,  # 1h old, well within 48h
            },
        )
        scope, backfilling = health_gate_scope(storage, universe)
        self.assertIn("NEWCOIN/USDT", scope)
        self.assertNotIn("NEWCOIN/USDT", backfilling)  # positions skip the backfill check

    # ---- case 3: newly rotated-in symbol excluded until 48h --------------

    def test_newly_rotated_in_symbol_excluded_from_scope_and_flagged(self):
        universe = ["BTC/USDT", "NEWCOIN/USDT"]
        storage = _FakeStorage(
            first_seen={
                "BTC/USDT": self.now - 1000 * 3600,
                "NEWCOIN/USDT": self.now - 3600,  # 1h old
            }
        )
        scope, backfilling = health_gate_scope(storage, universe)
        self.assertIn("BTC/USDT", scope)
        self.assertNotIn("NEWCOIN/USDT", scope)
        self.assertIn("NEWCOIN/USDT", backfilling)

    def test_symbol_past_backfill_window_graduates_into_scope(self):
        universe = ["NEWCOIN/USDT"]
        storage = _FakeStorage(
            first_seen={"NEWCOIN/USDT": self.now - (BACKFILL_MIN_HOURS + 1) * 3600}
        )
        scope, backfilling = health_gate_scope(storage, universe)
        self.assertIn("NEWCOIN/USDT", scope)
        self.assertNotIn("NEWCOIN/USDT", backfilling)

    def test_symbols_in_backfill_treats_no_data_as_backfilling(self):
        storage = _FakeStorage(first_seen={"GHOST/USDT": None})
        result = symbols_in_backfill(storage, ["GHOST/USDT"])
        self.assertEqual(result, {"GHOST/USDT"})


class VerdictPartitionTests(unittest.TestCase):
    """verdict() end-to-end: reproduces the actual incident shape."""

    def test_verdict_halts_only_for_in_scope_staleness(self):
        fake_fresh = [
            {"source": "prices_1h", "symbol": "BTC/USDT", "last_ts": 1,
             "age_s": 999999, "status": "stale"},
            {"source": "prices_1h", "symbol": "LINK/USDT", "last_ts": 1,
             "age_s": 1045899, "status": "stale"},
            {"source": "prices_1h", "symbol": "ETH/USDT", "last_ts": 1,
             "age_s": 10, "status": "ok"},
        ]
        with patch("src.research.data_quality.freshness", return_value=fake_fresh), \
             patch("src.research.data_quality.ntp_offset", return_value={"ok": True}), \
             patch("src.research.data_quality.get_storage", return_value=object()), \
             patch("src.symbols.health_gate_scope",
                   return_value=({"BTC/USDT", "ETH/USDT"}, set())):
            result = data_quality.verdict()

        self.assertFalse(result["healthy"])  # BTC/USDT is in scope and stale
        self.assertEqual([s["symbol"] for s in result["stale_sources"]], ["BTC/USDT"])
        self.assertEqual([s["symbol"] for s in result["stale_out_of_scope"]], ["LINK/USDT"])

    def test_verdict_healthy_when_only_out_of_scope_symbol_is_stale(self):
        """This is the exact 2026-07-13 incident: LINK stale, out of scope,
        BTC/ETH/etc. fresh -- entries must not halt."""
        fake_fresh = [
            {"source": "prices_1h", "symbol": "LINK/USDT", "last_ts": 1,
             "age_s": 1045899, "status": "stale"},
            {"source": "prices_1h", "symbol": "BTC/USDT", "last_ts": 1,
             "age_s": 10, "status": "ok"},
        ]
        with patch("src.research.data_quality.freshness", return_value=fake_fresh), \
             patch("src.research.data_quality.ntp_offset", return_value={"ok": True}), \
             patch("src.research.data_quality.get_storage", return_value=object()), \
             patch("src.symbols.health_gate_scope", return_value=({"BTC/USDT"}, set())):
            result = data_quality.verdict()

        self.assertTrue(result["healthy"])
        self.assertEqual(result["stale_sources"], [])
        self.assertEqual([s["symbol"] for s in result["stale_out_of_scope"]], ["LINK/USDT"])

    def test_verdict_halts_for_stale_open_position_regardless_of_universe(self):
        fake_fresh = [
            {"source": "prices_1h", "symbol": "SKL/USDT", "last_ts": 1,
             "age_s": 999999, "status": "stale"},
        ]
        with patch("src.research.data_quality.freshness", return_value=fake_fresh), \
             patch("src.research.data_quality.ntp_offset", return_value={"ok": True}), \
             patch("src.research.data_quality.get_storage", return_value=object()), \
             patch("src.symbols.health_gate_scope", return_value=({"SKL/USDT"}, set())):
            result = data_quality.verdict()

        self.assertFalse(result["healthy"])
        self.assertEqual([s["symbol"] for s in result["stale_sources"]], ["SKL/USDT"])


if __name__ == "__main__":
    unittest.main()
