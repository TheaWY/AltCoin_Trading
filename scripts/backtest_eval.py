#!/usr/bin/env python3
"""Backtest the *current* strategy stack against historical data.

Unlike scripts/backtest.py (single legacy strategy), this replays the exact
production decision path hour by hour:

  evaluate_symbol()  — all setups (funding, mean-rev, breakout, tsmom, swing,
                       volume), gates, BTC regime filter, direction policy,
                       confluence scoring, empirical calibration
  PaperTrader        — ATR stops/targets, trailing stops, time stops,
                       volatility-inverse sizing, fees + slippage

Data comes from a SQLite DB produced by scripts/fetch_history.py.

Usage:
    python scripts/fetch_history.py --months 12
    python scripts/backtest_eval.py --db data/backtest.db
    ALLOW_LONG=true python scripts/backtest_eval.py --db data/backtest.db
"""

from __future__ import annotations

import argparse
import bisect
import json
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src import config  # noqa: E402
from src.data.storage import Storage  # noqa: E402
from src.engine.calibration import build_calibration_map  # noqa: E402
from src.engine.evaluation import evaluate_symbol  # noqa: E402
from src.engine.paper_trader import PaperTrader  # noqa: E402
from src.engine.regime import btc_regime  # noqa: E402

HOUR = 3600
WARMUP_CANDLES = 720  # evaluation needs 30d of 1h history
STYLE_MAP = {"단타": "scalp", "스윙": "swing"}


class ReplayStorage:
    """Storage facade for backtests.

    Price/funding reads come from in-memory history clamped to the replay
    clock `now` (no look-ahead); trade/portfolio writes go to a scratch
    Storage so the production PaperTrader runs unmodified.
    """

    def __init__(self, history: Storage, trade_db: Path, symbols: list[str]) -> None:
        self.trades = Storage(db_path=trade_db)
        self.now = 0
        self._prices: dict[str, list[dict[str, Any]]] = {}
        self._price_ts: dict[str, list[int]] = {}
        self._funding: dict[str, list[dict[str, Any]]] = {}
        self._funding_ts: dict[str, list[int]] = {}
        for symbol in symbols:
            rows = history.get_prices(symbol, limit=1_000_000, since=0)
            self._prices[symbol] = rows
            self._price_ts[symbol] = [int(r["timestamp"]) for r in rows]
            frows = history.get_funding_rates(symbol, limit=1_000_000, since=0)
            self._funding[symbol] = frows
            self._funding_ts[symbol] = [int(r["timestamp"]) for r in frows]

    # --- reads (time-clamped) ---

    def _visible(self, symbol: str, before: int | None) -> int:
        """Index one past the last visible row for the current clock."""
        cutoff = self.now if before is None else min(self.now, before)
        return bisect.bisect_right(self._price_ts.get(symbol, []), cutoff)

    def get_prices(
        self,
        symbol: str,
        limit: int = 100,
        since: int | None = None,
        before: int | None = None,
        timeframe: str = "1h",
    ) -> list[dict[str, Any]]:
        rows = self._prices.get(symbol, [])
        end = self._visible(symbol, before)
        start = 0
        if since is not None:
            start = bisect.bisect_left(self._price_ts[symbol], since)
            return rows[start : min(end, start + limit)]
        return rows[max(start, end - limit) : end]

    def get_latest_price(self, symbol: str, timeframe: str = "1h") -> dict[str, Any] | None:
        end = self._visible(symbol, None)
        return self._prices[symbol][end - 1] if end else None

    def get_latest_funding_rate(self, symbol: str) -> dict[str, Any] | None:
        idx = bisect.bisect_right(self._funding_ts.get(symbol, []), self.now)
        return self._funding[symbol][idx - 1] if idx else None

    def get_volume_stats(
        self, symbol: str, timeframe: str = "1h", lookback: int = 24
    ) -> dict[str, Any]:
        import math

        rows = self.get_prices(symbol, limit=lookback)
        volumes = [float(r["volume"]) for r in rows]
        if not volumes:
            return {"avg": None, "stddev": None, "count": 0}
        avg = sum(volumes) / len(volumes)
        var = sum((v - avg) ** 2 for v in volumes) / len(volumes)
        return {"avg": avg, "stddev": math.sqrt(var), "count": len(volumes)}

    def get_latest_market_metrics(self, symbol: str) -> None:
        return None  # OI/LSR archives are not replayed

    # --- trade/portfolio ops (forwarded to scratch storage) ---

    def __getattr__(self, name: str) -> Any:
        return getattr(self.trades, name)


class ReplayTrader(PaperTrader):
    """PaperTrader on replay time."""

    def _now_ts(self) -> int:
        return self.storage.now


def candle_at(storage: ReplayStorage, symbol: str, ts: int) -> dict[str, Any] | None:
    idx = bisect.bisect_left(storage._price_ts.get(symbol, []), ts)
    ts_list = storage._price_ts.get(symbol, [])
    if idx < len(ts_list) and ts_list[idx] == ts:
        return storage._prices[symbol][idx]
    return None


