"""B17 event-window data (free, Binance Vision + Bybit public). Resumable; each day file deleted after use; disk < 5 GB.
  .venv/bin/python scripts/backfill_b17.py sample      -> data/cache/b17/sample.parquet (pump ids: B15_3 + B15_3b tick samples + 500 more per period, seed 171)
  .venv/bin/python scripts/backfill_b17.py sec1        -> data/cache/b17/sec1/{pump_id}.parquet   Binance perp 1-second bars [m0-65m, m0+60m]
                                                          cols: ts, o,h,l,c, qv, n, tbqv, maxq (largest trade quote), buy_n
  .venv/bin/python scripts/backfill_b17.py bybit       -> data/cache/b17/bybit1s/{pump_id}.parquet  same window, Bybit linear perp trades
  .venv/bin/python scripts/backfill_b17.py spot1s      -> data/cache/b17/spot1s/{pump_id}.parquet   Binance spot 1s klines, same window
  .venv/bin/python scripts/backfill_b17.py metrics5m   -> data/cache/b17/metrics5m/{code}.parquet   5-min OI / L-S / taker for every pump day +-1 (all 21k pumps)
bookTicker on Vision stops 2024-03-30 and the futures websocket is geo-blocked here, so best bid/ask history is NOT obtainable free; F11/F17 use bookDepth 1-min instead."""
from __future__ import annotations

import re
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
B = ROOT / "data/cache/b17"
TMP = ROOT / "data/tmp_b17"
V = "https://data.binance.vision/data/"
PRE, POST = 65 * 60, 60 * 60


def get(url, fp, retries=4):
    for a in range(retries):
        try:
            r = requests.get(url, timeout=180, stream=True)
            if r.status_code == 404:
                return False
            r.raise_for_status()
            with open(fp, "wb") as f:
                for ch in r.iter_content(1 << 20):
                    f.write(ch)
            return True
        except Exception:
            time.sleep(5 * (a + 1))
    return False


def sample():
    import b15_tick as T
    P = T.M.pump_context()
    ids = set(T.sample(False)["pump_id"]) | set(T.sample(True)["pump_id"])
    R = P[~P["pump_id"].isin(ids)]
    extra = pd.concat([R[R["hold"] == h].sample(min(500, (R["hold"] == h).sum()), random_state=171) for h in (False, True)])
    S = P[P["pump_id"].isin(ids | set(extra["pump_id"]))][["pump_id", "code", "ts", "hold", "size_bucket", "dv24"]]
    B.mkdir(parents=True, exist_ok=True)
    S.to_parquet(B / "sample.parquet"); print("sample", len(S), S["hold"].mean())


def days(t0, t1):
    return sorted({time.strftime("%Y-%m-%d", time.gmtime(t)) for t in (t0, t1)})


