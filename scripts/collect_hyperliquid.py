#!/usr/bin/env python3
"""Hyperliquid: real positions and real liquidation prices (public, no key).

Hyperliquid is on-chain, so every trade names both wallets. Three loops:

  harvest   websocket trades for every perp coin -> wallet addresses with the
            notional they traded (table hl_users, rolling 7-day notional)
  scan      every 10 minutes: clearinghouseState of the ~2,500 most active
            wallets (weight 2 each, kept under 1,000 weight/min) -> their open
            positions with entry, size, leverage and liquidation price.
            Per coin we store the liquidation map: USD of long positions that
            get liquidated at each % below the mark and of shorts at each %
            above (0.5% bins out to 30%), plus long/short totals and whale share
            -> hl_liqmap. The raw positions are kept hourly for 30 days -> hl_positions
  ctx       every 5 minutes: open interest, funding, mark, oracle, premium,
            24h volume per coin -> hl_ctx
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import requests
import websockets

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.data.storage import get_storage  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("hl")
INFO = "https://api.hyperliquid.xyz/info"
WS = "wss://api.hyperliquid.xyz/ws"
BINS = np.round(np.arange(0.5, 30.01, 0.5), 1)
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS hl_users (addr TEXT PRIMARY KEY, first_seen BIGINT, last_seen BIGINT,
       ntl_7d DOUBLE PRECISION, ntl_day DOUBLE PRECISION, day BIGINT)""",
    """CREATE TABLE IF NOT EXISTS hl_ctx (coin TEXT NOT NULL, ts BIGINT NOT NULL, mark DOUBLE PRECISION,
       oracle DOUBLE PRECISION, oi_usd DOUBLE PRECISION, funding DOUBLE PRECISION, premium DOUBLE PRECISION,
       vol_24h DOUBLE PRECISION, PRIMARY KEY (coin, ts))""",
    """CREATE TABLE IF NOT EXISTS hl_liqmap (coin TEXT NOT NULL, ts BIGINT NOT NULL, mark DOUBLE PRECISION,
       wallets INTEGER, long_usd DOUBLE PRECISION, short_usd DOUBLE PRECISION, n_long INTEGER, n_short INTEGER,
       avg_lev_long DOUBLE PRECISION, avg_lev_short DOUBLE PRECISION, top10_share DOUBLE PRECISION,
       long_liq JSON, short_liq JSON, PRIMARY KEY (coin, ts))""",
    """CREATE TABLE IF NOT EXISTS hl_positions (ts BIGINT NOT NULL, addr TEXT NOT NULL, coin TEXT NOT NULL,
       szi DOUBLE PRECISION, entry DOUBLE PRECISION, value_usd DOUBLE PRECISION, liq_px DOUBLE PRECISION,
       leverage DOUBLE PRECISION, upnl DOUBLE PRECISION, PRIMARY KEY (ts, addr, coin))""",
)
acc: dict[str, float] = defaultdict(float)      # addr -> notional since last flush
acc_lock = threading.Lock()


def post(body: dict, timeout: int = 30):
    for attempt in range(4):
        try:
            r = requests.post(INFO, json=body, timeout=timeout)
            if r.status_code == 429:
                time.sleep(20 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            time.sleep(5 * (attempt + 1))
    return None


def coins() -> list[str]:
    m = post({"type": "meta"}) or {}
    return [u["name"] for u in m.get("universe", []) if not u.get("isDelisted")]


async def harvest_ws() -> None:
    while True:
        try:
            cs = coins()
            async with websockets.connect(WS, max_size=None, ping_interval=30) as ws:
                for c in cs:
                    await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": c}}))
                log.info("harvest: subscribed %d coins", len(cs))
                async for raw in ws:
                    m = json.loads(raw)
                    if m.get("channel") != "trades":
                        continue
                    with acc_lock:
                        for t in m.get("data") or []:
                            ntl = float(t["px"]) * float(t["sz"])
                            for u in t.get("users") or []:
                                acc[u] += ntl
        except Exception as e:  # noqa: BLE001
            log.warning("harvest ws: %r; reconnecting", e)
            await asyncio.sleep(10)


def flush_users(storage) -> None:
    while True:
        time.sleep(60)
        with acc_lock:
            items = list(acc.items())
            acc.clear()
        if not items:
            continue
        now = int(time.time())
        day = now // 86400
        try:
            with storage._connect() as c:  # noqa: SLF001
                for a, n in items:
                    c.execute("INSERT INTO hl_users (addr, first_seen, last_seen, ntl_7d, ntl_day, day) VALUES (?,?,?,?,?,?) "
                              "ON CONFLICT (addr) DO UPDATE SET last_seen=EXCLUDED.last_seen, "
                              "ntl_7d = hl_users.ntl_7d * 0.99986 + EXCLUDED.ntl_7d, "   # ~7-day decay per minute
                              "ntl_day = CASE WHEN hl_users.day = EXCLUDED.day THEN hl_users.ntl_day + EXCLUDED.ntl_day ELSE EXCLUDED.ntl_day END, "
                              "day = EXCLUDED.day", (a, now, now, n, n, day))
        except Exception as e:  # noqa: BLE001
            log.warning("flush users: %r", e)


def ctx_loop(storage) -> None:
    while True:
        t0 = time.time()
        try:
            d = post({"type": "metaAndAssetCtxs"})
            if d:
                uni, ctxs = d[0]["universe"], d[1]
                ts = int(t0) // 300 * 300
                with storage._connect() as c:  # noqa: SLF001
                    for u, x in zip(uni, ctxs):
                        mark = float(x.get("markPx") or 0) or None
                        oi = float(x.get("openInterest") or 0) * (mark or 0)
                        c.execute("INSERT INTO hl_ctx VALUES (?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                                  (u["name"], ts, mark, float(x.get("oraclePx") or 0) or None, oi,
                                   float(x.get("funding") or 0), float(x.get("premium") or 0) if x.get("premium") else None,
                                   float(x.get("dayNtlVlm") or 0)))
        except Exception as e:  # noqa: BLE001
            log.warning("ctx: %r", e)
        time.sleep(max(10, 300 - (time.time() - t0)))