def process_exits(trader: ReplayTrader, storage: ReplayStorage, ts: int) -> None:
    """Candle-aware exit handling, conservative ordering.

    Live trading checks prices every few minutes; with 1h candles we check the
    adverse extreme first (stop-loss priority when both stop and target lie
    inside one candle), then the favorable extreme (take-profit + trailing
    ratchet), matching the standard conservative backtest convention.
    """
    for trade in storage.trades.get_open_trades():
        symbol = trade["symbol"]
        candle = candle_at(storage, symbol, ts)
        if not candle:
            continue
        is_long = trade["direction"] == "LONG"
        low, high = float(candle["low"]), float(candle["high"])
        stop = float(trade["stop_loss"])
        target = float(trade["take_profit"])
        # Clamp to the trigger level so fills happen at the stop/target price
        # (slippage is charged separately), not at the candle wick extreme.
        if is_long:
            adverse = max(low, stop) if low <= stop else low
            favorable = min(high, target) if high >= target else high
        else:
            adverse = min(high, stop) if high >= stop else high
            favorable = max(low, target) if low <= target else low
        closed = trader.check_open_trades_for_symbol(symbol, adverse)
        if not closed:
            closed = trader.check_open_trades_for_symbol(symbol, favorable)
        if not closed:
            trader.check_open_trades_for_symbol(symbol, float(candle["close"]))


def run_backtest(
    history_db: Path,
    symbols: list[str],
    eval_every_hours: int = 1,
    quiet: bool = False,
) -> dict[str, Any]:
    history = Storage(db_path=history_db)
    scratch = Path(tempfile.mkdtemp()) / "replay_trades.db"
    storage = ReplayStorage(history, scratch, symbols)
    trader = ReplayTrader(storage)

    btc_ts = storage._price_ts.get(config.SYMBOL, [])
    if len(btc_ts) <= WARMUP_CANDLES:
        raise SystemExit(f"Not enough history: {len(btc_ts)} BTC candles")
    start_ts, end_ts = btc_ts[WARMUP_CANDLES], btc_ts[-1]

    calibration: dict = {}
    equity_curve: list[tuple[int, float]] = []
    evaluations = 0
    entries_attempted = 0

    ts = start_ts
    step = 0
    while ts <= end_ts:
        storage.now = ts

        process_exits(trader, storage, ts)

        if step % eval_every_hours == 0:
            if step % 24 == 0:  # daily, like live calibration refreshing
                calibration = build_calibration_map(storage.trades)
            regime = btc_regime(storage)
            btc_rows = storage.get_prices(config.SYMBOL, limit=720)

            candidates = []
            for symbol in symbols:
                if storage.trades.get_open_trade_for_symbol(symbol):
                    continue
                result = evaluate_symbol(storage, symbol, btc_rows, regime, calibration)
                evaluations += 1
                if result["tradable"] and result["verdict"]:
                    candidates.append(result)

            candidates.sort(key=lambda r: -(r["verdict"]["score"] or 0))
            for result in candidates:
                if storage.trades.count_open_trades() >= config.MAX_OPEN_POSITIONS:
                    break
                verdict = result["verdict"]
                price_row = storage.get_latest_price(result["symbol"])
                if not price_row:
                    continue
                entries_attempted += 1
                trader.process_signal(
                    {
                        "symbol": result["symbol"],
                        "direction": verdict["direction"],
                        "strategy": verdict["strategy"],
                        "style": STYLE_MAP.get(verdict["style"], "swing"),
                        "signal_id": None,
                    },
                    float(price_row["close"]),
                )

        if step % 24 == 0:
            prices = {
                s: float(r["close"])
                for s in symbols
                if (r := storage.get_latest_price(s))
            }
            equity_curve.append((ts, trader.portfolio_value(prices)))
            if not quiet and step % (24 * 30) == 0:
                day = datetime.fromtimestamp(ts, tz=timezone.utc).date()
                print(f"  ... {day} equity={equity_curve[-1][1]:.0f}")

        ts += HOUR
        step += 1

    # Close whatever is still open at the last candle close.
    storage.now = end_ts
    for trade in storage.trades.get_open_trades():
        row = storage.get_latest_price(trade["symbol"])
        if row:
            trader._close_trade(trade, float(row["close"]), "end_of_backtest")

    final_prices = {
        s: float(r["close"]) for s in symbols if (r := storage.get_latest_price(s))
    }
    return build_report(
        storage, trader, symbols, start_ts, end_ts, equity_curve, final_prices,
        evaluations, entries_attempted,
    )


