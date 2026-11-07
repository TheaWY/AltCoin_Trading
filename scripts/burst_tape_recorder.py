"""Burst tape recorder (2026-10-03). Per-second Binance perp tape, but only for coins that just started moving.

Why: recording 570 perps at 1 s would fill the disk in a day and almost all of it would be noise. The moves we want to
understand (SAND/POD-class) all begin with a cheap, early trigger, so: watch the whole market with the free
!miniTicker@arr stream (one message per coin per second, no storage), and when a coin is up >= +3% over the last 5
minutes, open a dedicated aggTrade + bookTicker subscription for that coin and write 1-second bars for 4 hours:
  ts, mid, bid, ask, spread_bp, n_trades, buy_qv, sell_qv, max_trade_qv, last
About 14,400 rows per event (~0.5 MB parquet). Cap 30 recordings a day. Files: data/cache/burst_tape/<SYMBOL>_<ts>.parquet.
The library of recorded tapes (winners and fakes alike, labelled later from prices_1m) is the training set for the
per-second detector. Never places orders.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import defaultdict, deque
from pathlib import Path

import pandas as pd
import websockets

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/cache/burst_tape"; OUT.mkdir(parents=True, exist_ok=True)
TRIG, WINDOW_S, RECORD_S, MAX_PER_DAY, MAX_CONCURRENT = 0.03, 300, 4 * 3600, 30, 8
WS = "wss://fstream.binance.com/market/stream?streams="  # /market: the plain /ws path connects but sends nothing (same as collect_tick_bars)


class Recorder:
    def __init__(self, sym):
        self.sym, self.t0 = sym, time.time(); self.rows = []; self.cur = None; self.sec = None
        self.bid = self.ask = None

    def _flush(self):
        if self.cur:
            self.rows.append(self.cur)

    def on_trade(self, m):
        t = int(m["T"] // 1000); qv = float(m["p"]) * float(m["q"]); buy = not m["m"]      # m = buyer is maker -> sell aggressor
        if t != self.sec:
            self._flush(); self.sec = t
            self.cur = {"ts": t, "n_trades": 0, "buy_qv": 0.0, "sell_qv": 0.0, "max_trade_qv": 0.0, "last": float(m["p"]), "bid": self.bid, "ask": self.ask}
        c = self.cur; c["n_trades"] += 1; c["buy_qv" if buy else "sell_qv"] += qv; c["max_trade_qv"] = max(c["max_trade_qv"], qv); c["last"] = float(m["p"])

    def on_book(self, m):
        self.bid, self.ask = float(m["b"]), float(m["a"])
        if self.cur is not None:
            self.cur["bid"], self.cur["ask"] = self.bid, self.ask

    def save(self):
        self._flush()
        if not self.rows:
            return
        d = pd.DataFrame(self.rows); d["mid"] = (d.bid + d.ask) / 2; d["spread_bp"] = (d.ask - d.bid) / d.mid * 1e4
        d.to_parquet(OUT / f"{self.sym}_{int(self.t0)}.parquet", index=False)
        print(time.strftime("%F %T"), f"saved {self.sym} {len(d)} s", flush=True)


async def record(sym, active):
    rec = Recorder(sym)
    try:
        async with websockets.connect(f"{WS}{sym.lower()}@aggTrade/{sym.lower()}@bookTicker", ping_interval=20) as ws:
            while time.time() - rec.t0 < RECORD_S:
                try:
                    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=30)).get("data", {})
                except asyncio.TimeoutError:
                    continue
                if m.get("e") == "aggTrade":
                    rec.on_trade(m)
                elif m.get("e") == "bookTicker" or "b" in m and "a" in m:
                    rec.on_book(m)
    except Exception as e:  # noqa: BLE001
        print(time.strftime("%F %T"), "record error", sym, repr(e)[:100], flush=True)
    finally:
        rec.save(); active.discard(sym)


async def main():
    hist = defaultdict(lambda: deque(maxlen=WINDOW_S)); active = set(); started = deque()
    print(time.strftime("%F %T"), "burst tape recorder watching all perps", flush=True)
    while True:
        try:
            async with websockets.connect(f"{WS}!miniTicker@arr", ping_interval=20, max_size=2 ** 22) as ws:
                async for raw in ws:
                    now = time.time(); arr = json.loads(raw).get("data", [])
                    for m in arr:
                        s = m["s"]
                        if not s.endswith("USDT"):
                            continue
                        px = float(m["c"]); h = hist[s]; h.append((now, px))
                        if s in active or len(h) < 60:
                            continue
                        old = next((p for t, p in h if now - t <= WINDOW_S), None)
                        if old and px / old - 1 >= TRIG:
                            while started and now - started[0] > 86400:
                                started.popleft()
                            if len(started) >= MAX_PER_DAY or len(active) >= MAX_CONCURRENT:
                                continue
                            active.add(s); started.append(now)
                            print(time.strftime("%F %T"), f"TRIGGER {s} +{(px / old - 1) * 100:.1f}% / 5 min -> recording 4h", flush=True)
                            asyncio.create_task(record(s, active))
        except Exception as e:  # noqa: BLE001
            print(time.strftime("%F %T"), "stream error", repr(e)[:100], flush=True)
            await asyncio.sleep(5)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
