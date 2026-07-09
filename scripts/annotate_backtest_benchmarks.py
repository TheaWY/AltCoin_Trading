#!/usr/bin/env python3
"""Annotate an existing backtest JSON with cash/no-trade benchmarks.

Use this on Mac Mini after any backtest run:

    python scripts/annotate_backtest_benchmarks.py data/backtest_20260709.json --write

The goal is to stop accepting "lost less than BTC" as sufficient. A strategy
must beat cash/no-trade in absolute terms before it is considered useful.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _nested(report: dict[str, Any], *keys: str) -> Any:
    cur: Any = report
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def annotate(report: dict[str, Any]) -> dict[str, Any]:
    summary = report.setdefault("summary", {})
    start = float(summary.get("starting_capital") or summary.get("start_capital") or 0.0)
    final = float(summary.get("final_value") or 0.0)
    strategy_return = summary.get("return_pct")
    if strategy_return is None:
        strategy_return = summary.get("portfolio_return_pct")
    if strategy_return is None and start:
        strategy_return = (final - start) / start * 100.0

    btc_return = summary.get("btc_buy_hold_return_pct")
    cash_final = start
    cash_return = 0.0
    edge_vs_cash = final - cash_final if start else None

    benchmarks = {
        "cash_no_trade": {
            "final_value": round(cash_final, 2),
            "return_pct": cash_return,
            "description": "No-trade cash benchmark. Strategy must beat this before live capital.",
        },
        "strategy_vs_cash": {
            "absolute_edge": round(edge_vs_cash, 2) if edge_vs_cash is not None else None,
            "return_edge_pct": round(float(strategy_return or 0.0) - cash_return, 2)
            if strategy_return is not None
            else None,
            "beats_cash": bool(edge_vs_cash is not None and edge_vs_cash > 0),
        },
    }
    if btc_return is not None:
        benchmarks["btc_buy_hold"] = {
            "return_pct": btc_return,
            "strategy_return_edge_pct": round(float(strategy_return or 0.0) - float(btc_return), 2)
            if strategy_return is not None
            else None,
        }

    report["benchmarks"] = benchmarks
    summary["cash_no_trade_return_pct"] = 0.0
    summary["beats_cash"] = benchmarks["strategy_vs_cash"]["beats_cash"]
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Backtest JSON path")
    parser.add_argument("--write", action="store_true", help="Overwrite the JSON file with benchmarks attached")
    args = parser.parse_args()

    path = Path(args.path)
    report = json.loads(path.read_text(encoding="utf-8"))
    annotated = annotate(report)
    print(json.dumps(annotated.get("benchmarks", {}), indent=2, ensure_ascii=False))
    if args.write:
        path.write_text(json.dumps(annotated, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Updated {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