def scan_once(storage, n_wallets: int = 2500) -> dict:
    t0 = time.time()
    with storage._connect() as c:  # noqa: SLF001
        addrs = [r["addr"] for r in c.execute("SELECT addr FROM hl_users WHERE last_seen > ? ORDER BY ntl_7d DESC LIMIT ?",
                                              (int(t0) - 7 * 86400, n_wallets)).fetchall()]
    d = post({"type": "metaAndAssetCtxs"})
    marks = {u["name"]: float(x.get("markPx") or 0) for u, x in zip(d[0]["universe"], d[1])} if d else {}
    pos = []
    budget_t = time.time()
    for i, a in enumerate(addrs):
        st = post({"type": "clearinghouseState", "user": a}, timeout=20)
        for ap in (st or {}).get("assetPositions", []):
            p = ap.get("position", {})
            try:
                pos.append((a, p["coin"], float(p["szi"]), float(p.get("entryPx") or 0), float(p.get("positionValue") or 0),
                            float(p["liquidationPx"]) if p.get("liquidationPx") else None,
                            float((p.get("leverage") or {}).get("value") or 0), float(p.get("unrealizedPnl") or 0)))
            except (KeyError, TypeError, ValueError):
                continue
        # 2 weight per call, stay under ~1000 weight/min
        if (i + 1) % 480 == 0:
            spent = time.time() - budget_t
            if spent < 60:
                time.sleep(60 - spent)
            budget_t = time.time()
    ts = int(t0) // 600 * 600
    by = defaultdict(list)
    for r in pos:
        by[r[1]].append(r)
    rows = []
    for coin, ps in by.items():
        mark = marks.get(coin)
        if not mark:
            continue
        L = [p for p in ps if p[2] > 0]
        S = [p for p in ps if p[2] < 0]
        ll, sl = np.zeros(len(BINS)), np.zeros(len(BINS))
        for p in L:
            if p[5]:
                dist = (1 - p[5] / mark) * 100
                k = np.searchsorted(BINS, dist)
                if 0 <= dist and k < len(BINS):
                    ll[k] += p[4]
        for p in S:
            if p[5]:
                dist = (p[5] / mark - 1) * 100
                k = np.searchsorted(BINS, dist)
                if 0 <= dist and k < len(BINS):
                    sl[k] += p[4]
        vals = sorted((p[4] for p in ps), reverse=True)
        tot = sum(vals) or 1
        lev = lambda xs: float(np.average([p[6] for p in xs], weights=[p[4] for p in xs])) if xs and sum(p[4] for p in xs) > 0 else None  # noqa: E731
        rows.append((coin, ts, mark, len(ps), sum(p[4] for p in L), sum(p[4] for p in S), len(L), len(S), lev(L), lev(S),
                     sum(vals[:10]) / tot, json.dumps([round(x) for x in ll]), json.dumps([round(x) for x in sl])))
    with storage._connect() as c:  # noqa: SLF001
        for r in rows:
            c.execute("INSERT INTO hl_liqmap VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING", r)
        if ts % 3600 == 0:
            for p in pos:
                c.execute("INSERT INTO hl_positions VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING", (ts, *p))
            c.execute("DELETE FROM hl_positions WHERE ts < ?", (ts - 30 * 86400,))
    return {"wallets": len(addrs), "positions": len(pos), "coins": len(rows), "secs": round(time.time() - t0)}


def scan_loop(storage) -> None:
    time.sleep(120)            # let the harvester collect some wallets first
    while True:
        t0 = time.time()
        try:
            log.info("scan: %s", scan_once(storage))
        except Exception as e:  # noqa: BLE001
            log.warning("scan: %r", e)
        time.sleep(max(30, 600 - (time.time() - t0)))


def main() -> int:
    storage = get_storage()
    with storage._connect() as c:  # noqa: SLF001
        for s in SCHEMA:
            c.execute(s)
    for fn in (flush_users, ctx_loop, scan_loop):
        threading.Thread(target=fn, args=(storage,), daemon=True, name=fn.__name__).start()
    asyncio.run(harvest_ws())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
