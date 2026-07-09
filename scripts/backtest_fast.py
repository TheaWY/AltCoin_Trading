#!/usr/bin/env python3
"""Fast in-memory backtest for strategy sanity checks.

Why this exists:
    scripts/backtest.py is faithful to the live storage path, but when DATABASE_URL
    points to Railway Postgres it repeatedly queries the remote DB inside the replay
    loop. That is extremely slow. This script preloads the needed historical rows
    once, then replays from memory.

Use this to answer: "does this strategy actually emit signals and open simulated
trades on my historical candles?"

Examples:
    python scripts/backtest_fast.py --strategy mean_reversion --symbols BTC/USDT --start 2026-07-01
    ALLOW_LONG=true MIN_CONFIDENCE=0 python scripts/backtest_fast.py --strategy mean_reversion --symbols BTC/USDT,ETH/USDT --start 2026-07-01
"""

from __future__ import annotations

import argparse
import bisect
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src import config  # noqa: E402
from src.data.storage import Storage, get_storage  # noqa: E402
from src.engine.signal import gather_strategy_data  # noqa: E402
from src.strategies.base import SignalDirection  # noqa: E402
from src.strategies.registry import get_strategy, list_strategies  # noqa: E402
from scripts.backtest import BacktestPortfolio, _max_drawdown, _parse_datetime, _pct_return  # noqa: E402


@dataclass
class SeriesCache:
    rows: list[dict[str, Any]]
    timestamps: list[int]

    @classmethod
    def from_rows(cls, rows: list[dict[str, Any]]) -> "SeriesCache":
        clean = sorted(rows, key=lambda row: int(row["timestamp"]))
        return cls(clean, [int(row["timestamp"]) for row in clean])

    def before(self, timestamp: int, *, limit: int = 100, since: int | None = None) -> list[dict[str, Any]]:
        end = bisect.bisect_left(self.timestamps, timestamp)
        start = 0
        if since is not None:
            start = bisect.bisect_left(self.timestamps, since)
        subset = self.rows[start:end]
        return subset[-limit:] if limit else subset

    def latest_before(self, timestamp: int) -> dict[str, Any] | None:
        idx = bisect.bisect_left(self.timestamps, timestamp) - 1
        if idx < 0:
            return None
        return self.rows[idx]


