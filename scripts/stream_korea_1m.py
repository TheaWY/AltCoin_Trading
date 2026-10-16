#!/usr/bin/env python3
"""Real-time 1-minute bars for every KRW market on Upbit and Bithumb, built
from their public trade websockets (no key). Each bar carries the taker-buy
share (trades where the buyer hit the ask) that candles do not give, plus
Upbit's top-of-book depth (bid vs ask KRW in the visible orderbook).

Table kr_1m (exchange, symbol, ts, o h l c, value_krw, buy_krw, trades,
ob_bid_krw, ob_ask_krw). symbol is the bare base asset ("BTC"). Rows older
than KEEP_DAYS are moved to parquet (data/cache/kr1m/<exchange>/<day>.parquet)
once a day so Postgres stays small.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
import uuid
from pathlib import Path

import pandas as pd
import requests
import websockets

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from src.data.storage import get_storage  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("kr1m")
KEEP_DAYS = 3
ARCH = PROJECT_ROOT / "data" / "cache" / "kr1m"
SCHEMA = """CREATE TABLE IF NOT EXISTS kr_1m (exchange TEXT NOT NULL, symbol TEXT NOT NULL, ts BIGINT NOT NULL,
    o DOUBLE PRECISION, h DOUBLE PRECISION, l DOUBLE PRECISION, c DOUBLE PRECISION, value_krw DOUBLE PRECISION,
    buy_krw DOUBLE PRECISION, trades INTEGER, ob_bid_krw DOUBLE PRECISION, ob_ask_krw DOUBLE PRECISION,
    PRIMARY KEY (exchange, symbol, ts))"""
EX = {"upbit": ("wss://api.upbit.com/websocket/v1", "https://api.upbit.com/v1/market/all"),
      "bithumb": ("wss://ws-api.bithumb.com/websocket/v1", "https://api.bithumb.com/v1/market/all")}

bars: dict[tuple[str, str, int], list] = {}   # (ex, sym, minute) -> [o,h,l,c,val,buy,n]
book: dict[tuple[str, str], tuple[float, float]] = {}


def markets(ex: str) -> list[str]:
    return [m["market"] for m in requests.get(EX[ex][1], timeout=20).json() if m["market"].startswith("KRW-")]


async def run_ex(ex: str) -> None:
    while True:
        try:
            codes = markets(ex)
            subs = [{"ticket": str(uuid.uuid4())}, {"type": "trade", "codes": codes, "is_only_realtime": True}]
            if ex == "upbit":
                subs.append({"type": "orderbook", "codes": codes, "is_only_realtime": True})
            subs.append({"format": "SIMPLE"})
            async with websockets.connect(EX[ex][0], max_size=None, ping_interval=60, open_timeout=20) as ws:
                await ws.send(json.dumps(subs))
                log.info("%s: subscribed %d markets", ex, len(codes))
                async for raw in ws:
                    m = json.loads(raw)
                    if m.get("st") == "SNAPSHOT":        # replayed past trades would land in old minutes
                        continue
                    sym = str(m.get("cd", ""))[4:]
                    if m.get("ty") == "trade":
                        p, v = float(m["tp"]), float(m["tv"])
                        k = (ex, sym, int(m["ttms"]) // 60000 * 60)
                        b = bars.get(k)
                        if b is None:
                            bars[k] = b = [p, p, p, p, 0.0, 0.0, 0]
                        b[1], b[2], b[3] = max(b[1], p), min(b[2], p), p
                        b[4] += p * v
                        if m.get("ab") == "BID":
                            b[5] += p * v
                        b[6] += 1
                    elif m.get("ty") == "orderbook":
                        units = m.get("obu") or []
                        book[(ex, sym)] = (sum(u["bp"] * u["bs"] for u in units), sum(u["ap"] * u["as"] for u in units))
        except Exception as e:  # noqa: BLE001
            log.warning("%s websocket: %r; reconnecting", ex, e)
            await asyncio.sleep(5)


def archive(storage) -> None:
    cutoff = (int(time.time()) // 86400 - KEEP_DAYS) * 86400
    with storage._connect() as c:  # noqa: SLF001
        days = [r[0] for r in c.execute("SELECT DISTINCT ts/86400 FROM kr_1m WHERE ts < ?", (cutoff,)).fetchall()]
    for d in days:
        with storage._connect() as c:  # noqa: SLF001
            cur = c.execute("SELECT * FROM kr_1m WHERE ts >= ? AND ts < ?", (d * 86400, d * 86400 + 86400))
            cols = [x[0] for x in cur.description]
            df = pd.DataFrame.from_records(cur.fetchall(), columns=cols)
        for ex, g in df.groupby("exchange"):
            out = ARCH / ex
            out.mkdir(parents=True, exist_ok=True)
            day = pd.Timestamp(d * 86400, unit="s").strftime("%Y-%m-%d")
            path = out / f"{day}.parquet"
            if path.exists():
                g = pd.concat([pd.read_parquet(path), g]).drop_duplicates(["symbol", "ts"], keep="last")
            g.to_parquet(path, index=False, compression="zstd")
        with storage._connect() as c:  # noqa: SLF001
            c.execute("DELETE FROM kr_1m WHERE ts >= ? AND ts < ?", (d * 86400, d * 86400 + 86400))
        log.info("archived day %s (%d rows)", d, len(df))


async def flusher(storage) -> None:
    last_arch = 0.0
    while True:
        await asyncio.sleep(60 - time.time() % 60 + 2)
        done_before = int(time.time()) // 60 * 60            # bars for minutes < now are complete
        ready = [k for k in list(bars) if k[2] < done_before]
        rows = []
        for k in ready:
            o, h, l_, c_, val, buy, n = bars.pop(k)
            bid, ask = book.get((k[0], k[1]), (None, None))
            rows.append((k[0], k[1], k[2], o, h, l_, c_, val, buy, n, bid, ask))
        if rows:
            try:
                with storage._connect() as c:  # noqa: SLF001
                    for r in rows:
                        c.execute("INSERT INTO kr_1m VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING", r)
            except Exception as e:  # noqa: BLE001
                log.warning("flush failed: %r", e)
        if time.time() - last_arch > 6 * 3600:
            last_arch = time.time()
            try:
                await asyncio.to_thread(archive, storage)
            except Exception as e:  # noqa: BLE001
                log.warning("archive failed: %r", e)


async def main() -> None:
    storage = get_storage()
    with storage._connect() as c:  # noqa: SLF001
        c.execute(SCHEMA)
    await asyncio.gather(run_ex("upbit"), run_ex("bithumb"), flusher(storage))


if __name__ == "__main__":
    asyncio.run(main())