def bars_1s(tr, t0, t1):
    """tr: columns t(ms), p, q, buy(bool). -> 1-second OHLC + flow bars covering [t0,t1)."""
    tr = tr[(tr["t"] >= t0 * 1000) & (tr["t"] < t1 * 1000)]
    if tr.empty:
        return None
    tr = tr.assign(s=tr["t"] // 1000, v=tr["p"] * tr["q"])
    g = tr.groupby("s")
    out = pd.DataFrame({"o": g["p"].first(), "h": g["p"].max(), "l": g["p"].min(), "c": g["p"].last(), "qv": g["v"].sum(), "n": g["p"].size(),
                        "tbqv": tr[tr["buy"]].groupby("s")["v"].sum(), "maxq": g["v"].max(), "buy_n": tr[tr["buy"]].groupby("s")["p"].size()})
    out = out.reindex(range(t0, t1)).rename_axis("ts").reset_index()
    out[["qv", "n", "tbqv", "maxq", "buy_n"]] = out[["qv", "n", "tbqv", "maxq", "buy_n"]].fillna(0)
    out["c"] = out["c"].ffill(); out["o"] = out["o"].fillna(out["c"]); out["h"] = out["h"].fillna(out["c"]); out["l"] = out["l"].fillna(out["c"])
    return out


def _binance_day(code, day):
    fp = TMP / f"{code}-agg-{day}.zip"
    if not get(V + f"futures/um/daily/aggTrades/{code}/{code}-aggTrades-{day}.zip", fp):
        return None
    try:
        with zipfile.ZipFile(fp) as z, z.open(z.namelist()[0]) as fh:
            hdr = fh.readline().decode().startswith("agg_trade_id")
        with zipfile.ZipFile(fp) as z, z.open(z.namelist()[0]) as fh:
            d = pd.read_csv(fh, header=None, skiprows=1 if hdr else 0, usecols=[1, 2, 5, 6], names=["p", "q", "t", "m"])
    finally:
        fp.unlink(missing_ok=True)
    d["buy"] = ~d["m"].astype(str).str.lower().eq("true")
    return d[["t", "p", "q", "buy"]]


def _bybit_day(code, day):
    fp = TMP / f"{code}-bybit-{day}.csv.gz"
    if not get(f"https://public.bybit.com/trading/{code}/{code}{day}.csv.gz", fp):
        return None
    try:
        d = pd.read_csv(fp, usecols=["timestamp", "side", "size", "price"])
    finally:
        fp.unlink(missing_ok=True)
    return pd.DataFrame({"t": (d["timestamp"] * 1000).astype("int64"), "p": d["price"], "q": d["size"], "buy": d["side"].eq("Buy")})


def _spot_day(code, day):
    fp = TMP / f"{code}-spot1s-{day}.zip"
    if not get(V + f"spot/daily/klines/{code}/1s/{code}-1s-{day}.zip", fp):
        return None
    try:
        with zipfile.ZipFile(fp) as z, z.open(z.namelist()[0]) as fh:
            d = pd.read_csv(fh, header=None, usecols=[0, 1, 2, 3, 4, 7, 8, 10], names=["ts", "o", "h", "l", "c", "qv", "n", "tbqv"])
    finally:
        fp.unlink(missing_ok=True)
    d["ts"] = d["ts"] // (1000 if d["ts"].iloc[0] > 1e12 else 1)
    d.loc[d["ts"] > 1e12, "ts"] //= 1000
    return d


def run_events(kind):
    TMP.mkdir(parents=True, exist_ok=True)
    S = pd.read_parquet(B / ("sample_null.parquet" if kind == "sec1_null" else "sample.parquet"))
    out = B / {"sec1": "sec1", "bybit": "bybit1s", "spot1s": "spot1s", "sec1_null": "sec1_null"}[kind]; out.mkdir(exist_ok=True)
    if kind == "sec1_null":
        S = S.assign(pump_id=-S["pump_id"]); kind = "sec1"
    todo = S[~S["pump_id"].apply(lambda i: (out / f"{i}.parquet").exists())]
    print(kind, "todo", len(todo), flush=True)

    def one(cg):
        code, g = cg
        c2 = re.sub(r"^(1000000|1000|1M)", "", code) if kind == "spot1s" else code
        loader = {"sec1": _binance_day, "bybit": _bybit_day, "spot1s": _spot_day}[kind]
        cache = {}
        for pid, t in zip(g["pump_id"], g["ts"]):
            t0, t1 = int(t) - PRE, int(t) + POST
            parts = []
            for d in days(t0, t1):
                if d not in cache:
                    cache[d] = loader(c2, d)
                if cache[d] is not None:
                    parts.append(cache[d])
            if not parts:
                continue
            d = pd.concat(parts)
            if kind == "spot1s":
                w = d[(d["ts"] >= t0) & (d["ts"] < t1)]
            else:
                w = bars_1s(d, t0, t1)
            if w is not None and len(w):
                w.to_parquet(out / f"{pid}.parquet", index=False)
        return len(g)

    with ThreadPoolExecutor(4) as ex:
        for i, n in enumerate(ex.map(one, list(todo.groupby("code")))):
            if i % 20 == 0:
                print(i, flush=True)
    print("done")


def metrics5m():
    import b15_models as M
    TMP.mkdir(parents=True, exist_ok=True)
    out = B / "metrics5m"; out.mkdir(parents=True, exist_ok=True)
    P = pd.read_parquet(M.C / "b15/pumps.parquet", columns=["code", "ts"])
    for code, g in P.groupby("code"):
        fp_out = out / f"{code}.parquet"
        have = pd.read_parquet(fp_out) if fp_out.exists() else pd.DataFrame()
        need = sorted({d for t in g["ts"] for d in days(int(t) - 86400, int(t) + 86400)})
        if len(have):
            done = set(pd.to_datetime(have["create_time"], unit="s").dt.strftime("%Y-%m-%d"))
            need = [d for d in need if d not in done]
        parts = [have] if len(have) else []
        for d in need:
            fp = TMP / f"{code}-metrics-{d}.zip"
            if get(V + f"futures/um/daily/metrics/{code}/{code}-metrics-{d}.zip", fp):
                try:
                    with zipfile.ZipFile(fp) as z, z.open(z.namelist()[0]) as fh:
                        x = pd.read_csv(fh)
                    ct = x["create_time"]
                    if pd.api.types.is_numeric_dtype(ct):
                        x["create_time"] = (ct // 1000 if ct.iloc[0] > 1e12 else ct).astype("int64")
                    else:
                        x["create_time"] = pd.to_datetime(ct).astype("int64") // 10**9
                    parts.append(x)
                except Exception:
                    pass
                finally:
                    fp.unlink(missing_ok=True)
        if parts:
            pd.concat(parts).drop_duplicates("create_time").sort_values("create_time").to_parquet(fp_out, index=False)
        print(code, len(need), flush=True)


if __name__ == "__main__":
    {"sample": sample, "sec1": lambda: run_events("sec1"), "sec1_null": lambda: run_events("sec1_null"), "bybit": lambda: run_events("bybit"), "spot1s": lambda: run_events("spot1s"), "metrics5m": metrics5m}[sys.argv[1]]()
