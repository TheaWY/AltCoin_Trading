"""Free-data backfills for B8-B13 (all public, no keys, no accounts). One entry point per dataset:
  python scripts/backfill_free_data.py bithumb1h   -> data/cache/bithumb1h_hist/{BASE}.parquet   (DL3)
  python scripts/backfill_free_data.py spot1h      -> data/cache/spot1h_hist/{SYM}.parquet       (DL4)
  python scripts/backfill_free_data.py depth1h     -> data/cache/depth1h/{SYM}.parquet           (DL5, 2024-03..2026-03)
  python scripts/backfill_free_data.py notices     -> data/cache/notices/{exchange}.parquet      (DL6)
  python scripts/backfill_free_data.py unlocks     -> data/cache/unlocks/*                       (DL7)
  python scripts/backfill_free_data.py coingecko   -> data/cache/cg_hist/{cg_id}.parquet         (DL8)
  python scripts/backfill_free_data.py labels      -> data/cache/cex_labels/                     (DL9)
All resumable; polite rate limits."""
from __future__ import annotations

import io
import json
import subprocess
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
C = ROOT / "data/cache"
START = "2024-03-01"
UA = {"User-Agent": "Mozilla/5.0 (research backfill)"}


def codes():
    return json.load(open(C / "b7/codes.json"))


def secs(s):
    return (pd.to_datetime(s, utc=True) - pd.Timestamp(0, tz="UTC")) // pd.Timedelta("1s")


# ------------------------------------------------------------------ DL3 Bithumb 1h (v1 API is Upbit-compatible)
def bithumb1h():
    out = C / "bithumb1h_hist"
    out.mkdir(exist_ok=True)
    s = requests.Session()
    mk = [m["market"] for m in s.get("https://api.bithumb.com/v1/market/all", timeout=20).json() if m["market"].startswith("KRW-")]
    start = int(pd.Timestamp(START, tz="UTC").timestamp())
    print("bithumb markets", len(mk), flush=True)
    for n, m in enumerate(mk):
        p = out / f"{m.split('-', 1)[1]}.parquet"
        if p.exists():
            continue
        rows, to = [], None
        while True:
            par = {"market": m, "count": 200}
            if to:
                par["to"] = to
            for k in range(5):
                r = s.get("https://api.bithumb.com/v1/candles/minutes/60", params=par, timeout=20)
                if r.status_code == 200:
                    break
                time.sleep(1 + k)
            time.sleep(0.03)
            js = r.json() if r.status_code == 200 else []
            if not js or not isinstance(js, list):
                break
            rows += js
            old = js[-1]
            t_old = int(secs(old["candle_date_time_utc"]))
            if t_old <= start or len(js) < 200:
                break
            to = old["candle_date_time_kst"].replace("T", " ")
        if rows:
            df = pd.DataFrame(rows)
            d = pd.DataFrame({"ts": secs(df["candle_date_time_utc"]) + 3600, "c": df["trade_price"],
                              "value_krw": df["candle_acc_trade_price"]}).drop_duplicates("ts").sort_values("ts")
            d[d["ts"] >= start].to_parquet(p, index=False)
        if n % 25 == 0:
            print("bithumb", n, len(mk), m, len(rows), flush=True)


