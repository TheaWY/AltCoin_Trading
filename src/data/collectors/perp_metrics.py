"""Derivatives and order-book metrics for every USDT perpetual.

Three tables, all keyed (symbol, ts) in seconds:

  perp_1m    every minute from /fapi/v1/premiumIndex (one call, all symbols):
             mark, index, basis (mark vs index, bps), last funding rate,
             next funding time
  perp_5m    5-minute history from /futures/data/* (per symbol):
             open interest (coins, USD), circulating supply (-> market cap),
             global account long/short, top-trader position and account
             long/short, taker buy/sell volume
  book_5m    order book snapshot every ~5 minutes from /fapi/v1/depth
             (limit 100): spread, bid/ask depth within 0.5% and 2%,
             imbalance at both bands, depth-weighted microprice offset

The /futures/data endpoints allow ~1000 requests per 5 minutes per IP and
keep 30 days of history, so the live loop walks the symbol list slowly and
asks for the last few points each time (no gaps), and backfill can reach
back up to 30 days.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from src.data.collectors.klines_1m import FAPI, code

logger = logging.getLogger(__name__)
RETENTION_DAYS = 35

SCHEMAS = (
    """CREATE TABLE IF NOT EXISTS perp_1m (
        symbol TEXT NOT NULL, ts BIGINT NOT NULL, mark DOUBLE PRECISION, idx DOUBLE PRECISION,
        basis_bps DOUBLE PRECISION, funding DOUBLE PRECISION, next_funding BIGINT,
        PRIMARY KEY (symbol, ts))""",
    """CREATE TABLE IF NOT EXISTS perp_5m (
        symbol TEXT NOT NULL, ts BIGINT NOT NULL, oi DOUBLE PRECISION, oi_usd DOUBLE PRECISION,
        supply DOUBLE PRECISION, ls_global DOUBLE PRECISION, ls_top_pos DOUBLE PRECISION,
        ls_top_acct DOUBLE PRECISION, taker_ratio DOUBLE PRECISION, taker_buy DOUBLE PRECISION,
        taker_sell DOUBLE PRECISION, PRIMARY KEY (symbol, ts))""",
    """CREATE TABLE IF NOT EXISTS book_5m (
        symbol TEXT NOT NULL, ts BIGINT NOT NULL, spread_bps DOUBLE PRECISION,
        bid_05 DOUBLE PRECISION, ask_05 DOUBLE PRECISION, bid_2 DOUBLE PRECISION, ask_2 DOUBLE PRECISION,
        imb_05 DOUBLE PRECISION, imb_2 DOUBLE PRECISION, micro_bps DOUBLE PRECISION,
        PRIMARY KEY (symbol, ts))""",
)

FUT_ENDPOINTS = {
    "openInterestHist": ("oi", "oi_usd", "supply"),
    "globalLongShortAccountRatio": ("ls_global",),
    "topLongShortPositionRatio": ("ls_top_pos",),
    "topLongShortAccountRatio": ("ls_top_acct",),
    "takerlongshortRatio": ("taker_ratio", "taker_buy", "taker_sell"),
}
PERP5_COLS = ("oi", "oi_usd", "supply", "ls_global", "ls_top_pos", "ls_top_acct",
              "taker_ratio", "taker_buy", "taker_sell")


def ensure_schema(storage: Any) -> None:
    with storage._connect() as c:  # noqa: SLF001
        for sql in SCHEMAS:
            c.execute(sql)
        for t in ("perp_1m", "perp_5m", "book_5m"):
            c.execute(f"CREATE INDEX IF NOT EXISTS {t}_ts ON {t} (ts)")


def _executemany(storage: Any, sql: str, rows: list[tuple]) -> int:
    if not rows:
        return 0
    with storage._connect() as c:  # noqa: SLF001
        if getattr(storage, "is_postgres", False):
            with c.raw.cursor() as cur:
                cur.executemany(sql.replace("?", "%s"), rows)
        else:
            c.executemany(sql, rows)
    return len(rows)


def _f(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- premium index (1m)

def fetch_premium(session: requests.Session, symbols: set[str] | None = None) -> list[tuple]:
    data = session.get(f"{FAPI}/fapi/v1/premiumIndex", timeout=20).json()
    ts = int(time.time()) // 60 * 60
    rows = []
    for d in data:
        s = d.get("symbol", "")
        if not s.endswith("USDT"):
            continue
        sym = s[:-4] + "/USDT"
        if symbols is not None and sym not in symbols:
            continue
        mark, idx = _f(d.get("markPrice")), _f(d.get("indexPrice"))
        basis = (mark / idx - 1) * 1e4 if mark and idx else None
        rows.append((sym, ts, mark, idx, basis, _f(d.get("lastFundingRate")),
                     int(d.get("nextFundingTime") or 0) // 1000 or None))
    return rows


def insert_premium(storage: Any, rows: list[tuple]) -> int:
    return _executemany(storage, "INSERT INTO perp_1m (symbol, ts, mark, idx, basis_bps, funding, next_funding) "
                                 "VALUES (?,?,?,?,?,?,?) ON CONFLICT (symbol, ts) DO NOTHING", rows)


# ---------------------------------------------------------------- futures/data (5m)

def fetch_futures(session: requests.Session, symbol: str, limit: int = 4, start_ms: int | None = None,
                  end_ms: int | None = None, pause: float = 0.0) -> dict[int, dict[str, float | None]]:
    """ts -> column values merged across the five /futures/data endpoints."""
    out: dict[int, dict[str, float | None]] = {}
    for ep, cols in FUT_ENDPOINTS.items():
        params: dict[str, Any] = {"symbol": code(symbol), "period": "5m", "limit": limit}
        if start_ms:
            params["startTime"] = start_ms
        if end_ms:
            params["endTime"] = end_ms
        r = session.get(f"{FAPI}/futures/data/{ep}", params=params, timeout=20)
        if r.status_code in (418, 429):
            wait = int(r.headers.get("Retry-After", "60"))
            logger.warning("futures/data rate limited; sleeping %ss", wait)
            time.sleep(wait)
            continue
        if r.status_code != 200:
            continue
        for d in r.json() or []:
            ts = int(d["timestamp"]) // 1000
            row = out.setdefault(ts, {})
            if ep == "openInterestHist":
                row.update(oi=_f(d.get("sumOpenInterest")), oi_usd=_f(d.get("sumOpenInterestValue")),
                           supply=_f(d.get("CMCCirculatingSupply")))
            elif ep == "takerlongshortRatio":
                row.update(taker_ratio=_f(d.get("buySellRatio")), taker_buy=_f(d.get("buyVol")),
                           taker_sell=_f(d.get("sellVol")))
            else:
                row[cols[0]] = _f(d.get("longShortRatio"))
        if pause:
            time.sleep(pause)
    return out


def insert_futures(storage: Any, symbol: str, data: dict[int, dict[str, float | None]]) -> int:
    rows = [(symbol, ts, *[v.get(c) for c in PERP5_COLS]) for ts, v in data.items()]
    sets = ", ".join(f"{c} = COALESCE(EXCLUDED.{c}, perp_5m.{c})" for c in PERP5_COLS)
    sql = (f"INSERT INTO perp_5m (symbol, ts, {', '.join(PERP5_COLS)}) VALUES ({', '.join('?' * (len(PERP5_COLS) + 2))}) "
           f"ON CONFLICT (symbol, ts) DO UPDATE SET {sets}")
    return _executemany(storage, sql, rows)


# ---------------------------------------------------------------- order book (5m)

def book_row(symbol: str, ts: int, bids: list, asks: list) -> tuple | None:
    if not bids or not asks:
        return None
    b = [(float(p), float(q)) for p, q in bids]
    a = [(float(p), float(q)) for p, q in asks]
    bb, ba = b[0][0], a[0][0]
    mid = (bb + ba) / 2
    if mid <= 0:
        return None

    def depth(levels, lo, hi):
        return sum(p * q for p, q in levels if lo <= p <= hi)

    bid05, ask05 = depth(b, mid * 0.995, mid), depth(a, mid, mid * 1.005)
    bid2, ask2 = depth(b, mid * 0.98, mid), depth(a, mid, mid * 1.02)
    imb = lambda x, y: (x - y) / (x + y) if x + y > 0 else None  # noqa: E731
    micro = (ba * b[0][1] + bb * a[0][1]) / (b[0][1] + a[0][1]) if b[0][1] + a[0][1] > 0 else mid
    return (symbol, ts, (ba - bb) / mid * 1e4, bid05, ask05, bid2, ask2, imb(bid05, ask05), imb(bid2, ask2),
            (micro / mid - 1) * 1e4)


def fetch_book(session: requests.Session, symbol: str) -> tuple | None:
    r = session.get(f"{FAPI}/fapi/v1/depth", params={"symbol": code(symbol), "limit": 100}, timeout=20)
    if r.status_code != 200:
        return None
    d = r.json()
    return book_row(symbol, int(d.get("T", time.time() * 1000)) // 1000 // 60 * 60, d.get("bids"), d.get("asks"))


def insert_book(storage: Any, rows: list[tuple]) -> int:
    return _executemany(storage, "INSERT INTO book_5m (symbol, ts, spread_bps, bid_05, ask_05, bid_2, ask_2, imb_05, "
                                 "imb_2, micro_bps) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT (symbol, ts) DO NOTHING",
                        [r for r in rows if r])


def prune(storage: Any, now: int | None = None) -> None:
    cutoff = int(now or time.time()) - RETENTION_DAYS * 86400
    with storage._connect() as c:  # noqa: SLF001
        for t in ("perp_1m", "perp_5m", "book_5m"):
            c.execute(f"DELETE FROM {t} WHERE ts < ?", (cutoff,))
