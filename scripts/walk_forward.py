#!/usr/bin/env python3
"""Walk-forward parameter validation for trading strategies.

Splits history into rolling train/test windows. For each fold the small
parameter grid is tuned on the train window, then the best combo is evaluated
out-of-sample on the following test window. Only the out-of-sample results
matter — if OOS returns are much worse than train returns, the parameters are
overfit and should not be trusted.

Usage:
    python scripts/walk_forward.py --strategy funding_rate --train-days 30 --test-days 10
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src import config  # noqa: E402

from backtest import BacktestEngine, _parse_datetime, _parse_symbols  # noqa: E402

# Per-strategy tuning grids: config attribute -> candidate values.
PARAM_GRIDS: dict[str, dict[str, list[Any]]] = {
    "funding_rate": {
        "FUNDING_RATE_SHORT_THRESHOLD": [0.0005, 0.001, 0.002],
        "FUNDING_RATE_LONG_THRESHOLD": [-0.0003, -0.0005, -0.001],
    },
    "momentum": {
        "MOMENTUM_ENTRY_PCT": [2.0, 3.0, 5.0],
        "MOMENTUM_7D_STRONG_PCT": [3.0, 5.0, 8.0],
    },
    "volume_spike": {
        "VOLUME_SPIKE_RATIO": [1.5, 2.0, 3.0],
    },
}


def _combos(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    keys = list(grid)
    return [dict(zip(keys, values)) for values in itertools.product(*grid.values())]


def _score(summary: dict[str, Any]) -> float:
    """Rank parameter combos: return penalized by drawdown, 0 trades disqualified."""
    if not summary.get("closed_trades"):
        return float("-inf")
    ret = float(summary.get("portfolio_return_pct") or 0.0)
    max_dd = float(summary.get("max_drawdown_pct") or 0.0)
    return ret - 0.5 * max_dd


def _run_window(
    start: datetime,
    end: datetime,
    symbols: list[str],
    strategy: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    saved = {key: getattr(config, key) for key in params}
    try:
        for key, value in params.items():
            setattr(config, key, value)
        result = BacktestEngine(
            start=start, end=end, symbols=symbols, strategy_name=strategy
        ).run()
        return result["summary"]
    finally:
        for key, value in saved.items():
            setattr(config, key, value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Walk-forward strategy validation.")
    parser.add_argument("--start", default=None, help="History start (default: 120d ago)")
    parser.add_argument("--end", default=None, help="History end (default: now)")
    parser.add_argument("--symbols", default=None, help="Comma-separated spot symbols")
    parser.add_argument("--strategy", default=config.PRIMARY_STRATEGY)
    parser.add_argument("--train-days", type=int, default=30)
    parser.add_argument("--test-days", type=int, default=10)
    args = parser.parse_args()

    end = _parse_datetime(args.end) if args.end else datetime.now(timezone.utc)
    start = (
        _parse_datetime(args.start)
        if args.start
        else end - timedelta(days=120)
    )
    symbols = _parse_symbols(args.symbols)
    grid = PARAM_GRIDS.get(args.strategy)
    if not grid:
        print(f"No parameter grid defined for strategy '{args.strategy}'")
        return 1

    combos = _combos(grid)
    folds = []
    train_td = timedelta(days=args.train_days)
    test_td = timedelta(days=args.test_days)
    cursor = start
    while cursor + train_td + test_td <= end:
        folds.append(
            {
                "train": (cursor, cursor + train_td),
                "test": (cursor + train_td, cursor + train_td + test_td),
            }
        )
        cursor += test_td

    if not folds:
        print("Not enough history for one train+test fold — collect more data first.")
        return 1

    print(
        f"Walk-forward: {len(folds)} folds x {len(combos)} combos, "
        f"strategy={args.strategy}, symbols={len(symbols)}"
    )
    report = []
    for index, fold in enumerate(folds, 1):
        train_start, train_end = fold["train"]
        test_start, test_end = fold["test"]

        best_params, best_score, best_train = None, float("-inf"), None
        for params in combos:
            summary = _run_window(train_start, train_end, symbols, args.strategy, params)
            score = _score(summary)
            if score > best_score:
                best_params, best_score, best_train = params, score, summary

        if best_params is None:
            print(f"fold {index}: no combo produced trades in train window — skipped")
            continue

        oos = _run_window(test_start, test_end, symbols, args.strategy, best_params)
        entry = {
            "fold": index,
            "train": [train_start.isoformat(), train_end.isoformat()],
            "test": [test_start.isoformat(), test_end.isoformat()],
            "best_params": best_params,
            "train_return_pct": best_train.get("portfolio_return_pct"),
            "oos_return_pct": oos.get("portfolio_return_pct"),
            "oos_max_drawdown_pct": oos.get("max_drawdown_pct"),
            "oos_trades": oos.get("closed_trades"),
        }
        report.append(entry)
        print(
            f"fold {index}: params={best_params} "
            f"train={entry['train_return_pct']}% oos={entry['oos_return_pct']}% "
            f"(dd {entry['oos_max_drawdown_pct']}%, {entry['oos_trades']} trades)"
        )

    if report:
        oos_returns = [float(e["oos_return_pct"] or 0.0) for e in report]
        avg_oos = sum(oos_returns) / len(oos_returns)
        print(f"\nOOS average return: {avg_oos:.2f}% across {len(report)} folds")
        print(
            "Interpretation: OOS positive and close to train = robust; "
            "train >> OOS = overfit, keep current defaults."
        )

    output_path = PROJECT_ROOT / "data" / f"walk_forward_{datetime.now(timezone.utc):%Y%m%d}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
