"""1-minute kline store for the pump rider.

Table prices_1m (separate from `prices` so the hourly tables stay small):
symbol, ts (bar open, seconds), OHLC, base volume, quote volume, trade
count, taker-buy quote volume. Sources:

  * live: Binance USDT-M combined websocket, kline_1m for every TRADING
    USDT perpetual (closed bars only), see scripts/stream_1m.py
  * history: REST /fapi/v1/klines backfill, see scripts/backfill_1m.py

Rows older than RETENTION_DAYS are pruned by the stream once a day.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

FAPI = "https://fapi.binance.com"
RETENTION_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices_1m (
    symbol TEXT NOT NULL,
    ts BIGINT NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION NOT NULL,
    quote_volume DOUBLE PRECISION NOT NULL,
    trades INTEGER NOT NULL,
    taker_buy_quote DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (symbol, ts)
)
"""

INSERT = ("INSERT INTO prices_1m (symbol, ts, open, high, low, close, volume, quote_volume, trades, "
          "taker_buy_quote) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT (symbol, ts) DO NOTHING")


def ensure_schema(storage: Any) -> None:
    with storage._connect() as c:  # noqa: SLF001
        c.execute(SCHEMA)
        c.execute("CREATE INDEX IF NOT EXISTS prices_1m_ts ON prices_1m (ts)")


def usdt_perps() -> list[str]:
    """Every TRADING USDT-margined perpetual, as 'BASE/USDT'."""
    data = requests.get(f"{FAPI}/fapi/v1/exchangeInfo", timeout=20).json()
    return sorted(
        f"{s['baseAsset']}/USDT" for s in data.get("symbols", [])
        if s.get("quoteAsset") == "USDT" and s.get("contractType") == "PERPETUAL"
        and s.get("status") == "TRADING"
    )


def code(symbol: str) -> str:
    return symbol.replace("/", "")


def row_from_rest(symbol: str, k: list) -> tuple:
    return (symbol, int(k[0]) // 1000, float(k[1]), float(k[2]), float(k[3]), float(k[4]),
            float(k[5]), float(k[7]), int(k[8]), float(k[10]))


def row_from_ws(symbol: str, k: dict) -> tuple:
    return (symbol, int(k["t"]) // 1000, float(k["o"]), float(k["h"]), float(k["l"]), float(k["c"]),
            float(k["v"]), float(k["q"]), int(k["n"]), float(k["Q"]))


def insert_rows(storage: Any, rows: list[tuple]) -> int:
    if not rows:
        return 0
    with storage._connect() as c:  # noqa: SLF001
        if getattr(storage, "is_postgres", False):
            sql = INSERT.replace("?", "%s")
            with c.raw.cursor() as cur:
                cur.executemany(sql, rows)
        else:
            c.executemany(INSERT, rows)
    return len(rows)


def prune(storage: Any, now: int | None = None) -> None:
    cutoff = int(now or time.time()) - RETENTION_DAYS * 86400
    with storage._connect() as c:  # noqa: SLF001
        c.execute("DELETE FROM prices_1m WHERE ts < ?", (cutoff,))


def last_ts(storage: Any) -> dict[str, int]:
    with storage._connect() as c:  # noqa: SLF001
        rows = c.execute("SELECT symbol, MAX(ts) AS ts FROM prices_1m GROUP BY symbol").fetchall()
    return {dict(r)["symbol"]: int(dict(r)["ts"]) for r in rows}
