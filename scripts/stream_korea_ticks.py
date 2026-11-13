#!/usr/bin/env python3
"""Tick recorder for Upbit and Bithumb KRW markets (public websockets, no key). launchd: com.altcoin.koreaticks.
No bulk tick history exists for either exchange, so B17 families F07/F16/F17 need this running for weeks first.

Writes hourly parquet files (flushed every 60 s, whole hour rewritten atomically):
  data/cache/kr_ticks/{upbit,bithumb}/trades/YYYYMMDD_HH.parquet   ts_ms, ts_us, code, price, qty, side(1=ask hit/buy), seq
  data/cache/kr_ticks/{upbit,bithumb}/book/YYYYMMDD_HH.parquet     ts_ms, ts_us, code, b1..b5 px/qty, a1..a5 px/qty, tb, ta
    Upbit book throttled to 1 per 5 s per code; Bithumb book kept at full rate (>= 5 ms apart) with microsecond timestamps.
  Files before 2026-10-01 21:00 KST have no ts_us and Bithumb ts_ms holds MICROseconds: read with scripts/kr_ticks_io.py.
Disk: Bithumb book ~1.5-3 GB/day (zstd), Upbit ~0.2 GB/day. Reconnects forever; never raises."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

import pandas as pd
import requests
import websockets

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/cache/kr_ticks"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("kr_ticks")
EX = {"upbit": ("wss://api.upbit.com/websocket/v1", "https://api.upbit.com/v1/market/all"),
      "bithumb": ("wss://ws-api.bithumb.com/websocket/v1", "https://api.bithumb.com/v1/market/all")}


def us(t) -> int:
    """Exchange timestamp -> integer microseconds. Bithumb sends microseconds (kept at full precision), Upbit milliseconds."""
    t = int(t)
    return t if t > 10 ** 14 else t * 1000


def markets(ex):
    r = requests.get(EX[ex][1], params={"isDetails": "false"}, timeout=20).json()
    return sorted(m["market"] for m in r if m["market"].startswith("KRW-"))


class Sink:
    def __init__(self, ex):
        self.ex, self.tr, self.bk, self.hour, self.last_book = ex, [], [], None, {}

    def flush(self, force=False):
        h = time.strftime("%Y%m%d_%H", time.gmtime())
        if self.hour is None:
            self.hour = h
        if h != self.hour or force:
            self._write(self.hour); self.tr, self.bk, self.hour = [], [], h
        else:
            self._write(h)

    def _write(self, h):
        for kind, rows in (("trades", self.tr), ("book", self.bk)):
            if not rows:
                continue
            d = OUT / self.ex / kind; d.mkdir(parents=True, exist_ok=True)
            tmp = d / f".{h}.tmp"
            pd.DataFrame(rows).to_parquet(tmp, index=False, compression="zstd")
            os.replace(tmp, d / f"{h}.parquet")

    def on(self, m):
        t = m.get("type") or m.get("ty")
        if t == "trade":
            t_us = us(m.get("trade_timestamp") or m.get("ttms"))
            self.tr.append(dict(ts_ms=t_us // 1000, ts_us=t_us, code=m.get("code") or m.get("cd"), price=float(m.get("trade_price") or m.get("tp")),
                                qty=float(m.get("trade_volume") or m.get("tv")), side=int((m.get("ask_bid") or m.get("ab")) == "BID"), seq=int(m.get("sequential_id") or m.get("sid") or 0)))
        elif t == "orderbook":
            code, t_us = m.get("code") or m.get("cd"), us(m.get("timestamp") or m.get("tms"))
            # Upbit: 1 book per 5 s per code. Bithumb: every update kept (min 5 ms apart) at microsecond resolution, by request.
            gap = 5_000 if self.ex == "bithumb" else 5_000_000
            if t_us - self.last_book.get(code, 0) < gap:
                return
            self.last_book[code] = t_us
            ts = t_us // 1000
            u = (m.get("orderbook_units") or m.get("obu"))[:5]
            row = dict(ts_ms=ts, ts_us=t_us, code=code, tb=float(m.get("total_bid_size") or m.get("tbs") or 0), ta=float(m.get("total_ask_size") or m.get("tas") or 0))
            for i, x in enumerate(u, 1):
                row[f"b{i}"] = float(x.get("bid_price") or x.get("bp")); row[f"bq{i}"] = float(x.get("bid_size") or x.get("bs"))
                row[f"a{i}"] = float(x.get("ask_price") or x.get("ap")); row[f"aq{i}"] = float(x.get("ask_size") or x.get("as"))
            self.bk.append(row)


async def run(ex):
    sink = Sink(ex)
    while True:
        try:
            codes = markets(ex)
            sub = [{"ticket": f"altcoin-{ex}"}, {"type": "trade", "codes": codes}, {"type": "orderbook", "codes": codes, "level": 0}, {"format": "DEFAULT"}]
            async with websockets.connect(EX[ex][0], ping_interval=30, max_size=2**22) as ws:
                await ws.send(json.dumps(sub)); log.info("%s subscribed %d markets", ex, len(codes))
                last = time.time()
                async for raw in ws:
                    try:
                        sink.on(json.loads(raw))
                    except Exception as e:  # noqa: BLE001
                        log.debug("parse %s", e)
                    if time.time() - last > 60:
                        sink.flush(); last = time.time()
        except Exception as e:  # noqa: BLE001
            log.warning("%s reconnect: %s", ex, e); sink.flush(force=True); await asyncio.sleep(5)


async def main():
    await asyncio.gather(run("upbit"), run("bithumb"))


if __name__ == "__main__":
    asyncio.run(main())
