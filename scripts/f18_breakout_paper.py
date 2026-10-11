"""Forward paper test F18 (registered 2026-10-05 after B54): the two B54 holdout PASS rules, Upbit alt breakout portfolios.
  F18a = D_brk20_v1.5_trail : 20d closing high + value >= 1.5x 30d median, BTC trend >= 0.5, 10 slots x 10%, 20% trailing exit
  F18b = D_brk20_v1.5_own   : same entry + coin's own trend weight >= 0.75, Donchian-10 exit
Also F19 (registered 2026-10-05 18:30, chosen by 유리 for lower drawdown): B55 book S3_N1_L0_s20 =
80% F17 + 20% "up & volatile" alt satellite with the alt-index crash brake. It FAILED the Sharpe test (B55); it is kept
because it cut backtest max drawdown (2024-26: -21% vs -26%) at the cost of return (+31% vs +36%/yr).
Judged against the EW top-30 alt basket (the registered benchmark) AND, for 유리's purposes, against F17.
Daily (launchd 09:20 KST): refresh Upbit daily candles, recompute with B51_END = today (completed candles only), write
daily returns for days >= 2026-10-06 and today's holdings to data/forward/f18_breakout.parquet. Paper only, no orders.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
START = pd.Timestamp("2026-10-06")
X8F = ROOT / "data/forward/x8_daily.parquet"


def x8_snapshot(S, day):
    """F19x (forward.yaml): live X8 = log(Binance perp 24h quote vol in KRW / Upbit 24h KRW value), demeaned,
    for top-40 Upbit coins (30d median value, >= 90d) with a Binance USDT perp. Stored per decision day."""
    import json
    import urllib.request
    import numpy as np
    get = lambda u: json.load(urllib.request.urlopen(u, timeout=20))  # noqa: E731
    bn = {}
    for t in get("https://fapi.binance.com/fapi/v1/ticker/24hr"):
        s = t["symbol"]
        if not s.endswith("USDT"):
            continue
        b = s[:-4]
        for p in ("1000000", "1000"):
            if b.startswith(p) and len(b) > len(p) and not b[len(p)].isdigit():
                b = b[len(p):]; break
        bn["KRW-" + b] = bn.get("KRW-" + b, 0.0) + float(t["quoteVolume"])
    age = S.C.notna().cumsum().loc[day]; medv = S._medv.loc[day]
    alts = [c for c in S._alts if c in bn and age.get(c, 0) >= 90 and np.isfinite(medv.get(c, np.nan))]
    uni = list(medv[alts].sort_values(ascending=False).index[:40])
    up = {}
    for i in range(0, len(uni) + 1, 50):
        mk = ",".join(uni[i:i + 50] + (["KRW-USDT"] if i == 0 else []))
        for t in get(f"https://api.upbit.com/v1/ticker?markets={mk}"):
            up[t["market"]] = (float(t["acc_trade_price_24h"]), float(t["trade_price"]))
    fx = up["KRW-USDT"][1]
    x = pd.Series({c: np.log(max(bn[c] * fx, 1) / max(up[c][0], 1)) for c in uni if c in up})
    x = x - x.median()
    q = x.rank(pct=True)
    return pd.DataFrame({"day": day, "coin": x.index, "x8": x.values, "bottom": (q <= 0.2).values,
                         "taken": pd.Timestamp.now("UTC").tz_localize(None)})


def main():
    subprocess.run([sys.executable, str(ROOT / "scripts/upbit_daily.py"), "--refresh"], cwd=ROOT, check=True, timeout=3000,
                   capture_output=True)
    os.environ["B51_END"] = pd.Timestamp.now("UTC").strftime("%Y-%m-%d")    # completed candles only
    sys.path.insert(0, str(ROOT / "scripts"))
    import b54_sweep100 as S  # noqa: E402  (loads the panel at import)
    st = {r[0]: r for r in S.STRATEGIES}
    out, hold = [], []
    for fid, sid in (("F18a", "D_brk20_v1.5_trail"), ("F18b", "D_brk20_v1.5_own")):
        W = st[sid][3](); r = S.run(W)
        for d, v in r[(r.index >= START) & (r.index < r.index[-1])].items():
            out.append((fid, d, float(v)))       # r[t] = held open t -> open t+1 (decided at close t-1); last day incomplete
        last = W.iloc[-1]; h = last[last > 0]
        hold.append((fid, W.index[-1], ", ".join(f"{k[4:]} {v:.0%}" for k, v in h.items()) or "cash"))
    import b55_combos as K  # noqa: E402
    W19 = K.book(K.sat_upvol(), 0.2, K.down_n1(), 1.0, False)
    r = S.run(W19)
    for d, v in r[(r.index >= START) & (r.index < r.index[-1])].items():
        out.append(("F19", d, float(v)))
    last = W19.iloc[-1]; h = last[last > 0]
    hold.append(("F19", W19.index[-1], ", ".join(f"{k[4:]} {v:.1%}" for k, v in h.items()) or "cash"))
    # ---- F19x: same book, satellite alts in the bottom X8 quintile of that decision day -> 0 (snapshots kept)
    dday = W19.index[-1]
    try:
        snap = x8_snapshot(S, dday)
        old = pd.read_parquet(X8F) if X8F.exists() else snap.iloc[0:0]
        X8 = pd.concat([old[old.day != dday], snap], ignore_index=True)
        X8.to_parquet(X8F, index=False)
    except Exception as e:  # noqa: BLE001
        print("x8 snapshot failed:", repr(e), flush=True)
        X8 = pd.read_parquet(X8F) if X8F.exists() else None
    W19x = W19.copy()
    if X8 is not None:
        for d, g in X8[X8.bottom].groupby("day"):
            if d in W19x.index:
                cols = [c for c in g.coin if c in W19x.columns and c not in S.MAJ]
                W19x.loc[d, cols] = 0.0
    r = S.run(W19x)
    for d, v in r[(r.index >= START) & (r.index < r.index[-1])].items():
        out.append(("F19x", d, float(v)))
    last = W19x.iloc[-1]; h = last[last > 0]
    hold.append(("F19x", dday, ", ".join(f"{k[4:]} {v:.1%}" for k, v in h.items()) or "cash"))
    for bid, W in (("EW30", S.W_ew30()), ("F17", S.W_f17())):
        r = S.run(W)
        for d, v in r[(r.index >= START) & (r.index < r.index[-1])].items():
            out.append((bid, d, float(v)))
    D = pd.DataFrame(out, columns=["book", "day", "ret"])
    D = D[D.day >= START]
    (ROOT / "data/forward").mkdir(parents=True, exist_ok=True)
    D.to_parquet(ROOT / "data/forward/f18_breakout.parquet", index=False)
    pd.DataFrame(hold, columns=["book", "decided", "holdings"]).to_parquet(ROOT / "data/forward/f18_holdings.parquet", index=False)
    print(time.strftime("%F %T"), "rows", len(D), "|", " | ".join(f"{b}: {h}" for b, _, h in hold), flush=True)


if __name__ == "__main__":
    main()
