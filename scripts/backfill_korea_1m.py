#!/usr/bin/env python3
"""History of 1-minute KRW candles from Upbit and Bithumb (public REST, no
key) for coins that also trade as Binance USDT perps. Parquet per coin under
data/cache/kr1m_hist/<exchange>/<BASE>.parquet with the kr_1m columns
(buy_krw / orderbook are not in candles, so they stay empty).
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
OUT = PROJECT_ROOT / "data" / "cache" / "kr1m_hist"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("kr1m_hist")
BASE = {"upbit": "https://api.upbit.com/v1", "bithumb": "https://api.bithumb.com/v1"}
KST = timezone(timedelta(hours=9))


def perp_bases() -> set[str]:
    from src.data.storage import get_storage
    with get_storage()._connect() as c:  # noqa: SLF001
        rows = c.execute("SELECT DISTINCT symbol FROM prices WHERE timeframe='1h_perp' AND timestamp > ?",
                         (int(time.time()) - 200 * 86400,)).fetchall()
    out = set()
    for r in rows:
        b = (r["symbol"] if isinstance(r, dict) else r[0]).split("/")[0]
        out |= {b, b.removeprefix("1000"), b.removeprefix("1000000"), b.removeprefix("1M")}
    return out


def one(ex: str, s: requests.Session, market: str, since: int, pace: float) -> int:
    path = OUT / ex / f"{market[4:]}.parquet"
    if path.exists():
        return -1
    to = datetime.now(KST)
    rows = []
    while True:
        par = {"market": market, "count": 200,
               "to": to.strftime("%Y-%m-%dT%H:%M:%S") + ("+09:00" if ex == "upbit" else "")}
        for attempt in range(5):
            r = s.get(f"{BASE[ex]}/candles/minutes/1", params=par, timeout=20)
            time.sleep(pace)
            if r.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            break
        d = r.json() if r.status_code == 200 else []
        if not isinstance(d, list) or not d:
            break
        for k in d:
            ts = int(datetime.fromisoformat(k["candle_date_time_utc"]).replace(tzinfo=timezone.utc).timestamp())
            rows.append((ex, market[4:], ts, k["opening_price"], k["high_price"], k["low_price"], k["trade_price"],
                         k["candle_acc_trade_price"]))
        first = datetime.fromisoformat(d[-1]["candle_date_time_kst"]).replace(tzinfo=KST)
        if first.timestamp() <= since or len(d) < 200:
            break
        to = first
    if not rows:
        return 0
    df = pd.DataFrame(rows, columns=["exchange", "symbol", "ts", "o", "h", "l", "c", "value_krw"])
    df = df[df["ts"] >= since].drop_duplicates("ts").sort_values("ts")
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False, compression="zstd")
    return len(df)


def run_ex(ex: str, bases: set[str], since: int, pace: float) -> None:
    s = requests.Session()
    mk = [m["market"] for m in s.get(f"{BASE[ex]}/market/all", timeout=20).json()
          if m["market"].startswith("KRW-") and (m["market"][4:] in bases or m["market"][4:] == "USDT")]
    log.info("%s: %d markets", ex, len(mk))
    for i, m in enumerate(mk):
        try:
            n = one(ex, s, m, since, pace)
        except Exception as e:  # noqa: BLE001
            log.warning("%s %s: %r", ex, m, e)
            continue
        if i % 10 == 0:
            log.info("%s %d/%d %s %s", ex, i + 1, len(mk), m, n)
    log.info("%s done", ex)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    a = ap.parse_args()
    since = int(time.time()) - a.days * 86400
    bases = perp_bases()
    ts = [threading.Thread(target=run_ex, args=(ex, bases, since, 0.13)) for ex in ("upbit", "bithumb")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    log.info("korea 1m backfill done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
