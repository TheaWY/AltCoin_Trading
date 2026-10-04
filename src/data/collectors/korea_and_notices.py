"""Upbit (Korean retail flow, kimchi premium) and exchange announcements.

upbit_1h           hourly KRW-market candles from Upbit's public API
                   (symbol stored Binance-style, e.g. 'SOL/USDT'; KRW-USDT is
                   kept as 'USDT/KRW' for the FX + premium baseline)
exchange_notices   Upbit notices (listings, warnings, delistings) and Binance
                   CMS articles (new listings, delistings), with the tickers
                   found in the title

Both APIs are public and keyless. Upbit quotation limit: 10 req/s.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)
UPBIT = "https://api.upbit.com/v1"
UPBIT_NOTICES = "https://api-manager.upbit.com/api/v1/announcements"
BINANCE_CMS = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
BINANCE_CATALOGS = {48: "listing", 161: "delisting"}

SCHEMAS = (
    """CREATE TABLE IF NOT EXISTS upbit_1h (
        symbol TEXT NOT NULL, ts BIGINT NOT NULL, open DOUBLE PRECISION, high DOUBLE PRECISION,
        low DOUBLE PRECISION, close DOUBLE PRECISION, volume DOUBLE PRECISION, value_krw DOUBLE PRECISION,
        PRIMARY KEY (symbol, ts))""",
    """CREATE TABLE IF NOT EXISTS exchange_notices (
        source TEXT NOT NULL, notice_id TEXT NOT NULL, ts BIGINT NOT NULL, category TEXT, kind TEXT,
        title TEXT, symbols TEXT, PRIMARY KEY (source, notice_id))""",
)
TICKER = re.compile(r"\(([A-Z0-9]{2,12})\)")


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


def krw_markets(session: requests.Session) -> list[str]:
    return [m["market"] for m in session.get(f"{UPBIT}/market/all", timeout=20).json() if m["market"].startswith("KRW-")]


def _sym(market: str) -> str:
    base = market.split("-", 1)[1]
    return "USDT/KRW" if base == "USDT" else f"{base}/USDT"


def fetch_hourly(session: requests.Session, market: str, count: int = 200, to: str | None = None) -> list[tuple]:
    params: dict[str, Any] = {"market": market, "count": count}
    if to:
        params["to"] = to
    for _ in range(3):
        r = session.get(f"{UPBIT}/candles/minutes/60", params=params, timeout=20)
        if r.status_code == 429:
            time.sleep(1)
            continue
        if r.status_code != 200:
            return []
        out = []
        for d in r.json():
            ts = int(datetime.fromisoformat(d["candle_date_time_utc"]).replace(tzinfo=timezone.utc).timestamp())
            out.append((_sym(market), ts, d["opening_price"], d["high_price"], d["low_price"], d["trade_price"],
                        d["candle_acc_trade_volume"], d["candle_acc_trade_price"]))
        return out
    return []


def insert_hourly(storage: Any, rows: list[tuple]) -> int:
    return _exec(storage, "INSERT INTO upbit_1h (symbol, ts, open, high, low, close, volume, value_krw) "
                          "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (symbol, ts) DO UPDATE SET high=EXCLUDED.high, "
                          "low=EXCLUDED.low, close=EXCLUDED.close, volume=EXCLUDED.volume, value_krw=EXCLUDED.value_krw",
                 rows)


def backfill_hourly(storage: Any, days: int, log=logger.info) -> None:
    s = requests.Session()
    markets = krw_markets(s)
    stop = int(time.time()) - days * 86400
    for i, m in enumerate(markets):
        to = None
        while True:
            rows = fetch_hourly(s, m, 200, to)
            time.sleep(0.12)
            if not rows:
                break
            insert_hourly(storage, rows)
            oldest = min(r[1] for r in rows)
            if oldest <= stop or len(rows) < 200:
                break
            to = datetime.fromtimestamp(oldest, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if i % 25 == 0:
            log(f"upbit backfill {i + 1}/{len(markets)} {m}")


def _kind(title: str) -> str:
    t = title
    if any(k in t for k in ("신규 거래지원", "마켓 추가", "Will List", "Will Add", "Launchpool", "Futures Will Launch")):
        return "listing"
    if any(k in t for k in ("거래지원 종료", "Delist", "delist")):
        return "delisting"
    if "유의 종목 지정 해제" in t or "유의종목 지정 해제" in t:
        return "warning_lifted"
    if "유의 종목" in t or "유의종목" in t or "주의" in t:
        return "warning"
    return "other"


def fetch_notices(session: requests.Session, page: int = 1) -> list[tuple]:
    rows = []
    try:
        d = session.get(UPBIT_NOTICES, params={"os": "web", "page": page, "per_page": 20, "category": "trade"},
                        timeout=20).json()
        for n in (d.get("data") or {}).get("notices", []):
            ts = int(datetime.fromisoformat(n["listed_at"]).timestamp())
            rows.append(("upbit", str(n["id"]), ts, n.get("category"), _kind(n["title"]), n["title"],
                         ",".join(TICKER.findall(n["title"]))))
    except Exception:  # noqa: BLE001
        logger.exception("upbit notices failed")
    for cat in BINANCE_CATALOGS:
        try:
            d = session.get(BINANCE_CMS, params={"type": 1, "catalogId": cat, "pageNo": page, "pageSize": 20},
                            timeout=20, headers={"User-Agent": "Mozilla/5.0"}).json()
            for c in (d.get("data") or {}).get("catalogs", []):
                for a in c.get("articles", []):
                    rows.append(("binance", str(a["id"]), int(a["releaseDate"]) // 1000, BINANCE_CATALOGS.get(cat),
                                 _kind(a["title"]), a["title"], ",".join(TICKER.findall(a["title"]))))
        except Exception:  # noqa: BLE001
            logger.exception("binance cms failed")
    return rows


def insert_notices(storage: Any, rows: list[tuple]) -> int:
    return _exec(storage, "INSERT INTO exchange_notices (source, notice_id, ts, category, kind, title, symbols) "
                          "VALUES (?,?,?,?,?,?,?) ON CONFLICT (source, notice_id) DO NOTHING", rows)
