#!/usr/bin/env python3
"""Refill a gap in the Korean 1-minute candle record from the public Upbit / Bithumb REST APIs.

Used after the 2026-10-18..10-31 disk-full outage. Writes in the same shape stream_korea_1m.py produces:
  * UTC days older than KEEP_DAYS  -> data/cache/kr1m/<exchange>/<YYYY-MM-DD>.parquet (merged; existing rows win)
  * the last KEEP_DAYS days         -> table kr_1m (ON CONFLICT DO NOTHING)
REST candles carry o/h/l/c and KRW value only; buy_krw, trades and order-book columns stay NULL for refilled rows,
so research that needs taker flow or book depth must treat these days as "price only".
Read-only on the exchanges; paper system; never touches orders or LIVE_TRADING.
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from stream_korea_1m import ARCH, KEEP_DAYS, EX  # noqa: E402
from src.data.storage import get_storage  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("kr1m_refill")
BASE = {"upbit": "https://api.upbit.com/v1", "bithumb": "https://api.bithumb.com/v1"}
KST = timezone(timedelta(hours=9))
COLS = ["exchange", "symbol", "ts", "o", "h", "l", "c", "value_krw", "buy_krw", "trades", "ob_bid_krw", "ob_ask_krw"]


def markets(ex: str) -> list[str]:
    return [m["market"] for m in requests.get(EX[ex][1], timeout=20).json() if m["market"].startswith("KRW-")]


def fetch(ex: str, s: requests.Session, market: str, since: int, until: int, pace: float) -> list[tuple]:
    """1m candles with since <= ts < until, paging backwards 200 at a time."""
    to = datetime.fromtimestamp(until, KST)
    rows: list[tuple] = []
    while True:
        par = {"market": market, "count": 200, "to": to.strftime("%Y-%m-%dT%H:%M:%S") + ("+09:00" if ex == "upbit" else "")}
        d = []
        for attempt in range(6):
            try:
                r = s.get(f"{BASE[ex]}/candles/minutes/1", params=par, timeout=20)
            except requests.RequestException:
                time.sleep(2 * (attempt + 1)); continue
            time.sleep(pace)
            if r.status_code == 429:
                time.sleep(2 * (attempt + 1)); continue
            d = r.json() if r.status_code == 200 else []
            break
        if not isinstance(d, list) or not d:
            break
        for k in d:
            ts = int(datetime.fromisoformat(k["candle_date_time_utc"]).replace(tzinfo=timezone.utc).timestamp())
            if since <= ts < until:
                rows.append((ex, market[4:], ts, k["opening_price"], k["high_price"], k["low_price"], k["trade_price"],
                             k["candle_acc_trade_price"]))
        first = datetime.fromisoformat(d[-1]["candle_date_time_kst"]).replace(tzinfo=KST)
        if first.timestamp() <= since or len(d) < 200:
            break
        to = first
    return rows


def run_ex(ex: str, since: int, until: int, pace: float, out: dict) -> None:
    s = requests.Session()
    mk = markets(ex)
    rows: list[tuple] = []
    for i, m in enumerate(mk):
        rows += fetch(ex, s, m, since, until, pace)
        if i % 25 == 0:
            log.info("%s [%d/%d] %s rows so far %d", ex, i + 1, len(mk), m, len(rows))
    out[ex] = rows
    log.info("%s done: %d markets, %d rows", ex, len(mk), len(rows))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="first UTC day YYYY-MM-DD (inclusive)")
    ap.add_argument("--end", required=True, help="last UTC day YYYY-MM-DD (inclusive)")
    ap.add_argument("--pace", type=float, default=0.13)
    a = ap.parse_args()
    since = int(pd.Timestamp(a.start, tz="UTC").timestamp())
    until = int((pd.Timestamp(a.end, tz="UTC") + pd.Timedelta(days=1)).timestamp())
    out: dict = {}
    ts = [threading.Thread(target=run_ex, args=(ex, since, until, a.pace, out)) for ex in ("upbit", "bithumb")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    df = pd.DataFrame([r for rs in out.values() for r in rs], columns=COLS[:8])
    if df.empty:
        log.warning("nothing fetched"); return 1
    df["buy_krw"] = np.nan; df["trades"] = pd.array([pd.NA] * len(df), dtype="Int64")
    df["ob_bid_krw"] = np.nan; df["ob_ask_krw"] = np.nan
    df = df.drop_duplicates(["exchange", "symbol", "ts"])
    cutoff_day = int(time.time()) // 86400 - KEEP_DAYS
    st = get_storage()
    n_arch = n_tab = 0
    for (ex, day), g in df.groupby([df.exchange, df.ts // 86400]):
        if day < cutoff_day:
            path = ARCH / ex / f"{pd.Timestamp(day * 86400, unit='s').strftime('%Y-%m-%d')}.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                old = pd.read_parquet(path)
                g = pd.concat([old, g[COLS]]).drop_duplicates(["symbol", "ts"], keep="first")   # stream rows win
            g[COLS].sort_values(["symbol", "ts"]).to_parquet(path, index=False, compression="zstd")
            n_arch += len(g)
        else:
            with st._connect() as c:  # noqa: SLF001
                for r in g[COLS].itertuples(index=False):
                    c.execute("INSERT INTO kr_1m VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                              tuple(None if (isinstance(v, float) and np.isnan(v)) or v is pd.NA else v for v in r))
            n_tab += len(g)
    log.info("refill done: %d rows to parquet archive, %d rows to kr_1m", n_arch, n_tab)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
