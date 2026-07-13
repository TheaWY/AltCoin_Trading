"""Run one backtest and print pure JSON between markers.

The research runner invokes this in a SUBPROCESS with challenger config
values injected as environment variables. config.py reads env at import
time, so a subprocess is the only clean way to run many configs in one
night without polluting the parent process.

    python scripts/run_backtest_once.py --start 2024-01-01 --end 2024-03-01 \
        --symbols BTC/USDT,ETH/USDT
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

MARKER_BEGIN = "===RESULT_JSON_BEGIN==="
MARKER_END = "===RESULT_JSON_END==="


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--strategy", default=None)
    args = parser.parse_args()

    # Import AFTER env is set by the parent — config reads env at import.
    from scripts.backtest import BacktestEngine, _parse_datetime, _parse_symbols  # noqa: E402
    from src import config  # noqa: E402

    result = BacktestEngine(
        start=_parse_datetime(args.start),
        end=_parse_datetime(args.end),
        symbols=_parse_symbols(args.symbols),
        strategy_name=args.strategy or config.PRIMARY_STRATEGY,
    ).run()

    trades = result.get("closed_trade_pnls", [])
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    daily: dict[str, float] = {}
    for t in trades:
        closed = t.get("closed_at")
        if closed:
            import datetime as _dt
            day = _dt.datetime.fromtimestamp(int(closed), tz=_dt.timezone.utc).strftime("%Y-%m-%d")
            daily[day] = round(daily.get(day, 0.0) + t["pnl"], 4)
    total_fees = sum(t["fees"] for t in trades)
    total_slippage_cost = sum(t.get("slippage_cost", 0.0) for t in trades)
    compact = {
        "trade_count": len(pnls),
        "total_pnl": round(sum(pnls), 4),
        "expectancy": round(sum(pnls) / len(pnls), 6) if pnls else 0.0,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 4)
        if losses and sum(losses) != 0 else (999.0 if wins else 0.0),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else 0.0,
        "total_fees": round(total_fees, 4),
        "total_slippage_cost": round(total_slippage_cost, 4),
        # Pre-cost PnL: adds back BOTH fees and simulated execution slippage,
        # so gross_pnl - total_pnl == total_fees + total_slippage_cost exactly
        # -- the "how much of the edge did realistic costs consume" figure.
        "gross_pnl": round(sum(pnls) + total_fees + total_slippage_cost, 4),
        "max_drawdown_pct": result["summary"].get("max_drawdown_pct"),
        "portfolio_return_pct": result["summary"].get("portfolio_return_pct"),
        "category_checks": result["summary"].get("category_checks", 0),
        "category_present": result["summary"].get("category_present", 0),
        "category_coverage_pct": result["summary"].get("category_coverage_pct"),
        # for DSR (per-trade return series) and correlation gates — capped
        "trade_pnls": [round(p, 4) for p in pnls[:5000]],
        "daily_pnl": daily,
        # Per-trade symbol/exit-reason/cost detail (additive, capped like
        # trade_pnls) -- lets the sweep report slippage-as-%-of-edge broken
        # out by liquidity tier and by exit reason, without a second run.
        "trade_detail": [
            {
                "symbol": t["symbol"],
                "exit_reason": t.get("exit_reason"),
                "atr_pct": t.get("atr_pct"),
                "pnl": round(float(t["pnl"]), 6),
                "fees": round(float(t["fees"]), 6),
                "slippage_cost": round(float(t.get("slippage_cost", 0.0) or 0.0), 6),
            }
            for t in trades[:5000]
        ],
        # Market-neutral trades only (research_decisions,
        # subject='rel_strength_market_neutral'): raw per-trade hedge detail
        # so the walk-forward aggregator can compute an exact (not
        # weighted-average-of-averages) net-of-funding expectancy and
        # basis-risk split across ALL windows' trades, not just this one.
        "hedge_trades": [
            {
                "pnl": t["pnl"], "fees": t["fees"], "funding_pnl": t.get("funding_pnl"),
                "hedge_beta": t.get("hedge_beta"), "realized_beta": t.get("realized_beta"),
                "realized_correlation": t.get("realized_correlation"),
            }
            for t in trades if t.get("hedge_symbol")
        ],
    }
    print(MARKER_BEGIN)
    print(json.dumps(compact))
    print(MARKER_END)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
