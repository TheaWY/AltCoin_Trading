#!/usr/bin/env python3
"""Smoke tests for the full paper-only research strategy stack.

These tests intentionally avoid live exchange calls. They verify that all
research strategies are registered and can generate safe paper/NONE signals
through the real gather_strategy_data path.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "research_stack.db")
os.environ["DATABASE_URL"] = ""
os.environ.setdefault("LIVE_TRADING", "false")
os.environ.setdefault("PAPER_STARTING_CAPITAL", "730")
os.environ.setdefault("ALLOW_LONG", "false")
os.environ.setdefault("ALLOW_SHORT", "true")

from src.data.storage import Storage  # noqa: E402
from src.engine.signal import gather_strategy_data  # noqa: E402
from src.strategies.registry import get_strategy, list_strategies  # noqa: E402

SYMBOL = "BTC/USDT"
PAIR = "ETH/USDT"
HOUR = 3600
NOW = int(time.time()) // HOUR * HOUR


def rows(symbol: str, start_price: float, drift: float, n: int = 720) -> list[dict]:
    out = []
    price = start_price
    start = NOW - n * HOUR
    for i in range(n):
        price *= 1 + drift
        out.append(
            {
                "symbol": symbol,
                "timestamp": start + i * HOUR,
                "timeframe": "1h",
                "open": price * 0.999,
                "high": price * 1.004,
                "low": price * 0.996,
                "close": price,
                "volume": 1000.0,
            }
        )
    return out


def funding(symbol: str, rate: float, n: int = 36) -> list[dict]:
    start = NOW - n * 8 * HOUR
    return [
        {"symbol": symbol, "timestamp": start + i * 8 * HOUR, "funding_rate": rate}
        for i in range(n)
    ]


def main() -> int:
    storage = Storage(db_path=Path(_tmp) / "research_stack.sqlite")
    storage.insert_prices(rows(SYMBOL, 50_000, -0.0002))
    storage.insert_prices(rows(PAIR, 3_000, -0.0001))
    storage.insert_funding_rates(funding(SYMBOL, 0.0003))

    expected = {
        "funding_carry",
        "positioning_short",
        "grid",
        "mean_reversion",
        "donchian_breakout",
        "tsmom_28d",
        "pair_trading",
        "listing_reversion",
        "funding_rate",
        "momentum",
        "volume_spike",
    }
    registered = set(list_strategies())
    missing = sorted(expected - registered)
    assert not missing, f"missing strategies: {missing}; registered={sorted(registered)}"

    # Strategies that can run with only price/funding data should produce an ok Signal.
    runnable = [
        "funding_carry",
        "grid",
        "mean_reversion",
        "donchian_breakout",
        "tsmom_28d",
        "pair_trading",
        "listing_reversion",
        "funding_rate",
        "momentum",
        "volume_spike",
    ]
    for name in runnable:
        strategy = get_strategy(name)
        data = gather_strategy_data(storage, strategy, SYMBOL)
        missing_data = strategy.validate_data(data)
        assert not missing_data, f"{name} missing data: {missing_data}"
        sig = strategy.generate_signal(data)
        assert sig.symbol == SYMBOL, name
        assert sig.direction.value in {"LONG", "SHORT", "NONE"}, name
        print(f"[OK] {name}: {sig.direction.value} — {sig.reason[:80]}")

    # positioning_short requires enrichment data. It should report missing data until collector runs.
    pos = get_strategy("positioning_short")
    pos_data = gather_strategy_data(storage, pos, SYMBOL)
    missing_pos = pos.validate_data(pos_data)
    assert "ls_ratio_history" in missing_pos or "open_interest_history" in missing_pos
    print(f"[OK] positioning_short safely waits for enrichment: {missing_pos}")

    print("\nAll research stack smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
