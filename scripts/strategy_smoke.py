#!/usr/bin/env python3
"""Smoke test for direction policy, ATR exits, scorecard, calibration, regime."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("SYMBOL_UNIVERSE", "static")

from src import config  # noqa: E402
from src.data.storage import Storage  # noqa: E402
from src.engine.calibration import build_calibration_map, calibrate_score  # noqa: E402
from src.engine.evaluation import evaluate_symbol  # noqa: E402
from src.engine.paper_trader import PaperTrader  # noqa: E402
from src.engine.regime import btc_regime, direction_blocked  # noqa: E402


def seed_prices(storage: Storage, symbol: str, base: float, drift: float, n: int = 200) -> None:
    now = int(time.time())
    rows = []
    price = base
    for i in range(n):
        price *= 1 + drift
        rows.append(
            {
                "symbol": symbol,
                "timestamp": now - (n - i) * 3600,
                "open": price * 0.999,
                "high": price * 1.01,
                "low": price * 0.99,
                "close": price,
                "volume": 1000.0,
            }
        )
    storage.insert_prices(rows, timeframe="1h")


def main() -> int:
    tmp = tempfile.mkdtemp()
    storage = Storage(db_path=Path(tmp) / "smoke.db")
    trader = PaperTrader(storage)

    seed_prices(storage, "BTC/USDT", 100000, -0.0005)  # BTC drifting down ~ -0.05%/h
    seed_prices(storage, "AAA/USDT", 10, 0.0)
    storage.insert_funding_rates(
        [{"symbol": "AAA/USDT", "timestamp": int(time.time()), "funding_rate": 0.002}]
    )

    # 1. LONG rejected by policy
    result = trader.process_signal(
        {"symbol": "AAA/USDT", "direction": "LONG", "strategy": "funding_rate"}, 10.0
    )
    assert not result["opened"] and "disabled by policy" in result["reason"], result
    print("1. LONG blocked by policy:", result["reason"])

    # 2. SHORT opens with ATR stops + volatility sizing + strategy/style recorded
    result = trader.process_signal(
        {"symbol": "AAA/USDT", "direction": "SHORT", "strategy": "funding_rate", "style": "scalp"},
        10.0,
    )
    assert result["opened"], result
    trade = storage.get_open_trade_for_symbol("AAA/USDT")
    assert trade["strategy"] == "funding_rate" and trade["style"] == "scalp", trade
    assert trade["atr_pct"] is not None and trade["stop_loss"] > 10.0 > trade["take_profit"]
    notional = trade["quantity"] * trade["entry_price"]
    print(
        f"2. SHORT opened: stop={trade['stop_loss']:.3f} tp={trade['take_profit']:.3f} "
        f"atr={trade['atr_pct']:.2f}% notional=${notional:,.0f}"
    )
    assert notional <= config.PAPER_STARTING_CAPITAL * config.MAX_POSITION_PCT + 1

    # 3. Trailing stop ratchets down for a short as price falls
    stop_before = float(trade["stop_loss"])
    trader.check_open_trades_for_symbol("AAA/USDT", 9.5)
    trade = storage.get_open_trade_for_symbol("AAA/USDT")
    if trade:  # may have take-profited depending on ATR levels
        assert float(trade["stop_loss"]) <= stop_before
        print(f"3. Trailing stop moved {stop_before:.3f} -> {trade['stop_loss']:.3f}")
        trader.check_open_trades_for_symbol("AAA/USDT", float(trade["take_profit"]) - 0.01)
    else:
        print("3. Position take-profited on the drop (trailing not needed)")

    closed = storage.get_recent_closed_trades(5)
    assert closed and closed[0]["exit_reason"] in ("take_profit", "stop_loss")
    assert closed[0]["fees"] and closed[0]["fees"] > 0
    print(f"4. Closed with reason={closed[0]['exit_reason']} fees=${closed[0]['fees']:.2f}")

    # 5. Scorecard aggregates the closed trade
    stats = storage.get_strategy_stats()
    assert stats and stats[0]["strategy"] == "funding_rate" and stats[0]["trades"] == 1
    print(f"5. Scorecard: {stats[0]['strategy']} {stats[0]['direction']} win_rate={stats[0]['win_rate_pct']}%")

    # 6. Calibration blends base score with win rate once enough samples exist
    calibration = {("funding_rate", "SHORT"): {"trades": 20, "wins": 15, "win_rate": 0.75}}
    calibrated = calibrate_score(0.6, "funding_rate", "SHORT", calibration)
    assert calibrated["calibrated"] and calibrated["score"] > 0.6
    print(f"6. Calibration: base 0.60 + 15/20 wins -> {calibrated['score']}")
    empty_cal = calibrate_score(0.6, "funding_rate", "SHORT", build_calibration_map(storage))
    assert not empty_cal["calibrated"]  # only 1 real trade < CALIBRATION_MIN_TRADES

    # 7. Regime: BTC seeded downtrend => risk_off, LONG blocked
    regime = btc_regime(storage)
    assert regime["state"] == "risk_off" and direction_blocked(regime, "LONG")
    assert not direction_blocked(regime, "SHORT")
    print(f"7. Regime: {regime['state']} — {regime['reason']}")

    # 8. Evaluation: confidence present for all; LONG setups filtered
    entry = evaluate_symbol(storage, "AAA/USDT", None, regime=regime, calibration={})
    assert "confidence" in entry and entry["confidence"] is not None
    for setup in [entry["verdict"], *entry["other_setups"]]:
        if setup:
            assert setup["direction"] != "LONG"
    print(
        f"8. Evaluation: tradable={entry['tradable']} confidence={entry['confidence']} "
        f"verdict={entry['verdict'] and entry['verdict']['direction']}"
    )

    # 9. Waiting coin still gets a graded confidence
    seed_prices(storage, "BBB/USDT", 5, 0.0001)
    entry2 = evaluate_symbol(storage, "BBB/USDT", None, regime=regime, calibration={})
    assert entry2["confidence"] is not None
    print(f"9. Waiting coin confidence: {entry2['confidence']} why_not={entry2['why_not'][:1]}")

    # 10. Confluence: flat range then a sharp breakdown -> SHORT setups align
    # (650 flat candles, then 50 candles of -1.5%/h: breaks the 20d low,
    #  turns 7d/28d momentum deeply negative, MACD down, price < SMAs)
    now = int(time.time())
    rows = []
    price = 4000.0
    for i in range(700):
        if i >= 650:
            price *= 0.985
        rows.append(
            {
                "symbol": "CCC/USDT",
                "timestamp": now - (700 - i) * 3600,
                "open": price * 0.999,
                "high": price * 1.005,
                "low": price * 0.995,
                "close": price,
                "volume": 5000.0,
            }
        )
    storage.insert_prices(rows, timeframe="1h")
    storage.insert_funding_rates(
        [{"symbol": "CCC/USDT", "timestamp": int(time.time()), "funding_rate": 0.0015}]
    )
    entry3 = evaluate_symbol(storage, "CCC/USDT", None, regime=regime, calibration={})
    assert entry3["tradable"], entry3["why_not"]
    v = entry3["verdict"]
    assert v["direction"] == "SHORT" and v["confluence"]["aligned"] >= 1, v
    print(
        f"10. Confluence: {v['strategy']} SHORT, aligned={v['confluence']['aligned']} "
        f"({v['confluence']['aligned_strategies']}), final confidence={v['score']} "
        f"(base {v['base_score']})"
    )

    print("\nAll strategy smoke tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
