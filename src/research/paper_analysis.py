"""Paper-trade analytics — mining the genuinely out-of-sample data.

Every closed paper trade was generated on data the system had never seen.
This module extracts what that data can answer today:

  calibration   — does stated confidence match realized win rate?
                  (joins paper_trades -> signals.metadata for confidence)
  exits         — pnl by exit_reason + MAE/MFE per trade computed from stored
                  hourly prices: "did stops kill trades that would have won?"
  setups        — per-strategy live scorecard, to sit next to backtest numbers
                  (large gap = overfitting suspect)
  regimes       — performance split by BTC 7d regime at entry

Honest limits: only the champion config's trades exist here; challenger
counterfactuals still require replay (fresh evals). Buckets under
MIN_SAMPLE trades print but are marked low-n.

    python -m src.research.paper_analysis            # full report
    python -m src.research.paper_analysis --json     # for the dashboard API
"""

from __future__ import annotations

import argparse
import json
import time
from typing import Any

from src.data.storage import get_storage

MIN_SAMPLE = 30


def _closed_trades() -> list[dict[str, Any]]:
    with get_storage()._connect() as conn:  # noqa: SLF001
        rows = conn.execute(
            "SELECT t.*, s.metadata AS signal_metadata FROM paper_trades t "
            "LEFT JOIN signals s ON s.id = t.signal_id "
            "WHERE t.status = 'closed' AND t.closed_at IS NOT NULL "
            "ORDER BY t.closed_at"
        ).fetchall()
        return [dict(r) for r in rows]


def _bucket_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [float(t["pnl"] or 0) for t in trades]
    wins = [p for p in pnls if p > 0]
    return {
        "n": len(pnls),
        "low_n": len(pnls) < MIN_SAMPLE,
        "win_rate": round(len(wins) / len(pnls), 3) if pnls else None,
        "expectancy": round(sum(pnls) / len(pnls), 4) if pnls else None,
        "total_pnl": round(sum(pnls), 2),
        "total_fees": round(sum(float(t["fees"] or 0) for t in trades), 2),
    }


# ---------------------------------------------------------------- calibration

def _confidence(trade: dict[str, Any]) -> float | None:
    raw = trade.get("signal_metadata")
    if not raw:
        return None
    try:
        meta = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    for key in ("confidence", "score", "final_confidence"):
        if key in meta:
            try:
                return float(meta[key])
            except (TypeError, ValueError):
                return None
    return None


def calibration(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets = [(0.0, 0.55), (0.55, 0.65), (0.65, 0.75), (0.75, 1.01)]
    out = []
    for low, high in buckets:
        grp = [t for t in trades if (c := _confidence(t)) is not None and low <= c < high]
        stats = _bucket_stats(grp)
        stats["bucket"] = f"{low:.2f}-{high:.2f}"
        stats["stated_mid"] = round((low + min(high, 1.0)) / 2, 3)
        out.append(stats)
    return out


# --------------------------------------------------------------------- exits

def _prices_between(symbol: str, start: int, end: int) -> list[dict[str, Any]]:
    return get_storage().get_prices(
        symbol, limit=100_000, since=start, before=end, timeframe="1h"
    )


def exit_autopsy(trades: list[dict[str, Any]]) -> dict[str, Any]:
    by_reason: dict[str, list[dict[str, Any]]] = {}
    mae_mfe: list[dict[str, Any]] = []
    stopped_would_have_won = 0
    stopped_total = 0

    for t in trades:
        reason = t.get("exit_reason") or "unknown"
        by_reason.setdefault(reason, []).append(t)

        prices = _prices_between(t["symbol"], int(t["opened_at"]), int(t["closed_at"]))
        if len(prices) < 2:
            continue
        entry = float(t["entry_price"])
        highs = [float(p["high"]) for p in prices]
        lows = [float(p["low"]) for p in prices]
        if t["direction"] == "SHORT":
            mfe = (entry - min(lows)) / entry
            mae = (max(highs) - entry) / entry
        else:
            mfe = (max(highs) - entry) / entry
            mae = (entry - min(lows)) / entry
        record = {
            "id": t["id"], "reason": reason,
            "mae_pct": round(mae * 100, 3), "mfe_pct": round(mfe * 100, 3),
            "pnl": round(float(t["pnl"] or 0), 2),
        }
        mae_mfe.append(record)

        # stopped-out trades: did price later reach take_profit before closed_at+72h?
        if reason and "stop" in reason.lower() and t.get("take_profit"):
            stopped_total += 1
            tp = float(t["take_profit"])
            after = _prices_between(
                t["symbol"], int(t["closed_at"]), int(t["closed_at"]) + 72 * 3600
            )
            if t["direction"] == "SHORT":
                reached = any(float(p["low"]) <= tp for p in after)
            else:
                reached = any(float(p["high"]) >= tp for p in after)
            if reached:
                stopped_would_have_won += 1

    return {
        "by_reason": {reason: _bucket_stats(ts) for reason, ts in sorted(by_reason.items())},
        "mae_mfe": mae_mfe,
        "stops_that_reached_tp_within_72h": {
            "count": stopped_would_have_won,
            "of_stopped": stopped_total,
            "note": "high ratio = stops too tight relative to targets",
        },
    }


# ---------------------------------------------------------- setups & regimes

def setup_scorecard(trades: list[dict[str, Any]]) -> dict[str, Any]:
    by_strategy: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        by_strategy.setdefault(t.get("strategy") or "unknown", []).append(t)
    return {name: _bucket_stats(ts) for name, ts in sorted(by_strategy.items())}


def _btc_regime_at(ts: int, btc_prices: dict[int, float]) -> str:
    hour = ts // 3600 * 3600
    now = btc_prices.get(hour)
    then = btc_prices.get(hour - 7 * 86400)
    if not now or not then:
        return "unknown"
    change = (now - then) / then
    return "up" if change > 0.03 else ("down" if change < -0.03 else "flat")


def regime_split(trades: list[dict[str, Any]]) -> dict[str, Any]:
    since = min((int(t["opened_at"]) for t in trades), default=int(time.time())) - 8 * 86400
    btc = {
        int(p["timestamp"]): float(p["close"])
        for p in get_storage().get_prices("BTC/USDT", limit=1_000_000, since=since, timeframe="1h")
    }
    by_regime: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        by_regime.setdefault(_btc_regime_at(int(t["opened_at"]), btc), []).append(t)
    return {reg: _bucket_stats(ts) for reg, ts in sorted(by_regime.items())}


# -------------------------------------------------------------------- report

def full_report() -> dict[str, Any]:
    trades = _closed_trades()
    if not trades:
        return {"closed_trades": 0, "note": "no closed paper trades yet"}
    return {
        "closed_trades": len(trades),
        "overall": _bucket_stats(trades),
        "calibration": calibration(trades),
        "exits": exit_autopsy(trades),
        "setups": setup_scorecard(trades),
        "regimes": regime_split(trades),
        "generated_at": int(time.time()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = full_report()
    if args.json:
        print(json.dumps(report, indent=2))
        return
    print(f"closed trades: {report.get('closed_trades', 0)}")
    for section in ("overall", "calibration", "setups", "regimes"):
        print(f"\n== {section} ==")
        print(json.dumps(report.get(section), indent=2))
    exits = report.get("exits", {})
    print("\n== exits by reason ==")
    print(json.dumps(exits.get("by_reason"), indent=2))
    print("\n== stopped trades that reached TP within 72h ==")
    print(json.dumps(exits.get("stops_that_reached_tp_within_72h"), indent=2))


if __name__ == "__main__":
    main()
