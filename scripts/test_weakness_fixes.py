#!/usr/bin/env python3
"""Smoke checks for the conservative weakness-fix defaults.

No network calls. This verifies that the repo defaults now reflect the review:
- evaluation engine is the entry source
- low-turnover confidence/cooldown defaults
- weak/noisy setup branches disabled by default
- cash benchmark annotation works
- paper market data uses real public endpoints by default
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# Ensure environment and local promotion overrides do not mask defaults.
for key in (
    "ENTRY_DECISION_ENGINE",
    "PAPER_STARTING_CAPITAL",
    "MIN_CONFIDENCE",
    "PAPER_MIN_CONFIDENCE",
    "COOLDOWN_HOURS_PER_SYMBOL",
    "SETUP_BREAKOUT_ENABLED",
    "SETUP_TSMOM_ENABLED",
    "SETUP_VOLUME_ENABLED",
    "BINANCE_MARKET_DATA_TESTNET",
):
    os.environ.pop(key, None)
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="weakness_fix_test_")

from src import config  # noqa: E402
from scripts.annotate_backtest_benchmarks import annotate  # noqa: E402


def main() -> int:
    assert config.ENTRY_DECISION_ENGINE == "evaluation"
    assert config.PAPER_STARTING_CAPITAL == 730.0
    assert config.MIN_CONFIDENCE == 0.70
    assert config.COOLDOWN_HOURS_PER_SYMBOL == 48.0
    assert config.SETUP_BREAKOUT_ENABLED is False
    assert config.SETUP_TSMOM_ENABLED is False
    assert config.SETUP_VOLUME_ENABLED is False
    assert config.BINANCE_MARKET_DATA_TESTNET is False

    report = {
        "summary": {
            "starting_capital": 730.0,
            "final_value": 700.0,
            "return_pct": -4.11,
            "btc_buy_hold_return_pct": -20.0,
        }
    }
    annotated = annotate(report)
    assert annotated["summary"]["beats_cash"] is False
    assert annotated["benchmarks"]["strategy_vs_cash"]["absolute_edge"] == -30.0
    assert annotated["benchmarks"]["btc_buy_hold"]["strategy_return_edge_pct"] == 15.89

    print("Weakness-fix smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