def build_report(
    storage: ReplayStorage,
    trader: ReplayTrader,
    symbols: list[str],
    start_ts: int,
    end_ts: int,
    equity_curve: list[tuple[int, float]],
    final_prices: dict[str, float],
    evaluations: int,
    entries_attempted: int,
) -> dict[str, Any]:
    trades = [
        t for t in storage.trades.get_recent_trades(100_000) if t["status"] == "closed"
    ]
    final_value = trader.portfolio_value(final_prices)
    start_cap = config.PAPER_STARTING_CAPITAL

    wins = [t for t in trades if float(t["pnl"] or 0) > 0]
    losses = [t for t in trades if float(t["pnl"] or 0) <= 0]
    gross_win = sum(float(t["pnl"]) for t in wins)
    gross_loss = abs(sum(float(t["pnl"]) for t in losses))
    hold_hours = [
        (float(t["closed_at"]) - float(t["opened_at"])) / 3600
        for t in trades
        if t.get("closed_at") and t.get("opened_at")
    ]

    peak, max_dd = 0.0, 0.0
    for _, value in equity_curve:
        peak = max(peak, value)
        if peak > 0:
            max_dd = max(max_dd, (peak - value) / peak * 100)

    def bucket(key_fn) -> dict[str, dict[str, Any]]:
        groups: dict[str, list] = defaultdict(list)
        for t in trades:
            groups[str(key_fn(t))].append(t)
        out = {}
        for key, group in sorted(groups.items()):
            group_wins = sum(1 for t in group if float(t["pnl"] or 0) > 0)
            out[key] = {
                "trades": len(group),
                "wins": group_wins,
                "win_rate_pct": round(group_wins / len(group) * 100, 1),
                "total_pnl": round(sum(float(t["pnl"] or 0) for t in group), 2),
            }
        return out

    btc_rows = storage._prices.get(config.SYMBOL, [])
    btc_window = [
        r for r in btc_rows if start_ts <= int(r["timestamp"]) <= end_ts
    ]
    btc_hold = (
        round(
            (float(btc_window[-1]["close"]) - float(btc_window[0]["close"]))
            / float(btc_window[0]["close"]) * 100, 2,
        )
        if len(btc_window) >= 2
        else None
    )

    monthly: dict[str, float] = {}
    prev_value = start_cap
    for ts, value in equity_curve:
        month = f"{datetime.fromtimestamp(ts, tz=timezone.utc):%Y-%m}"
        monthly.setdefault(month, prev_value)
        monthly[month] = value  # last equity point of the month (overwritten)
        prev_value = value
    month_keys = sorted(monthly)
    monthly_returns = {}
    prev = start_cap
    for month in month_keys:
        monthly_returns[month] = round((monthly[month] - prev) / prev * 100, 2)
        prev = monthly[month]

    return {
        "params": {
            "start": datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat(),
            "end": datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat(),
            "symbols": symbols,
            "allow_long": config.ALLOW_LONG,
            "allow_short": config.ALLOW_SHORT,
            "regime_filter": config.REGIME_FILTER_ENABLED,
            "atr_stop_mult": config.ATR_STOP_MULT,
            "atr_tp_mult": config.ATR_TP_MULT,
            "risk_per_trade_pct": config.RISK_PER_TRADE_PCT,
            "fees_slippage_round_trip_pct": round(
                2 * (config.FEE_PCT_PER_SIDE + config.SLIPPAGE_PCT_PER_SIDE) * 100, 4
            ),
        },
        "summary": {
            "starting_capital": start_cap,
            "final_value": round(final_value, 2),
            "return_pct": round((final_value - start_cap) / start_cap * 100, 2),
            "btc_buy_hold_return_pct": btc_hold,
            "max_drawdown_pct": round(max_dd, 2),
            "closed_trades": len(trades),
            "win_rate_pct": round(len(wins) / len(trades) * 100, 1) if trades else None,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
            "avg_win": round(gross_win / len(wins), 2) if wins else None,
            "avg_loss": round(-gross_loss / len(losses), 2) if losses else None,
            "avg_hold_hours": round(sum(hold_hours) / len(hold_hours), 1) if hold_hours else None,
            "total_fees": round(sum(float(t["fees"] or 0) for t in trades), 2),
            "evaluations": evaluations,
            "entries_attempted": entries_attempted,
        },
        "by_strategy": bucket(lambda t: t.get("strategy") or "?"),
        "by_style": bucket(lambda t: t.get("style") or "?"),
        "by_direction": bucket(lambda t: t.get("direction")),
        "by_exit_reason": bucket(lambda t: t.get("exit_reason") or "?"),
        "by_symbol": bucket(lambda t: t.get("symbol")),
        "monthly_return_pct": monthly_returns,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(PROJECT_ROOT / "data" / "backtest.db"))
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--eval-every", type=int, default=1, help="hours between entry scans")
    parser.add_argument("--out", default=None, help="output JSON path")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    symbols = (
        [s.strip() for s in args.symbols.split(",") if s.strip()]
        if args.symbols
        else list(config.TRADING_SYMBOLS)
    )
    print(f"Replaying {len(symbols)} symbols from {args.db} "
          f"(ALLOW_LONG={config.ALLOW_LONG}, ALLOW_SHORT={config.ALLOW_SHORT})")
    report = run_backtest(Path(args.db), symbols, args.eval_every, args.quiet)

    out = Path(args.out) if args.out else (
        PROJECT_ROOT / "data" / f"backtest_eval_{datetime.now(timezone.utc):%Y%m%d_%H%M}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {out}\n")
    print(json.dumps({k: report[k] for k in ("summary", "by_strategy", "by_style",
                                             "by_direction", "by_exit_reason")},
                     indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
