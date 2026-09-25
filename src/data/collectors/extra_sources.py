"""More free, keyless data sources.

cg_daily        CoinGecko daily market cap / volume / price per coin (point-in-
                time market cap, incl. coins Binance has since delisted)
cg_trending     CoinGecko trending list, hourly (search interest proxy)
fng_daily       Crypto Fear & Greed index (alternative.me), full history
bithumb_1h      Bithumb hourly KRW candles (the API only serves ~8 days, so
                history starts when collection starts)
coinbase_1h     Coinbase BTC-USD / ETH-USD hourly candles -> Coinbase premium
                (US spot demand) vs Binance

CoinGecko without a key allows roughly 5-30 calls per minute; everything here
paces itself and backs off on 429. With a free Demo key in .env
(COINGECKO_API_KEY) the same calls go through the demo endpoint faster.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)
CG = "https://api.coingecko.com/api/v3"

SCHEMAS = (
    "CREATE TABLE IF NOT EXISTS cg_daily (symbol TEXT NOT NULL, cg_id TEXT, ts BIGINT NOT NULL, mcap DOUBLE PRECISION, "
    "volume DOUBLE PRECISION, price DOUBLE PRECISION, PRIMARY KEY (symbol, ts))",
    "CREATE TABLE IF NOT EXISTS cg_trending (ts BIGINT NOT NULL, rank INTEGER NOT NULL, cg_id TEXT, symbol TEXT, "
    "PRIMARY KEY (ts, rank))",
    "CREATE TABLE IF NOT EXISTS fng_daily (ts BIGINT PRIMARY KEY, value INTEGER, label TEXT)",
    "CREATE TABLE IF NOT EXISTS bithumb_1h (symbol TEXT NOT NULL, ts BIGINT NOT NULL, open DOUBLE PRECISION, "
    "close DOUBLE PRECISION, high DOUBLE PRECISION, low DOUBLE PRECISION, volume DOUBLE PRECISION, PRIMARY KEY (symbol, ts))",
    "CREATE TABLE IF NOT EXISTS coinbase_1h (product TEXT NOT NULL, ts BIGINT NOT NULL, open DOUBLE PRECISION, "
    "high DOUBLE PRECISION, low DOUBLE PRECISION, close DOUBLE PRECISION, volume DOUBLE PRECISION, PRIMARY KEY (product, ts))",
)


def ensure_schema(storage: Any) -> None:
    with storage._connect() as c:  # noqa: SLF001
        for s in SCHEMAS:
            c.execute(s)


def _exec(storage: Any, sql: str, rows: list[tuple]) -> int:
    if not rows:
        return 0
    with storage._connect() as c:  # noqa: SLF001
        with c.raw.cursor() as cur:
            cur.executemany(sql.replace("?", "%s"), rows)
    return len(rows)


class CoinGecko:
    def __init__(self, pause: float | None = None):
        self.s = requests.Session()
        key = os.getenv("COINGECKO_API_KEY", "").strip()
        self.params = {"x_cg_demo_api_key": key} if key else {}
        self.pause = pause if pause is not None else (2.2 if key else 6.5)

    def get(self, path: str, **params) -> Any:
        for attempt in range(6):
            r = self.s.get(f"{CG}{path}", params={**params, **self.params}, timeout=30)
            time.sleep(self.pause)
            if r.status_code == 429:
                time.sleep(30 * (attempt + 1))
                continue
            if r.status_code != 200:
                return None
            return r.json()
        return None


def map_ids(cg: CoinGecko, symbols: list[str], pages: int = 10) -> dict[str, str]:
    """Binance symbol -> CoinGecko id. The largest coin by market cap wins a
    ticker; Binance's 1000-prefixed contracts map to the base coin."""
    best: dict[str, tuple[float, str]] = {}
    for page in range(1, pages + 1):
        data = cg.get("/coins/markets", vs_currency="usd", order="market_cap_desc", per_page=250, page=page) or []
        for c in data:
            t = str(c.get("symbol", "")).upper()
            mc = float(c.get("market_cap") or 0)
            if t not in best or mc > best[t][0]:
                best[t] = (mc, c["id"])
        if len(data) < 250:
            break
    listing = cg.get("/coins/list") or []
    by_sym: dict[str, list[str]] = {}
    for c in listing:
        by_sym.setdefault(str(c.get("symbol", "")).upper(), []).append(c["id"])
    out = {}
    for sym in symbols:
        base = sym.split("/")[0]
        for cand in (base, base.removeprefix("1000000"), base.removeprefix("1000"), base.removeprefix("1M")):
            if cand in best:
                out[sym] = best[cand][1]
                break
            if len(by_sym.get(cand, [])) == 1:        # unambiguous smaller coin
                out[sym] = by_sym[cand][0]
                break
    return out