class MemorySnapshotStorage:
    """Storage-like adapter backed by preloaded in-memory rows."""

    def __init__(
        self,
        timestamp: int,
        price_cache: dict[str, SeriesCache],
        funding_cache: dict[str, SeriesCache],
        latest_signal: dict[str, Any] | None = None,
    ) -> None:
        self.timestamp = timestamp
        self.price_cache = price_cache
        self.funding_cache = funding_cache
        self.latest_signal = latest_signal

    def get_latest_price(self, symbol: str, timeframe: str = "1h") -> dict[str, Any] | None:
        cache = self.price_cache.get(symbol)
        return cache.latest_before(self.timestamp) if cache else None

    def get_prices(
        self,
        symbol: str,
        limit: int = 100,
        since: int | None = None,
        before: int | None = None,
        timeframe: str = "1h",
    ) -> list[dict[str, Any]]:
        cache = self.price_cache.get(symbol)
        if not cache:
            return []
        effective_before = min(value for value in (before, self.timestamp) if value is not None)
        return cache.before(effective_before, limit=limit, since=since)

    def get_latest_funding_rate(self, symbol: str) -> dict[str, Any] | None:
        cache = self.funding_cache.get(symbol)
        return cache.latest_before(self.timestamp) if cache else None

    def get_funding_rates(
        self,
        symbol: str,
        limit: int = 100,
        since: int | None = None,
        before: int | None = None,
    ) -> list[dict[str, Any]]:
        cache = self.funding_cache.get(symbol)
        if not cache:
            return []
        effective_before = min(value for value in (before, self.timestamp) if value is not None)
        return cache.before(effective_before, limit=limit, since=since)

    def get_volume_stats(
        self, symbol: str, timeframe: str = "1h", lookback: int = 24
    ) -> dict[str, float | int | None]:
        rows = self.get_prices(symbol, limit=lookback, timeframe=timeframe)
        volumes = [float(row["volume"]) for row in rows]
        if not volumes:
            return {"avg": None, "stddev": None, "count": 0}
        avg = sum(volumes) / len(volumes)
        variance = sum((volume - avg) ** 2 for volume in volumes) / len(volumes)
        return {"avg": avg, "stddev": variance ** 0.5, "count": len(volumes)}

    def get_latest_signal(
        self, symbol: str | None = None, strategy: str | None = None
    ) -> dict[str, Any] | None:
        if self.latest_signal is None:
            return None
        if symbol and self.latest_signal.get("symbol") != symbol:
            return None
        if strategy and self.latest_signal.get("strategy") != strategy:
            return None
        return self.latest_signal

    def get_open_trade_for_symbol(self, symbol: str) -> None:
        return None

    # Unsupported enrichment data. Returning [] keeps positioning/crowding
    # strategies safely non-actionable until those histories are preloaded.
    def get_ls_ratio_history(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    def get_open_interest_history(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []


class FastBacktestEngine:
    def __init__(
        self,
        start: datetime,
        end: datetime,
        symbols: list[str],
        strategy_name: str,
        storage: Storage | None = None,
        warmup_hours: int = 720,
    ) -> None:
        self.storage = storage or get_storage()
        self.start = start
        self.end = end
        self.start_ts = int(start.timestamp())
        self.end_ts = int(end.timestamp())
        self.warmup_ts = self.start_ts - warmup_hours * 3600
        self.symbols = symbols
        self.strategy_name = strategy_name
        self.strategy = get_strategy(strategy_name)

    def run(self) -> dict[str, Any]:
        print("Preloading candles/funding once from storage...")
        price_cache = self._load_price_cache()
        funding_cache = self._load_funding_cache()
        timeline = sorted(
            {
                ts
                for symbol in self.symbols
                for ts in price_cache.get(symbol, SeriesCache([], [])).timestamps
                if self.start_ts <= ts <= self.end_ts
            }
        )
        print(f"Replay timestamps: {len(timeline)}")

        portfolio = BacktestPortfolio(storage=None)
        signal_count = 0
        opened_count = 0
        skipped_direction = 0
        skipped_missing = 0
        equity_curve: list[dict[str, float | int]] = []
        last_prices: dict[str, float] = {}

        for i, ts in enumerate(timeline, start=1):
            current_prices = self._prices_at(price_cache, ts)
            last_prices.update(current_prices)
            portfolio.check_exits(current_prices, ts)

            for symbol in self.symbols:
                snapshot = MemorySnapshotStorage(ts, price_cache, funding_cache)
                data = gather_strategy_data(snapshot, self.strategy, symbol)
                missing = self.strategy.validate_data(data)
                if missing:
                    skipped_missing += 1
                    continue
                signal = self.strategy.generate_signal(data)
                if signal.direction == SignalDirection.NONE:
                    continue
                signal_count += 1
                if not config.direction_allowed(signal.direction.value):
                    skipped_direction += 1
                    continue
                price_row = data.get("latest_price")
                if not price_row:
                    continue
                opened = portfolio.open_trade(
                    symbol,
                    signal.direction.value,
                    float(price_row["close"]),
                    ts,
                    strategy=self.strategy_name,
                    metadata=signal.metadata,
                )
                if opened:
                    opened_count += 1

            equity_curve.append({"timestamp": ts, "value": portfolio.value(last_prices, ts)})
            if i % 100 == 0:
                print(f"Replayed {i}/{len(timeline)} timestamps; signals={signal_count}; opened={opened_count}")

        if timeline:
            portfolio.close_all(last_prices, timeline[-1])
        final_value = portfolio.value(last_prices, timeline[-1] if timeline else None)
        closed = portfolio.closed_trades
        wins = [trade for trade in closed if float(trade.pnl or 0.0) > 0]
        losses = [trade for trade in closed if float(trade.pnl or 0.0) < 0]
        gross_profit = sum(float(t.pnl or 0.0) for t in wins)
        gross_loss = abs(sum(float(t.pnl or 0.0) for t in losses))

        return {
            "params": {
                "start": self.start.isoformat(),
                "end": self.end.isoformat(),
                "symbols": self.symbols,
                "strategy": self.strategy_name,
                "mode": "fast_in_memory_strategy_module",
            },
            "summary": {
                "starting_capital": config.PAPER_STARTING_CAPITAL,
                "final_value": round(final_value, 2),
                "portfolio_return_pct": round(_pct_return(config.PAPER_STARTING_CAPITAL, final_value), 2),
                "max_drawdown_pct": round(_max_drawdown([p["value"] for p in equity_curve]), 2),
                "btc_buy_hold_return_pct": self._btc_buy_hold_return(price_cache),
                "signals": signal_count,
                "trades_opened": opened_count,
                "closed_trades": len(closed),
                "win_rate_pct": round(len(wins) / len(closed) * 100.0, 2) if closed else None,
                "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else None,
                "skipped_missing_data": skipped_missing,
                "skipped_direction_policy": skipped_direction,
            },
            "closed_trade_pnls": [
                {
                    "symbol": t.symbol,
                    "strategy": t.strategy,
                    "direction": t.direction,
                    "pnl": round(float(t.pnl or 0.0), 6),
                    "fees": round(float(t.fees or 0.0), 6),
                    "opened_at": t.opened_at,
                    "closed_at": t.closed_at,
                    "exit_reason": t.exit_reason,
                }
                for t in closed
            ],
        }

    def _load_price_cache(self) -> dict[str, SeriesCache]:
        return {
            symbol: SeriesCache.from_rows(
                self.storage.get_prices(
                    symbol,
                    limit=1_000_000,
                    since=self.warmup_ts,
                    before=self.end_ts,
                    timeframe="1h",
                )
            )
            for symbol in self.symbols
        }

    def _load_funding_cache(self) -> dict[str, SeriesCache]:
        return {
            symbol: SeriesCache.from_rows(
                self.storage.get_funding_rates(
                    symbol,
                    limit=1_000_000,
                    since=self.warmup_ts,
                    before=self.end_ts,
                )
                or []
            )
            for symbol in self.symbols
        }

    def _prices_at(self, price_cache: dict[str, SeriesCache], timestamp: int) -> dict[str, float]:
        prices: dict[str, float] = {}
        for symbol, cache in price_cache.items():
            row = cache.latest_before(timestamp + 1)
            if row:
                prices[symbol] = float(row["close"])
        return prices

    def _btc_buy_hold_return(self, price_cache: dict[str, SeriesCache]) -> float | None:
        rows = price_cache.get(config.SYMBOL)
        if not rows or len(rows.rows) < 2:
            return None
        in_window = [row for row in rows.rows if self.start_ts <= int(row["timestamp"]) <= self.end_ts]
        if len(in_window) < 2:
            return None
        return round(_pct_return(float(in_window[0]["close"]), float(in_window[-1]["close"])), 2)


def _default_start() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=30)


def _default_end() -> datetime:
    return datetime.now(timezone.utc)


def _parse_symbols(value: str | None) -> list[str]:
    if not value:
        return list(config.TRADING_SYMBOLS)
    return [symbol.strip() for symbol in value.split(",") if symbol.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run fast in-memory historical strategy backtests.")
    parser.add_argument("--start", default=None, help="Start date/time, e.g. 2026-07-01")
    parser.add_argument("--end", default=None, help="End date/time, defaults to now")
    parser.add_argument("--symbols", default=None, help="Comma-separated spot symbols")
    parser.add_argument("--strategy", default=config.PRIMARY_STRATEGY, help=f"Strategy name (available: {', '.join(list_strategies())})")
    parser.add_argument("--warmup-hours", type=int, default=720, help="Lookback rows loaded before start for indicators")
    args = parser.parse_args()

    start = _parse_datetime(args.start) if args.start else _default_start()
    end = _parse_datetime(args.end) if args.end else _default_end()
    symbols = _parse_symbols(args.symbols)

    result = FastBacktestEngine(
        start=start,
        end=end,
        symbols=symbols,
        strategy_name=args.strategy,
        warmup_hours=args.warmup_hours,
    ).run()

    output_dir = PROJECT_ROOT / "data"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"backtest_fast_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.json"
    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Wrote {output_path}")
    print(json.dumps(result["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
