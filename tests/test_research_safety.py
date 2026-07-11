"""Regression tests for autonomous research and promotion safety."""

from __future__ import annotations

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.research import generator, promotion, runner


class ChampionConfigTests(unittest.TestCase):
    def test_champion_uses_resolved_config_default_not_first_grid_value(self) -> None:
        axes = {"ACTIVE_STRATEGY": ["momentum", "funding_rate"]}
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ACTIVE_STRATEGY", None)
            with patch.object(generator.config, "ACTIVE_STRATEGY", "funding_rate"):
                champion = generator._champion_config(axes)
        self.assertEqual(champion["ACTIVE_STRATEGY"], "funding_rate")

    def test_environment_override_wins_and_is_type_coerced(self) -> None:
        axes = {"MIN_CONFIDENCE": [0.70, 0.80]}
        with patch.dict(os.environ, {"MIN_CONFIDENCE": "0.80"}, clear=False):
            champion = generator._champion_config(axes)
        self.assertEqual(champion["MIN_CONFIDENCE"], 0.80)
        self.assertIsInstance(champion["MIN_CONFIDENCE"], float)


class RunnerSafetyTests(unittest.TestCase):
    def test_profit_factor_uses_trade_pnls_not_window_totals(self) -> None:
        windows = [
            {
                "trade_count": 2,
                "total_pnl": 5.0,
                "gross_pnl": 5.5,
                "total_fees": 0.5,
                "trade_pnls": [10.0, -5.0],
            },
            {
                "trade_count": 2,
                "total_pnl": 1.0,
                "gross_pnl": 1.5,
                "total_fees": 0.5,
                "trade_pnls": [2.0, -1.0],
            },
        ]
        aggregate = runner._aggregate(windows)
        self.assertEqual(aggregate["profit_factor"], 2.0)
        self.assertEqual(aggregate["trade_pnl_samples"], 4)

    def test_active_strategy_override_also_sets_active_strategies(self) -> None:
        stdout = (
            f"{runner.MARKER_BEGIN}\n"
            + json.dumps({"trade_count": 0})
            + f"\n{runner.MARKER_END}\n"
        )
        completed = SimpleNamespace(returncode=0, stdout=stdout, stderr="")
        with patch.object(runner.subprocess, "run", return_value=completed) as run:
            result = runner._run_subprocess(
                {"ACTIVE_STRATEGY": "mean_reversion"},
                "2024-01-01",
                "2024-02-01",
            )
        self.assertEqual(result, {"trade_count": 0})
        self.assertEqual(run.call_args.kwargs["env"]["ACTIVE_STRATEGIES"], "mean_reversion")

    def test_nonzero_subprocess_exit_is_rejected(self) -> None:
        completed = SimpleNamespace(
            returncode=1,
            stdout=f"{runner.MARKER_BEGIN}\n{{}}\n{runner.MARKER_END}\n",
            stderr="failure",
        )
        with patch.object(runner.subprocess, "run", return_value=completed):
            self.assertIsNone(
                runner._run_subprocess({}, "2024-01-01", "2024-02-01")
            )


class PromotionSafetyTests(unittest.TestCase):
    def test_stage2_uses_one_latest_cumulative_snapshot(self) -> None:
        exp = {"config_hash": "candidate", "created_at": 1_000}
        latest = {
            "eval_start": 1_000,
            "eval_end": 1_000 + 15 * 86_400,
            "trade_count": 12,
            "expectancy": 0.25,
            "total_pnl": 3.0,
        }
        with patch.object(promotion, "_latest_fresh_eval", return_value=latest):
            with patch.object(promotion, "S2_MIN_FRESH_DAYS", 14):
                with patch.object(promotion, "S2_MIN_TRADES", 10):
                    passed, reason = promotion.stage2_pass(exp)
        self.assertTrue(passed, reason)
        self.assertIn("12 trades", reason)

    def test_stage2_rejects_negative_total_pnl_even_with_positive_expectancy(self) -> None:
        exp = {"config_hash": "candidate", "created_at": 1_000}
        latest = {
            "eval_start": 1_000,
            "eval_end": 1_000 + 15 * 86_400,
            "trade_count": 12,
            "expectancy": 0.25,
            "total_pnl": -1.0,
        }
        with patch.object(promotion, "_latest_fresh_eval", return_value=latest):
            with patch.object(promotion, "S2_MIN_FRESH_DAYS", 14):
                with patch.object(promotion, "S2_MIN_TRADES", 10):
                    passed, _ = promotion.stage2_pass(exp)
        self.assertFalse(passed)


if __name__ == "__main__":
    unittest.main()
