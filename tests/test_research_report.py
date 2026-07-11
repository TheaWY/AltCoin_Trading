from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from src.data.storage import Storage
from src.research.report import _ensure_schema_for, build_report, trial_budget_status


def _storage() -> Storage:
    return Storage(Path(tempfile.mkdtemp()) / "research.db", database_url="")


def _metrics(trades: int, exp: float, pf: float, pos: int, windows: int = 30) -> str:
    return json.dumps(
        {
            "aggregate": {
                "trade_count": trades,
                "total_pnl": round(trades * exp, 4),
                "gross_pnl": round(abs(trades * exp) + 10.0, 4),
                "total_fees": 2.0,
                "expectancy": exp,
                "profit_factor": pf,
                "positive_windows": pos,
                "window_count": windows,
            },
            "windows": [
                {"total_pnl": 1.0 if i < pos else -1.0, "trade_count": max(1, trades // windows)}
                for i in range(windows)
            ],
        }
    )


def _insert_exp(storage: Storage, hash_: str, cfg: dict, metrics: str, champion: bool = False) -> None:
    _ensure_schema_for(storage)
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute(
            "INSERT INTO experiments "
            "(config_hash, config_json, status, priority, is_champion_baseline, created_at, finished_at, metrics_json) "
            "VALUES (?, ?, 'done', 0, ?, ?, ?, ?)",
            (
                hash_,
                json.dumps(cfg, sort_keys=True),
                1 if champion else 0,
                int(time.time()) - 100,
                int(time.time()),
                metrics,
            ),
        )


class ResearchReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_budget = os.environ.get("RESEARCH_TRIAL_BUDGET")
        os.environ["RESEARCH_TRIAL_BUDGET"] = "2"

    def tearDown(self) -> None:
        if self.old_budget is None:
            os.environ.pop("RESEARCH_TRIAL_BUDGET", None)
        else:
            os.environ["RESEARCH_TRIAL_BUDGET"] = self.old_budget

    def test_budget_exhaustion_is_reported_without_raising_budget(self):
        storage = _storage()
        _insert_exp(storage, "champ", {"ACTIVE_STRATEGY": "momentum"}, _metrics(100, 0.01, 1.1, 20), True)
        _insert_exp(
            storage,
            "fpump",
            {"ACTIVE_STRATEGY": "momentum", "SETUP_FAILED_PUMP_ENABLED": True},
            _metrics(80, 0.02, 1.3, 22),
        )
        budget = trial_budget_status(storage)
        self.assertTrue(budget["exhausted"])
        self.assertEqual(budget["budget"], 2)
        self.assertEqual(budget["tried"], 2)
        self.assertEqual(budget["remaining"], 0)
        self.assertIn("overfitting guard", budget["reason"])

    def test_report_includes_failed_pump_and_rejections(self):
        storage = _storage()
        _insert_exp(storage, "champ", {"ACTIVE_STRATEGY": "momentum"}, _metrics(100, 0.01, 1.1, 20), True)
        _insert_exp(
            storage,
            "fpump",
            {"ACTIVE_STRATEGY": "momentum", "SETUP_FAILED_PUMP_ENABLED": True},
            _metrics(80, 0.02, 1.3, 22),
        )
        _insert_exp(
            storage,
            "thin",
            {"ACTIVE_STRATEGY": "mean_reversion", "SETUP_FAILED_PUMP_ENABLED": False},
            _metrics(1, 9.0, 999.0, 1),
        )
        report = build_report(storage, limit=5)
        self.assertEqual(report["completed_analysis_label"], "완료된 3개 분석 결과")
        self.assertEqual(report["failed_pump_short"]["count"], 1)
        self.assertEqual(report["failed_pump_short"]["top"][0]["hash"], "fpump")
        self.assertTrue(any("low_trade_count" in ";".join(r["reasons"]) for r in report["rejected"]))
        self.assertTrue(report["recommended_narrowed_space"]["next_candidates"])


if __name__ == "__main__":
    unittest.main()
