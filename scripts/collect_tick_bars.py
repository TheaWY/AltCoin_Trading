#!/usr/bin/env python3
"""Collect live Binance futures ticks into compact tick bars.

This is a Mac-mini worker script for future scalping research.

It does NOT store every raw trade tick. Instead it subscribes to Binance futures
aggTrade streams, aggregates trades into 1s/5s bars in memory, and batch-writes
those compact bars into Postgres/SQLite.

Why bars, not raw ticks?
- Raw ticks for hundreds of markets can explode DB size and slow Railway.
- 1s/5s bars preserve enough microstructure for early scalping features:
  direction, burst volume, trade count, taker-buy imbalance, short-term volatility.
- You can later build raw-tick capture for a tiny watchlist if the 1s bars prove useful.

Examples:
    # Start with top 50 crypto perps, 1-second bars, write every 5 seconds
    python scripts/collect_tick_bars.py --limit 50 --bucket-seconds 1 --flush-seconds 5

    # Specific liquid symbols only
    python scripts/collect_tick_bars.py --only BTC/USDT,ETH/USDT,SOL/USDT --bucket-seconds 1

Environment:
    DATABASE_URL must point to Railway Postgres if you want the dashboard/research
    service to see the data. LIVE_TRADING can remain false.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src import config  # noqa: E402
from src.data.collectors.binance import _build_exchange  # noqa: E402
from src.data.storage import get_storage  # noqa: E402
from src.symbols import ccxt_symbol, trading_symbols  # noqa: E402

logger = logging.getLogger(__name__)

BINANCE_FUTURES_COMBINED_STREAM = "wss://fstream.binance.com/stream?streams="
MAX_STREAMS_PER_CONNECTION = 200
ALNUM_SYMBOL = re.compile(r"^[A-Z0-9]+$")


@dataclass
class TickBar:
    symbol: str
    timestamp: int
    bucket_seconds: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    trade_count: int = 0
    taker_buy_volume: float = 0.0
    notional: float = 0.0

    def update(self, price: float, qty: float, taker_buy: bool) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += qty
        self.notional += price * qty
        self.trade_count += 1
        if taker_buy:
            self.taker_buy_volume += qty

    def row(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "bucket_seconds": self.bucket_seconds,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "trade_count": self.trade_count,
            "taker_buy_volume": self.taker_buy_volume,
            "notional": self.notional,
        }


class TickBarWriter:
    def __init__(self, storage: Any) -> None:
        self.storage = storage
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        if self.storage.is_postgres:
            schema = """
            CREATE TABLE IF NOT EXISTS tick_bars (
                id BIGSERIAL PRIMARY KEY,
                symbol TEXT NOT NULL,
                timestamp BIGINT NOT NULL,
                bucket_seconds INTEGER NOT NULL,
                open DOUBLE PRECISION NOT NULL,
                high DOUBLE PRECISION NOT NULL,
                low DOUBLE PRECISION NOT NULL,
                close DOUBLE PRECISION NOT NULL,
                volume DOUBLE PRECISION NOT NULL,
                trade_count INTEGER NOT NULL,
                taker_buy_volume DOUBLE PRECISION NOT NULL,
                notional DOUBLE PRECISION NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE(symbol, timestamp, bucket_seconds)
            );
            CREATE INDEX IF NOT EXISTS idx_tick_bars_symbol_bucket_ts
                ON tick_bars(symbol, bucket_seconds, timestamp);
            """
            with self.storage._connect() as conn:
                for statement in schema.split(";"):
                    if statement.strip():
                        conn.raw.execute(statement)
            return

        schema = """
        CREATE TABLE IF NOT EXISTS tick_bars (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            bucket_seconds INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            trade_count INTEGER NOT NULL,
            taker_buy_volume REAL NOT NULL,
            notional REAL NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(symbol, timestamp, bucket_seconds)
        );
        CREATE INDEX IF NOT EXISTS idx_tick_bars_symbol_bucket_ts
            ON tick_bars(symbol, bucket_seconds, timestamp);
        """
        with self.storage._connect() as conn:
            conn.raw.executescript(schema)

    def insert_many(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        if self.storage.is_postgres:
            return self._insert_many_postgres(rows)
        return self._insert_many_sqlite(rows)

    def _insert_many_postgres(self, rows: list[dict[str, Any]]) -> int:
        values = [
            (
                r["symbol"],
                int(r["timestamp"]),
                int(r["bucket_seconds"]),
                float(r["open"]),
                float(r["high"]),
                float(r["low"]),
                float(r["close"]),
                float(r["volume"]),
                int(r["trade_count"]),
                float(r["taker_buy_volume"]),
                float(r["notional"]),
            )
            for r in rows
        ]
        affected = 0
        chunk_size = 500
        with self.storage._pool().connection() as conn:
            with conn.cursor() as cur:
                for start in range(0, len(values), chunk_size):
                    chunk = values[start : start + chunk_size]
                    placeholders = ",".join(["(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"] * len(chunk))
                    flat: list[Any] = []
                    for item in chunk:
                        flat.extend(item)
                    cur.execute(
                        f"""
                        INSERT INTO tick_bars
                            (symbol, timestamp, bucket_seconds, open, high, low, close,
                             volume, trade_count, taker_buy_volume, notional)
                        VALUES {placeholders}
                        ON CONFLICT (symbol, timestamp, bucket_seconds)
                        DO UPDATE SET
                            high = GREATEST(tick_bars.high, EXCLUDED.high),
                            low = LEAST(tick_bars.low, EXCLUDED.low),
                            close = EXCLUDED.close,
                            volume = tick_bars.volume + EXCLUDED.volume,
                            trade_count = tick_bars.trade_count + EXCLUDED.trade_count,
                            taker_buy_volume = tick_bars.taker_buy_volume + EXCLUDED.taker_buy_volume,
                            notional = tick_bars.notional + EXCLUDED.notional
                        """,
                        flat,
                    )
                    affected += max(cur.rowcount, 0)
        return affected

    def _insert_many_sqlite(self, rows: list[dict[str, Any]]) -> int:
        sql = """
            INSERT OR IGNORE INTO tick_bars
                (symbol, timestamp, bucket_seconds, open, high, low, close,
                 volume, trade_count, taker_buy_volume, notional)
            VALUES
                (:symbol, :timestamp, :bucket_seconds, :open, :high, :low, :close,
                 :volume, :trade_count, :taker_buy_volume, :notional)
        """
        with self.storage._connect() as conn:
            return conn.executemany(sql, rows).rowcount


class TickBarCollector:
    def __init__(
        self,
        symbols: list[str],
        bucket_seconds: int,
        flush_seconds: float,
        writer: TickBarWriter,
    ) -> None:
        self.symbols = symbols
        self.bucket_seconds = bucket_seconds
        self.flush_seconds = flush_seconds
        self.writer = writer
        self.bars: dict[tuple[str, int], TickBar] = {}
        self._stop = asyncio.Event()
        self._last_count = 0

    def stop(self) -> None:
        self._stop.set()

    def _bucket_ts(self, event_ts_ms: int) -> int:
        ts = event_ts_ms // 1000
        return ts - (ts % self.bucket_seconds)

    def ingest_trade(self, trade: dict[str, Any]) -> None:
        stream_symbol = trade.get("s")
        if not stream_symbol:
            return
        symbol = stream_to_spot_symbol(stream_symbol)
        try:
            price = float(trade.get("p"))
            qty = float(trade.get("q"))
            event_ms = int(trade.get("T") or trade.get("E") or time.time() * 1000)
        except (TypeError, ValueError):
            return
        # Binance aggTrade m=true means buyer is the market maker, so taker was seller.
        taker_buy = not bool(trade.get("m"))
        bucket = self._bucket_ts(event_ms)
        key = (symbol, bucket)
        bar = self.bars.get(key)
        if bar is None:
            bar = TickBar(
                symbol=symbol,
                timestamp=bucket,
                bucket_seconds=self.bucket_seconds,
                open=price,
                high=price,
                low=price,
                close=price,
            )
            self.bars[key] = bar
        bar.update(price, qty, taker_buy)
        self._last_count += 1

    async def flush_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self.flush_seconds)
            self.flush(final=False)
        self.flush(final=True)

    def flush(self, final: bool) -> None:
        now = int(time.time())
        # Keep the currently forming bucket in memory unless this is final flush.
        cutoff = now - self.bucket_seconds if not final else now + self.bucket_seconds
        ready_keys = [key for key in self.bars if key[1] <= cutoff]
        if not ready_keys:
            return
        rows = [self.bars.pop(key).row() for key in ready_keys]
        inserted = self.writer.insert_many(rows)
        logger.info(
            "Tick bars flushed: rows=%d affected=%d active_bars=%d trades_seen=%d",
            len(rows),
            inserted,
            len(self.bars),
            self._last_count,
        )
        self._last_count = 0

    async def run(self) -> None:
        flush_task = asyncio.create_task(self.flush_loop())
        try:
            tasks = [
                asyncio.create_task(self._run_connection(chunk))
                for chunk in chunked(self.symbols, MAX_STREAMS_PER_CONNECTION)
            ]
            await asyncio.gather(*tasks)
        finally:
            self.stop()
            flush_task.cancel()
            self.flush(final=True)

    async def _run_connection(self, symbols: list[str]) -> None:
        import websockets

        streams = "/".join(f"{spot_to_stream_symbol(s).lower()}@aggTrade" for s in symbols)
        url = BINANCE_FUTURES_COMBINED_STREAM + streams
        while not self._stop.is_set():
            try:
                logger.info("Opening tick stream for %d symbols", len(symbols))
                async with websockets.connect(url, ping_interval=20, max_size=2**24) as ws:
                    async for raw in ws:
                        if self._stop.is_set():
                            return
                        msg = json.loads(raw)
                        data = msg.get("data") if isinstance(msg, dict) else None
                        if isinstance(data, dict) and data.get("e") == "aggTrade":
                            self.ingest_trade(data)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Tick stream failed for chunk (%s); reconnecting", exc)
                await asyncio.sleep(5)


def spot_to_stream_symbol(symbol: str) -> str:
    return symbol.replace("/", "").upper()


def stream_to_spot_symbol(stream_symbol: str) -> str:
    s = stream_symbol.upper()
    if s.endswith("USDT"):
        return f"{s[:-4]}/USDT"
    return s


def chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _streamable_symbols(symbols: list[str]) -> list[str]:
    out: list[str] = []
    skipped: list[str] = []
    for symbol in symbols:
        stream = spot_to_stream_symbol(symbol)
        if ALNUM_SYMBOL.match(stream):
            out.append(symbol)
        else:
            skipped.append(symbol)
    if skipped:
        logger.warning("Skipping %d non-alphanumeric symbols for websocket streams: %s", len(skipped), skipped[:10])
    return out


def _parse_symbols(value: str) -> list[str]:
    return [s.strip() for s in value.split(",") if s.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=50, help="Top N crypto perps to stream; 0 = all discovered, not recommended")
    parser.add_argument("--only", default="", help="Comma-separated symbols instead of auto universe")
    parser.add_argument("--bucket-seconds", type=int, default=1, choices=[1, 5, 10, 15, 30, 60])
    parser.add_argument("--flush-seconds", type=float, default=5.0)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper()), format="%(asctime)s %(levelname)s %(message)s")
    os.environ.setdefault("LIVE_TRADING", "false")
    os.environ.setdefault("BINANCE_MARKET_DATA_TESTNET", "false")
    if args.limit >= 0:
        os.environ["TRADING_SYMBOLS_LIMIT"] = str(args.limit)
        config.TRADING_SYMBOLS_LIMIT = args.limit
    config.BINANCE_MARKET_DATA_TESTNET = False

    storage = get_storage()
    writer = TickBarWriter(storage)

    if args.only:
        symbols = _parse_symbols(args.only)
    else:
        symbols = trading_symbols()
        if args.limit > 0:
            symbols = symbols[: args.limit]
    symbols = _streamable_symbols(symbols)
    if not symbols:
        print("No symbols to stream.")
        return 2

    print("\n=== Live tick-bar collector ===")
    print(f"Symbols: {len(symbols)}")
    print(f"Bucket: {args.bucket_seconds}s")
    print(f"Flush: every {args.flush_seconds}s")
    print(f"DATABASE_URL set: {'yes' if bool(config.DATABASE_URL) else 'no (local SQLite)'}")
    print("Table: tick_bars")
    print("Stop with Control+C")
    print("===============================\n")

    collector = TickBarCollector(symbols, args.bucket_seconds, args.flush_seconds, writer)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _request_stop(*_: Any) -> None:
        logger.info("Stop requested; flushing bars...")
        collector.stop()

    try:
        signal.signal(signal.SIGINT, _request_stop)
        signal.signal(signal.SIGTERM, _request_stop)
    except Exception:
        pass

    try:
        loop.run_until_complete(collector.run())
    except KeyboardInterrupt:
        collector.stop()
        collector.flush(final=True)
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
