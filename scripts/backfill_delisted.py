#!/usr/bin/env python3
"""Remove survivorship bias: load hourly klines and 5m metrics for USDT perps
that traded during the research window but are no longer listed.

The Binance archive keeps every symbol that ever existed. We list them from
the archive's S3 index, keep USDT perps not in today's exchangeInfo, and load
  prices (timeframe '1h_perp')  from monthly/daily 1h klines
  metrics_5m                    from daily metrics files

    .venv/bin/python scripts/backfill_delisted.py --days 185
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import re
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backfill_binance_vision import SCHEMA, fetch as fetch_metrics  # noqa: E402
from src.data.collectors import klines_1m as k1  # noqa: E402
from src.data.storage import get_storage  # noqa: E402

S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
DATA = "https://data.binance.vision"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("delisted")


def all_archive_symbols(session: requests.Session) -> list[str]:
    out, marker = [], ""
    while True:
        r = session.get(S3, params={"delimiter": "/", "prefix": "data/futures/um/monthly/klines/", "marker": marker},
                        timeout=30)
        pre = re.findall(r"<Prefix>data/futures/um/monthly/klines/([^/<]+)/</Prefix>", r.text)
        out += pre
        nxt = re.search(r"<NextMarker>([^<]*)</NextMarker>", r.text)
        if not nxt or "<IsTruncated>true" not in r.text:
            break
        marker = nxt.group(1)
    return sorted(set(out))


def klines(session: requests.Session, code: str, url: str) -> list[tuple]:
    r = session.get(url, timeout=30)
    if r.status_code != 200:
        return []
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        text = z.read(z.namelist()[0]).decode()
    rows = []
    for rec in csv.reader(io.StringIO(text)):
        if not rec or not rec[0].isdigit():
            continue
        ts = int(rec[0]) // 1000
        rows.append((f"{code[:-4]}/USDT", ts, "1h_perp", float(rec[1]), float(rec[2]), float(rec[3]), float(rec[4]),
                     float(rec[5]), float(rec[7]), int(float(rec[8]))))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=185)
    args = ap.parse_args()
    storage = get_storage()
    s = requests.Session()
    s.mount("https://", requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=16))
    live = {x.replace("/", "") for x in k1.usdt_perps()}
    codes = [c for c in all_archive_symbols(s) if c.endswith("USDT") and c not in live]
    log.info("archive symbols not listed today: %d", len(codes))
    start = date.today() - timedelta(days=args.days)
    months = sorted({(d.year, d.month) for d in (start + timedelta(days=i) for i in range(args.days + 1))})
    urls = [(c, f"{DATA}/data/futures/um/monthly/klines/{c}/1h/{c}-1h-{y}-{m:02d}.zip") for c in codes for y, m in months]
    kl_sql = ("INSERT INTO prices (symbol, timestamp, timeframe, open, high, low, close, volume, quote_volume, num_trades) "
              "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (symbol, timestamp, timeframe) DO NOTHING")
    n = 0
    with ThreadPoolExecutor(16) as ex:
        for rows in ex.map(lambda cu: klines(s, *cu), urls):
            if rows:
                with storage._connect() as conn:  # noqa: SLF001
                    with conn.raw.cursor() as cur:
                        cur.executemany(kl_sql, rows)
                n += len(rows)
    log.info("klines: %d hourly rows for %d delisted symbols", n, len(codes))
    # metrics only for symbols that actually had klines in the window
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute(SCHEMA)
        had = [dict(r)["symbol"] for r in conn.execute(
            "SELECT DISTINCT symbol FROM prices WHERE timeframe='1h_perp' AND timestamp >= %s AND symbol = ANY(%s)"
            .replace("%s", "?"), (int(time.time()) - args.days * 86400, [f"{c[:-4]}/USDT" for c in codes])).fetchall()]
    today = datetime.now(timezone.utc).date()
    jobs = [(sym, (today - timedelta(days=i)).isoformat()) for sym in had for i in range(1, args.days + 1)]
    m_sql = ("INSERT INTO metrics_5m (symbol, ts, oi, oi_usd, ls_top_acct, ls_top_pos, ls_global, taker_ratio) "
             "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (symbol, ts) DO NOTHING")
    m = 0
    with ThreadPoolExecutor(16) as ex:
        buf = []
        for rows in ex.map(lambda j: fetch_metrics(s, *j), jobs):
            buf += rows
            if len(buf) > 20000:
                with storage._connect() as conn:  # noqa: SLF001
                    with conn.raw.cursor() as cur:
                        cur.executemany(m_sql, buf)
                m += len(buf)
                buf = []
        if buf:
            with storage._connect() as conn:  # noqa: SLF001
                with conn.raw.cursor() as cur:
                    cur.executemany(m_sql, buf)
            m += len(buf)
    log.info("delisted done: %d symbols with prices, %d metrics rows", len(had), m)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
