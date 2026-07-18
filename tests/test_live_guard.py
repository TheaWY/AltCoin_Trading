"""Live-strategy guard: nothing trades live unless gate-validated or explicitly
eyes-open. Pins the load-bearing behavior so the discipline can't silently
regress."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.data.storage import Storage
from src.engine import live_guard as lg


def _storage() -> Storage:
    return Storage(Path(tempfile.mkdtemp()) / "t.db", database_url="")


def _env(**overrides):
    """Clean env: every setup flag OFF + EYES_OPEN empty, then apply overrides,
    so ambient .env values (loaded at import) can't leak into the assertion."""
    base = {flag: "false" for flag in lg.SETUP_TO_STRATEGY}
    base["EYES_OPEN_STRATEGIES"] = ""
    base.update(overrides)
    return base


class LiveGuardTests(unittest.TestCase):
    def test_unvalidated_unlisted_strategy_refuses_start(self):
        st = _storage()
        with mock.patch.dict("os.environ", _env(SETUP_MEAN_REVERSION_LONG_ENABLED="true"), clear=False):
            with self.assertRaises(lg.LiveGuardError):
                lg.check_live_strategies(st)

    def test_eyes_open_override_allows_start(self):
        st = _storage()
        with mock.patch.dict("os.environ", _env(SETUP_MEAN_REVERSION_LONG_ENABLED="true",
                                                EYES_OPEN_STRATEGIES="mean_reversion_long"), clear=False):
            lg.check_live_strategies(st)  # must not raise

    def test_gate_validated_strategy_allows_start(self):
        st = _storage()
        lg.record_gate_pass(st, "funding_carry", dsr=0.97, window_frac=0.7)
        self.assertIn("funding_carry", lg.validated_strategies(st))
        with mock.patch.dict("os.environ", _env(SETUP_FUNDING_CARRY_ENABLED="true"), clear=False):
            lg.check_live_strategies(st)  # validated -> no raise

    def test_no_strategies_enabled_is_ok(self):
        st = _storage()
        with mock.patch.dict("os.environ", _env(), clear=False):
            lg.check_live_strategies(st)  # all-cash -> no raise

    def test_enabled_live_strategies_reads_env(self):
        with mock.patch.dict("os.environ", _env(SETUP_MEAN_REVERSION_LONG_ENABLED="true"), clear=False):
            got = lg.enabled_live_strategies()
            self.assertIn("mean_reversion_long", got)
            self.assertNotIn("funding_carry", got)


if __name__ == "__main__":
    unittest.main()
