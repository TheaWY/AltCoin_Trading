"""Forward paper test F18 (registered 2026-10-05 after B54): the two B54 holdout PASS rules, Upbit alt breakout portfolios.
  F18a = D_brk20_v1.5_trail : 20d closing high + value >= 1.5x 30d median, BTC trend >= 0.5, 10 slots x 10%, 20% trailing exit
  F18b = D_brk20_v1.5_own   : same entry + coin's own trend weight >= 0.75, Donchian-10 exit
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