# ------------------------------------------------------------------ DL4 Binance spot 1h (vision monthly + daily)
def _vision_klines(url):
    r = requests.get(url, timeout=60, headers=UA)
    if r.status_code != 200:
        return None
    z = zipfile.ZipFile(io.BytesIO(r.content))
    d = pd.read_csv(z.open(z.namelist()[0]), header=None)
    if not str(d.iloc[0, 0]).isdigit():
        d = d.iloc[1:].astype(float)
    ot = d[0].astype("int64")
    ot = np.where(ot > 1e14, ot // 1000, ot)                     # 2025+ spot files are in microseconds
    return pd.DataFrame({"ts": ot // 1000 + 3600, "c": d[4].astype(float), "qv": d[7].astype(float), "tbq": d[10].astype(float)})


def _spot_one(sym):
    p = C / "spot1h_hist" / f"{sym}.parquet"
    if p.exists():
        return sym, -1
    months = pd.period_range(START, pd.Timestamp.today() - pd.offsets.MonthBegin(1), freq="M")
    parts = []
    for m in months:
        f = _vision_klines(f"https://data.binance.vision/data/spot/monthly/klines/{sym}/1h/{sym}-1h-{m}.zip")
        if f is not None:
            parts.append(f)
    d0 = pd.Timestamp.today().normalize().replace(day=1)
    for k in range((pd.Timestamp.today().normalize() - d0).days):
        dd = (d0 + pd.Timedelta(days=k)).date()
        f = _vision_klines(f"https://data.binance.vision/data/spot/daily/klines/{sym}/1h/{sym}-1h-{dd}.zip")
        if f is not None:
            parts.append(f)
    if not parts:
        pd.DataFrame({"ts": []}).to_parquet(p, index=False)       # marker: no spot market
        return sym, 0
    d = pd.concat(parts).drop_duplicates("ts").sort_values("ts")
    d.to_parquet(p, index=False)
    return sym, len(d)


def spot1h():
    (C / "spot1h_hist").mkdir(exist_ok=True)
    syms = [c.replace("1000", "") if c.startswith("1000") and not c.startswith("1000000") else c for c in codes()]
    with ThreadPoolExecutor(8) as ex:
        for n, (s, k) in enumerate(ex.map(_spot_one, syms)):
            if n % 50 == 0:
                print("spot", n, len(syms), s, k, flush=True)


# ------------------------------------------------------------------ DL5 bookDepth history, hourly
def _depth_one(args):
    sym, days = args
    sys.path.insert(0, str(ROOT / "scripts"))
    from backfill_vision_bookdepth import day_frame
    p = C / "depth1h" / f"{sym}.parquet"
    if p.exists():
        return sym, -1
    s = requests.Session()
    parts = []
    for d in days:
        f = day_frame(s, sym, d)
        if f is None:
            continue
        f["h"] = (f["ts"] // 3600 + 1) * 3600
        parts.append(f.drop(columns="ts").groupby("h").mean().reset_index().rename(columns={"h": "ts"}))
    if not parts:
        pd.DataFrame({"ts": []}).to_parquet(p, index=False)
        return sym, 0
    d = pd.concat(parts).drop_duplicates("ts").sort_values("ts")
    d.astype({c: "float32" for c in d.columns if c != "ts"}).to_parquet(p, index=False)
    return sym, len(d)


def depth1h():
    (C / "depth1h").mkdir(exist_ok=True)
    ts = np.load(C / "b7/ts.npy")
    cc = np.load(C / "b7/c.npy", mmap_mode="r")
    d0, d1 = date.fromisoformat(START), date.fromisoformat("2026-03-24")
    jobs = []
    for j, sym in enumerate(codes()):
        live = np.flatnonzero(np.isfinite(cc[:, j]))
        if len(live) == 0:
            continue
        a0 = max(d0, pd.Timestamp(int(ts[live[0]]), unit="s").date())
        a1 = min(d1, pd.Timestamp(int(ts[live[-1]]), unit="s").date())
        if a0 <= a1:
            jobs.append((sym, [str(a0 + timedelta(k)) for k in range((a1 - a0).days + 1)]))
    with ThreadPoolExecutor(12) as ex:
        for n, (s, k) in enumerate(ex.map(_depth_one, jobs)):
            if n % 25 == 0:
                print("depth", n, len(jobs), s, k, flush=True)


# ------------------------------------------------------------------ DL6 exchange notices
def notices():
    out = C / "notices"
    out.mkdir(exist_ok=True)
    s = requests.Session()
    s.headers.update(UA)
    # Binance CMS catalogs: 48 new listings, 161 delistings
    for cat, kind in ((48, "listing"), (161, "delisting")):
        rows = []
        for page in range(1, 60):
            r = s.get("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query",
                      params={"type": 1, "catalogId": cat, "pageNo": page, "pageSize": 50}, timeout=20)
            try:
                arts = r.json()["data"]["catalogs"][0]["articles"]
            except Exception:
                break
            if not arts:
                break
            rows += [dict(ts=a["releaseDate"] // 1000, title=a["title"], kind=kind, id=a.get("code")) for a in arts]
            time.sleep(0.5)
        pd.DataFrame(rows).to_parquet(out / f"binance_{kind}.parquet", index=False)
        print("binance", kind, len(rows), flush=True)
    # Bybit announcements
    for typ in ("new_crypto", "delistings"):
        rows = []
        for page in range(1, 100):
            r = s.get("https://api.bybit.com/v5/announcements/index", params={"locale": "en-US", "type": typ, "page": page, "limit": 50}, timeout=20)
            try:
                lst = r.json()["result"]["list"]
            except Exception:
                break
            if not lst:
                break
            rows += [dict(ts=a["dateTimestamp"] // 1000, title=a["title"], kind=typ, id=a.get("url")) for a in lst]
            time.sleep(0.3)
        pd.DataFrame(rows).to_parquet(out / f"bybit_{typ}.parquet", index=False)
        print("bybit", typ, len(rows), flush=True)
    # OKX announcements
    for typ in ("announcements-new-listings", "announcements-delistings"):
        rows = []
        for page in range(1, 100):
            r = s.get("https://www.okx.com/api/v5/support/announcements", params={"annType": typ, "page": page}, timeout=20)
            try:
                det = r.json()["data"][0]["details"]
            except Exception:
                break
            if not det:
                break
            rows += [dict(ts=int(a["pTime"]) // 1000, title=a["title"], kind=typ, id=a.get("url")) for a in det]
            time.sleep(0.3)
        pd.DataFrame(rows).to_parquet(out / f"okx_{typ}.parquet", index=False)
        print("okx", typ, len(rows), flush=True)
    # Upbit (api-manager, trade category) and Bithumb (last notices only)
    rows = []
    for page in range(1, 200):
        r = s.get("https://api-manager.upbit.com/api/v1/announcements", params={"os": "web", "page": page, "per_page": 20, "category": "trade"}, timeout=20)
        try:
            lst = r.json()["data"]["notices"]
        except Exception:
            break
        if not lst:
            break
        rows += [dict(ts=int(secs(a["listed_at"])), title=a["title"], kind="trade", id=a.get("id")) for a in lst]
        time.sleep(0.3)
    pd.DataFrame(rows).to_parquet(out / "upbit_trade.parquet", index=False)
    print("upbit", len(rows), flush=True)
    try:
        r = s.get("https://api.bithumb.com/v1/notices", params={"page": 1, "limit": 20}, timeout=20)
        pd.DataFrame(r.json()).to_parquet(out / "bithumb_latest.parquet", index=False)
    except Exception as e:
        print("bithumb notices", e)


# ------------------------------------------------------------------ DL7 token unlocks (DefiLlama emissions, public datasets)
def unlocks():
    out = C / "unlocks"
    out.mkdir(exist_ok=True)
    s = requests.Session()
    s.headers.update(UA)
    lst = None
    for url in ("https://api.llama.fi/emissions", "https://defillama-datasets.llama.fi/emissionsProtocolsList"):
        r = s.get(url, timeout=30)
        if r.status_code == 200:
            lst = r.json()
            json.dump(lst, open(out / "protocols.json", "w"))
            print("emissions list from", url, len(lst), flush=True)
            break
    if lst is None:
        print("public emissions endpoints unavailable -> cloning DefiLlama/emissions-adapters", flush=True)
        subprocess.run(["git", "clone", "--depth", "1", "https://github.com/DefiLlama/emissions-adapters.git", str(out / "emissions-adapters")])
        return
    names = [x if isinstance(x, str) else (x.get("token") or x.get("name") or x.get("protocolId")) for x in lst]
    for n, name in enumerate(names):
        p = out / f"{str(name).replace('/', '_')}.json"
        if p.exists() or not name:
            continue
        for url in (f"https://api.llama.fi/emission/{name}", f"https://defillama-datasets.llama.fi/emissions/{name}"):
            r = s.get(url, timeout=30)
            if r.status_code == 200:
                p.write_text(r.text)
                break
        time.sleep(0.3)
        if n % 50 == 0:
            print("unlocks", n, len(names), name, flush=True)


# ------------------------------------------------------------------ DL8 CoinGecko 365d history
def coingecko():
    out = C / "cg_hist"
    out.mkdir(exist_ok=True)
    from dotenv import load_dotenv
    import os
    import psycopg
    load_dotenv(ROOT / ".env")
    con = psycopg.connect(os.environ["DATABASE_URL"])
    ids = [r[0] for r in con.execute("SELECT DISTINCT cg_id FROM cg_daily WHERE cg_id IS NOT NULL").fetchall()]
    s = requests.Session()
    s.headers.update(UA)
    print("coingecko ids", len(ids), flush=True)
    for n, cid in enumerate(ids):
        p = out / f"{cid}.parquet"
        if p.exists():
            continue
        for k in range(4):
            r = s.get(f"https://api.coingecko.com/api/v3/coins/{cid}/market_chart", params={"vs_currency": "usd", "days": 365, "interval": "daily"}, timeout=30)
            if r.status_code == 200:
                break
            time.sleep(30 * (k + 1))
        if r.status_code == 200:
            js = r.json()
            d = pd.DataFrame(js["prices"], columns=["t", "price"])
            d["mcap"] = [x[1] for x in js["market_caps"]][:len(d)]
            d["volume"] = [x[1] for x in js["total_volumes"]][:len(d)]
            d["ts"] = d["t"] // 1000
            d.drop(columns="t").to_parquet(p, index=False)
        time.sleep(4.5)                                           # ~13 calls/min, inside the free public limit
        if n % 25 == 0:
            print("cg", n, len(ids), cid, flush=True)


# ------------------------------------------------------------------ DL9 CEX wallet labels (Dune spellbook, sparse clone)
def labels():
    out = C / "cex_labels"
    if (out / "spellbook").exists():
        print("labels already cloned")
        return
    out.mkdir(exist_ok=True)
    subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", "https://github.com/duneanalytics/spellbook.git",
                    str(out / "spellbook")], check=False)
    subprocess.run(["git", "-C", str(out / "spellbook"), "sparse-checkout", "set", "--no-cone", "**/cex/**", "**/labels/**/cex*"], check=False)
    n = len(list((out / "spellbook").rglob("*cex*")))
    print("cex label files", n)


if __name__ == "__main__":
    {"bithumb1h": bithumb1h, "spot1h": spot1h, "depth1h": depth1h, "notices": notices, "unlocks": unlocks,
     "coingecko": coingecko, "labels": labels}[sys.argv[1]]()
