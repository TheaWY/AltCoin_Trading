"""B17 F19 news/social: only F19_004 is runnable (Binance announcement word class: 'will list' vs 'delist'). The other 11 need CryptoPanic /
Santiment / Telegram / X / Reddit / Korean-community data (keys from 유리) -> not_run.
  .venv/bin/python -W ignore scripts/b17_f19.py   -> data/reports/b17/f19.json
Data: data/cache/notices/binance_listing.parquet, binance_delisting.parquet (ts = publication time to the second; checked 2026-09-29:
removal notices are published days before the removal dates in their titles). Symbols are parsed from the title: tickers in parentheses,
'XYZUSDT' / 'XYZ/USDT' pairs, and comma lists after 'Delist'. Only coins with a live Binance USDT perp at the time are traded.
Rule (fixed, no fitting): entry at the perp 1-minute close 60 s after publication (latency), long after 'will list' / 'add ... on' notices,
short after delisting notices; exits +1h and +24h; costs 2 x (fee + slippage). Cells: listing|1h, listing|24h, delisting|1h, delisting|24h.
Pass: validation (2026) net CI > 0, discovery mean > 0, BH q=0.10 over the 4 cells."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

os.environ["B2_ERA"] = "all"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b2_panel as bp  # noqa: E402
import b7_lib as L  # noqa: E402
import b15_models as M  # noqa: E402
from b17_f03 import bh  # noqa: E402

C, OUT = M.C, ROOT / "data/reports/b17"
RNG = np.random.default_rng(1719)
VAL0 = 1767225600
STOP = {"USDT", "USDC", "FDUSD", "TRY", "EUR", "BRL", "UAH", "BTC", "ETH", "BNB", "SPOT", "PERP", "NEW", "WILL", "AND", "THE", "USD"}


def tickers(title, universe):
    cand = set(re.findall(r"\(([A-Z0-9]{2,12})\)", title)) | set(re.findall(r"\b([A-Z0-9]{2,12})(?:/?USDT)\b", title))
    if "elist" in title:
        tail = title.split("elist", 1)[1]
        cand |= set(re.findall(r"\b([A-Z0-9]{2,12})\b", tail.split(" on ")[0]))
    out = set()
    for c in cand - STOP:
        for code in (f"{c}USDT", f"1000{c}USDT"):
            if code in universe:
                out.add(code); break
    return out


def main():
    ts_h, codes, X = L.data(); universe = set(codes)
    N = pd.concat([pd.read_parquet(C / "notices/binance_listing.parquet").assign(cls="listing"),
                   pd.read_parquet(C / "notices/binance_delisting.parquet").assign(cls="delisting")])
    N = N[(N["ts"] >= 1709251200 + 3 * 86400) & (N["ts"] <= 1790294400 - 86400)]
    ev = [(int(r.ts), r.cls, code, r.title) for r in N.itertuples(index=False) for code in tickers(r.title, universe)]
    E = pd.DataFrame(ev, columns=["ts", "cls", "code", "title"]).drop_duplicates(["code", "cls", "ts"])
    E = E.sort_values("ts"); E = E[E.groupby(["code", "cls"])["ts"].diff().fillna(1e9) > 86400]      # 1 event per coin/class per day
    rows = []
    for code, g in E.groupby("code"):
        d = bp.load_minutes(code)
        if d is None:
            continue
        t = d.index.to_numpy().astype("int64"); t = t // 10 ** 9 if t.max() > 1e12 else t
        c = d["c"].to_numpy(float); qv = d["qv"].to_numpy(float)
        for r in g.itertuples(index=False):
            k = np.searchsorted(t + 60, r.ts + 60)                       # first minute whose close (t+60) >= publication + 60 s
            if k >= len(t) or t[k] + 60 - (r.ts + 60) > 300 or k + 1440 >= len(t):
                continue
            dv = qv[max(0, k - 1440):k].sum(); cost = 2 * (L.FEE + float(M.slip(np.array([dv]))[0])); sgn = 1 if r.cls == "listing" else -1
            for hz, m in (("1h", 60), ("24h", 1440)):
                rows.append((r.cls, hz, code, r.ts, sgn * (c[k + m] / c[k] - 1) - cost))
    T = pd.DataFrame(rows, columns=["cls", "hz", "code", "ts", "net"]); T["day"] = T["ts"] // 86400; T["val"] = T["ts"] >= VAL0
    res = {"events": {k: int(v) for k, v in E["cls"].value_counts().items()}, "tests": {},
           "not_run": {f"F19_{i:03d}": "needs CryptoPanic / Santiment / Telegram / X / Reddit / Korean-community data" for i in (1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12)}}
    pv = {}
    for (cls, hz), g in T.groupby(["cls", "hz"]):
        def st(x, day):
            if len(x) < 20:
                return dict(n=int(len(x)), mean=float(x.mean()) if len(x) else None, ci=[np.nan, np.nan], p=1.0)
            b = pd.DataFrame({"x": x, "d": day}).groupby("d")["x"].agg(["sum", "count"]); su, cn = b["sum"].to_numpy(), b["count"].to_numpy()
            bo = np.array([su[i].sum() / cn[i].sum() for i in (RNG.integers(0, len(su), len(su)) for _ in range(2000))])
            return dict(n=int(len(x)), mean=float(x.mean()), ci=[float(v) for v in np.percentile(bo, [2.5, 97.5])], p=float((bo <= 0).mean()))
        v = st(g.loc[g["val"], "net"].to_numpy(), g.loc[g["val"], "day"].to_numpy()); dsc = st(g.loc[~g["val"], "net"].to_numpy(), g.loc[~g["val"], "day"].to_numpy())
        k = f"F19_004|{cls}|{hz}"; res["tests"][k] = dict(val=v, disc=dsc); pv[k] = v["p"]
        print(k, "val", v, "disc", round(dsc["mean"] or 0, 4), dsc["n"], flush=True)
    res["bh_pass"] = bh(pv)
    res["pass"] = [k for k in res["bh_pass"] if res["tests"][k]["val"]["ci"][0] > 0 and (res["tests"][k]["disc"]["mean"] or 0) > 0]
    OUT.mkdir(parents=True, exist_ok=True); json.dump(res, open(OUT / "f19.json", "w"), indent=1, default=float)
    print("events", res["events"], "F19 BH", res["bh_pass"], "PASS", res["pass"], flush=True)


if __name__ == "__main__":
    main()
