import shutil
import os
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from scripts import backtest


class ReplayStorageIsolationTests(unittest.TestCase):
    def setUp(self):
        self._database_url = backtest.config.DATABASE_URL
        self._database_path = backtest.config.DATABASE_PATH
        self._allow_readonly = os.environ.pop("REPLAY_ALLOW_DATABASE_URL_READONLY", None)

    def tearDown(self):
        backtest.config.DATABASE_URL = self._database_url
        backtest.config.DATABASE_PATH = self._database_path
        if self._allow_readonly is not None:
            os.environ["REPLAY_ALLOW_DATABASE_URL_READONLY"] = self._allow_readonly
        else:
            os.environ.pop("REPLAY_ALLOW_DATABASE_URL_READONLY", None)

    def test_refuses_configured_database_url_without_replay_path(self):
        backtest.config.DATABASE_URL = "postgresql://altcoin:secret@localhost:5432/altcoin_trading"
        with self.assertRaises(SystemExit):
            backtest.resolve_replay_storage()

    def test_refuses_explicit_live_sqlite_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "trading.db"
            backtest.config.DATABASE_URL = ""
            backtest.config.DATABASE_PATH = live
            with self.assertRaises(SystemExit):
                backtest.resolve_replay_storage(str(live))

    def test_allows_separate_replay_sqlite_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "trading.db"
            replay = Path(tmp) / "replay.db"
            backtest.config.DATABASE_URL = ""
            backtest.config.DATABASE_PATH = live
            storage = backtest.resolve_replay_storage(str(replay))
            self.assertFalse(storage.is_postgres)
            self.assertEqual(storage.db_path.resolve(), replay.resolve())

    def test_research_runner_can_opt_into_configured_db_readonly(self):
        backtest.config.DATABASE_URL = "postgresql://altcoin:secret@localhost:5432/altcoin_trading"
        os.environ["REPLAY_ALLOW_DATABASE_URL_READONLY"] = "1"
        with mock.patch.object(backtest, "get_storage", return_value="readonly-storage"):
            self.assertEqual(backtest.resolve_replay_storage(), "readonly-storage")


class DashboardPriceFormatTests(unittest.TestCase):
    def test_usd_formatter_keeps_leading_zero_for_sub_dollar_prices(self):
        if shutil.which("node") is None:
            self.skipTest("node is not installed")
        source = Path("src/dashboard/index.html").read_text(encoding="utf-8")
        start = source.index("const KRW_RATE")
        end = source.index("const pct=")
        js = source[start:end] + "console.log(usd(0.4304));"
        result = subprocess.run(
            ["node", "-e", js],
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), "$0.4304")


class StorageSmokeSafetyTests(unittest.TestCase):
    def test_postgres_smoke_refuses_normal_database_url(self):
        env = {
            **os.environ,
            "DATABASE_URL": "postgresql://altcoin:secret@localhost:5432/altcoin_trading",
            "STORAGE_SMOKE_POSTGRES": "1",
        }
        env.pop("STORAGE_SMOKE_DATABASE_URL", None)
        env.pop("STORAGE_SMOKE_ALLOW_DISPOSABLE_POSTGRES", None)
        result = subprocess.run(
            [".venv/bin/python", "scripts/storage_smoke.py"],
            text=True,
            capture_output=True,
            env=env,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("STORAGE_SMOKE_DATABASE_URL", result.stdout)


if __name__ == "__main__":
    unittest.main()
