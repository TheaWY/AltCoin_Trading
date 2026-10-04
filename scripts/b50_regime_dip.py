"""B50 / U9 (2026-10-05): Upbit multi-day selloff dip-buy, LONG ONLY, with and without a BTC < MA20 regime gate.

Idea from B45 (F2 longs did better when BTC was below its 20-day average) applied to plain Upbit dip-buying.
Signal at the close of hour i: coin's 72h return <= -x (x in 20%, 30%), alive (24h value >= 2.7e9 KRW, >= 72h history).
Regime: KRW-BTC previous DAILY close (last 00:00 UTC close at or before i) below its 20-day mean of daily closes.
Variants: regime in {any, btc<ma20, btc>ma20} x x x hold {24h, 72h}; entry at OPEN of hour i+1 (B43 events helper,
one open trade per coin). Same eras, costs, selection (best val, n >= 30) and PASS rule (TEST CI lower bound > 0) as B43.
Output research/b50_regime_dip.md, data/upbit_db/b50_trades.parquet. Paper research only.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from b43_upbit_hourly_suite import DB, ROOT, alive, boot, era, events, panel  # noqa: E402


def btc_regime(P):
    c = P["c"]["KRW-BTC"]
    daily = c[c.index % 86400 == 82800]            # close of the 23:00 UTC candle = daily close at 00:00
    ma = daily.rolling(20).mean()
    below = (daily < ma).astype(float).where(ma.notna())
    # value known at the close of the 23:00 candle (index t) -> applies from hour t onward
    return below.reindex(c.index).ffill()


def main():
    t0 = time.time()
    P = panel()
    reg = btc_regime(P)
    C = P["c"]
    r72 = C / C.shift(72) - 1
    ok = alive(P, 2.7e9)
    out = []
    for x in (0.20, 0.30):
        base = (r72 <= -x) & ok
        for rn, mask in (("any", None), ("btc<ma20", reg == 1), ("btc>ma20", reg == 0)):
            sig = base if mask is None else base & pd.DataFrame({m: mask for m in C.columns}, index=C.index)
            for h in (24, 72):
                out.append(events(P, sig, 0, h, fam="dip", var=f"72h <= -{x:.0%}, regime {rn}, hold {h}h"))
    T = pd.concat(out, ignore_index=True)
    T["net"] = T.gross - T.cost; T["mirror"] = -T.gross - T.cost
    T["era"] = era(T.t.to_numpy()); T["day"] = T.t // 86400
    T.to_parquet(DB / "b50_trades.parquet", index=False)
    L = ["# B50 / U9: Upbit multi-day selloff dip-buy with BTC regime gate (long only)", "",
         "| variant | train n / mean | val n / mean | test n / mean | test mirror |", "|---|---|---|---|---|"]
    best = None
    for var, g in T.groupby("variant", sort=False):
        cell = {e: (f"{(g.era == e).sum()} / {g[g.era == e].net.mean():+.2%}" if (g.era == e).any() else "0 / -") for e in ("train", "val", "test")}
        te = g[g.era == "test"]
        L.append(f"| {var} | {cell['train']} | {cell['val']} | {cell['test']} | {te.mirror.mean():+.2%} |" if len(te) else f"| {var} | {cell['train']} | {cell['val']} | {cell['test']} | - |")
        va = g[g.era == "val"]
        if len(va) >= 30 and (best is None or va.net.mean() > best[1]):
            best = (var, va.net.mean())
    g = T[(T.variant == best[0]) & (T.era == "test")]
    lo, hi = boot(g.net.to_numpy(), g.day.to_numpy()) if len(g) else (np.nan, np.nan)
    vd = "PASS" if len(g) >= 30 and lo > 0 else "FAIL"
    L += ["", "| chosen | val mean | test n | test mean | 95% CI | verdict |", "|---|---|---|---|---|---|",
          f"| {best[0]} | {best[1]:+.2%} | {len(g)} | {g.net.mean():+.2%} | [{lo:+.2%}, {hi:+.2%}] | **{vd}** |", "", f"Runtime {time.time() - t0:.0f}s."]
    (ROOT / "research/b50_regime_dip.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
