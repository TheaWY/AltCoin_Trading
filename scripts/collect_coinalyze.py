#!/usr/bin/env python3
"""Coinalyze (free API, 40 calls/min): hourly open interest, liquidations,
funding, long/short ratio and buy/sell volume for every perp of our coins on
Binance, Bybit, OKX, Hyperliquid, Gate and HTX -> table coinalyze_1h.

  --backfill   pull the full intraday history the API keeps (~80-90 days hourly)
  (default)    live loop: every hour at :07 refresh the last 8 hours

Up to 20 symbols per call; values converted to USD by the API. Key:
COINALYZE_API_KEY in .env (never printed). Data source: coinalyze.net.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.data.storage import get_storage  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("coinalyze")
API = "https://api.coinalyze.net/v1"
EXCH = {"A": "binance", "6": "bybit", "3": "okx", "H": "hyperliquid", "Y": "gate", "4": "htx"}
SCHEMA = """CREATE TABLE IF NOT EXISTS coinalyze_1h (symbol TEXT NOT NULL, exchange TEXT, base TEXT, ts BIGINT NOT NULL,
    oi_usd DOUBLE PRECISION, liq_long DOUBLE PRECISION, liq_short DOUBLE PRECISION, funding DOUBLE PRECISION,
    ls_ratio DOUBLE PRECISION, close DOUBLE PRECISION, volume DOUBLE PRECISION, buy_volume DOUBLE PRECISION,
    trades DOUBLE PRECISION, buy_trades DOUBLE PRECISION, PRIMARY KEY (symbol, ts))"""
PAUSE = 1.6


def key() -> str:
    k = os.getenv("COINALYZE_API_KEY")
    if not k:
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith("COINALYZE_API_KEY="):
                k = line.split("=", 1)[1].strip()
    return k


class Client:
    def __init__(self) -> None:
        self.s = requests.Session()
        self.s.headers["api_key"] = key()

    def get(self, path: str, **params):
        for attempt in range(6):
            r = self.s.get(f"{API}/{path}", params=params, timeout=60)
            time.sleep(PAUSE)
            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After", 30))
                time.sleep(wait + 1)
                continue
            if r.status_code >= 500:
                time.sleep(10 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        return []


def markets(c: Client, storage) -> list[dict]:
    with storage._connect() as conn:  # noqa: SLF001
        bases = {r["symbol"].split("/")[0] for r in conn.execute(
            "SELECT DISTINCT symbol FROM prices_1m WHERE ts > ?", (int(time.time()) - 2 * 86400,)).fetchall()}
    bases |= {b.removeprefix("1000").removeprefix("1000000") for b in bases}
    out = []
    for m in c.get("future-markets"):
        if (m.get("is_perpetual") and m.get("exchange") in EXCH and m.get("margined") == "STABLE"
                and m.get("base_asset", "").upper() in bases):
            out.append(m)
    return out


COLS = {
    "open-interest-history": lambda h: {"oi_usd": h.get("c")},
    "liquidation-history": lambda h: {"liq_long": h.get("l"), "liq_short": h.get("s")},
    "funding-rate-history": lambda h: {"funding": h.get("c")},
    "long-short-ratio-history": lambda h: {"ls_ratio": h.get("r")},
    "ohlcv-history": lambda h: {"close": h.get("c"), "volume": h.get("v"), "buy_volume": h.get("bv"),
                                "trades": h.get("tx"), "buy_trades": h.get("btx")},
}


def pull(c: Client, storage, mk: list[dict], hours: int) -> int:
    meta = {m["symbol"]: m for m in mk}
    syms = sorted(meta)
    now = int(time.time())
    n = 0
    for ep, fn in COLS.items():
        for i in range(0, len(syms), 20):
            chunk = syms[i:i + 20]
            params = {"symbols": ",".join(chunk), "interval": "1hour", "from": now - hours * 3600, "to": now}
            if ep in ("open-interest-history", "liquidation-history", "ohlcv-history"):
                params["convert_to_usd"] = "true"
            try:
                data = c.get(ep, **params) or []
            except requests.RequestException as e:
                log.warning("%s %s: %r", ep, chunk[0], e)
                continue
            rows = []
            for item in data:
                s = item.get("symbol")
                m = meta.get(s, {})
                for h in item.get("history") or []:
                    vals = fn(h)
                    rows.append((s, EXCH.get(m.get("exchange"), m.get("exchange")), m.get("base_asset"), int(h["t"]), vals))
            if not rows:
                continue
            cols = list(rows[0][4].keys())
            sql = (f"INSERT INTO coinalyze_1h (symbol, exchange, base, ts, {', '.join(cols)}) VALUES (?,?,?,?,{','.join('?' * len(cols))}) "
                   f"ON CONFLICT (symbol, ts) DO UPDATE SET {', '.join(f'{k}=EXCLUDED.{k}' for k in cols)}")
            with storage._connect() as conn:  # noqa: SLF001
                cur = conn.raw.cursor()
                cur.executemany(sql.replace("?", "%s"), [(s, ex, b, t, *[v[k] for k in cols]) for s, ex, b, t, v in rows])
            n += len(rows)
        log.info("%s: done (%d rows so far)", ep, n)
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true")
    a = ap.parse_args()
    if not key():
        log.error("COINALYZE_API_KEY missing")
        return 1
    storage = get_storage()
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute(SCHEMA)
        conn.execute("CREATE INDEX IF NOT EXISTS coinalyze_1h_base_ts ON coinalyze_1h (base, ts)")
    c = Client()
    mk = markets(c, storage)
    log.info("%d markets (%s)", len(mk), ", ".join(sorted({EXCH[m['exchange']] for m in mk})))
    if a.backfill:
        log.info("backfill: %d rows", pull(c, storage, mk, 2200))
        return 0
    while True:
        t0 = time.time()
        try:
            if int(t0) // 86400 != getattr(main, "_day", None):
                mk = markets(c, storage)
                main._day = int(t0) // 86400
            log.info("live: %d rows", pull(c, storage, mk, 8))
        except Exception as e:  # noqa: BLE001
            log.warning("live pull failed: %r", e)
        nxt = (int(time.time()) // 3600 + 1) * 3600 + 7 * 60
        time.sleep(max(60, nxt - time.time()))


if __name__ == "__main__":
    raise SystemExit(main())