def backfill_mcap(storage: Any, cg: CoinGecko, ids: dict[str, str], days: int = 200, log=logger.info) -> None:
    sql = ("INSERT INTO cg_daily (symbol, cg_id, ts, mcap, volume, price) VALUES (?,?,?,?,?,?) "
           "ON CONFLICT (symbol, ts) DO UPDATE SET mcap=EXCLUDED.mcap, volume=EXCLUDED.volume, price=EXCLUDED.price")
    for i, (sym, cid) in enumerate(sorted(ids.items())):
        d = cg.get(f"/coins/{cid}/market_chart", vs_currency="usd", days=days, interval="daily")
        if not d:
            continue
        vol = {int(t) // 86_400_000: v for t, v in d.get("total_volumes", [])}
        px = {int(t) // 86_400_000: v for t, v in d.get("prices", [])}
        rows = [(sym, cid, (int(t) // 86_400_000) * 86400, mc, vol.get(int(t) // 86_400_000), px.get(int(t) // 86_400_000))
                for t, mc in d.get("market_caps", [])]
        _exec(storage, sql, rows)
        if i % 25 == 0:
            log(f"coingecko mcap {i + 1}/{len(ids)} {sym}")


def trending(storage: Any, cg: CoinGecko) -> int:
    d = cg.get("/search/trending") or {}
    ts = int(time.time()) // 3600 * 3600
    rows = [(ts, i + 1, c["item"]["id"], str(c["item"]["symbol"]).upper()) for i, c in enumerate(d.get("coins", []))]
    return _exec(storage, "INSERT INTO cg_trending (ts, rank, cg_id, symbol) VALUES (?,?,?,?) ON CONFLICT DO NOTHING", rows)


def fear_greed(storage: Any, limit: int = 0) -> int:
    d = requests.get("https://api.alternative.me/fng/", params={"limit": limit}, timeout=30).json()
    rows = [(int(x["timestamp"]), int(x["value"]), x["value_classification"]) for x in d.get("data", [])]
    return _exec(storage, "INSERT INTO fng_daily (ts, value, label) VALUES (?,?,?) ON CONFLICT (ts) DO NOTHING", rows)


def bithumb(storage: Any) -> int:
    s = requests.Session()
    coins = [c for c in (s.get("https://api.bithumb.com/public/ticker/ALL_KRW", timeout=20).json().get("data") or {})
             if c != "date"]
    rows = []
    for c in coins:
        try:
            d = s.get(f"https://api.bithumb.com/public/candlestick/{c}_KRW/1h", timeout=20).json().get("data") or []
        except Exception:  # noqa: BLE001
            continue
        sym = "USDT/KRW" if c == "USDT" else f"{c}/USDT"
        rows += [(sym, int(k[0]) // 1000, float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])) for k in d[-48:]]
        time.sleep(0.07)
    return _exec(storage, "INSERT INTO bithumb_1h (symbol, ts, open, close, high, low, volume) VALUES (?,?,?,?,?,?,?) "
                          "ON CONFLICT (symbol, ts) DO UPDATE SET close=EXCLUDED.close, high=EXCLUDED.high, "
                          "low=EXCLUDED.low, volume=EXCLUDED.volume", rows)


def coinbase(storage: Any, days: int = 2) -> int:
    s = requests.Session()
    now = int(time.time()) // 3600 * 3600
    n = 0
    for prod in ("BTC-USD", "ETH-USD"):
        end = now
        while end > now - days * 86400:
            start = end - 300 * 3600
            d = s.get(f"https://api.exchange.coinbase.com/products/{prod}/candles", timeout=20, params={
                "granularity": 3600, "start": datetime.fromtimestamp(start, tz=timezone.utc).isoformat(),
                "end": datetime.fromtimestamp(end, tz=timezone.utc).isoformat()}).json()
            if not isinstance(d, list) or not d:
                break
            n += _exec(storage, "INSERT INTO coinbase_1h (product, ts, low, high, open, close, volume) VALUES (?,?,?,?,?,?,?) "
                                "ON CONFLICT (product, ts) DO UPDATE SET close=EXCLUDED.close, high=EXCLUDED.high, "
                                "low=EXCLUDED.low, volume=EXCLUDED.volume",
                       [(prod, int(k[0]), k[1], k[2], k[3], k[4], k[5]) for k in d])
            end = start
            time.sleep(0.4)
    return n
