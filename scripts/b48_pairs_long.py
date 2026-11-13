"""B48 / U7 (2026-10-04, Upbit queue): pairs divergence on Upbit KRW hourly, LONG-ONLY single leg.

Re-run of F10 (divergence continuation) and the retired pairs_statarb (reversion) where only one leg can be held.
Pair selection and z are the production functions in src/engine/pairs.py (select window 90d, trade window 30d,
z window 20d, |z| >= 2 entry, beta 0.2..5, half-life 12h..20d, ranking spread_std/half_life), run on Upbit h1.
Universe at each rebalance: top 60 Upbit markets by median daily value over the select window (>= 8M USD/day,
KRW at 1370), excluding BTC/ETH/USDT/USDC; top 40 selected pairs traded. z at the close of hour i uses spread values
before i (rolling window shifted by one) and the beta fitted on the select window only; entry at OPEN of hour i+1.
Variants (all long one leg, one open trade per pair):
  rich   buy the leg that is up relative to the other (F10 continuation)
  cheap  buy the leg that is down relative to the other (statarb laggard)
  exits  zrule (rich: |z| >= |z_in|+1 target or |z| <= 0.5 stop; cheap: |z| <= 0.5 target or |z| >= |z_in|+1 stop;
         both capped at 48h, exit at next open), fixed 24h, fixed 72h
Protocol as B43: eras by entry time train ..2025-06 | val 2025-07..12 | TEST 2026; best-validation variant (n >= 30)
judged once on TEST; PASS = day-clustered 95% CI lower bound > 0. Costs 2 x (0.05% + slippage tier). Mirror reported.
Output research/b48_pairs_long.md, data/upbit_db/b48_trades.parquet. Paper research only.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
from b43_upbit_hourly_suite import DB, ROOT, START, boot, era, panel, slip  # noqa: E402
from src.engine import pairs  # noqa: E402

FEE, USDKRW = 0.0005, 1370.0
EXCL = {"KRW-BTC", "KRW-ETH", "KRW-USDT", "KRW-USDC"}
TOPN, NPAIRS, MAXH = 60, 40, 48


def zseries(sp):
    s = pd.Series(sp)
    w = pairs.ZWIN_HOURS
    m = s.rolling(w, min_periods=int(w * 0.6)).mean().shift(1)
    sd = s.rolling(w, min_periods=int(w * 0.6)).std(ddof=0).shift(1)
    return ((s - m) / sd).to_numpy()


def trade_pair(O, z, ts, v24, ja, jb, t0, t1, cols):
    """walk hours t0..t1-1 (indices). z[i] known at close of i. returns trades for all variants."""
    out = []
    for side in ("rich", "cheap"):
        for ex in ("zrule", "24h", "72h"):
            i = t0
            while i < t1:
                zi = z[i]
                if not np.isfinite(zi) or abs(zi) < pairs.Z_IN:
                    i += 1
                    continue
                rich_is_a = zi > 0
                buy = ja if (rich_is_a == (side == "rich")) else jb
                e = i + 1
                if e >= len(ts):
                    break
                if ex == "zrule":
                    x = None
                    for k in range(i + 1, min(i + 1 + MAXH, len(ts) - 1)):
                        zk = abs(z[k])
                        if not np.isfinite(zk):
                            continue
                        hit_far, hit_near = zk >= abs(zi) + pairs.Z_STOP_DELTA, zk <= pairs.Z_OUT
                        if hit_far or hit_near:
                            x = k + 1
                            break
                    x = x if x is not None else min(i + 1 + MAXH, len(ts) - 1)
                else:
                    x = e + (24 if ex == "24h" else 72)
                if x >= len(ts):
                    break
                pe, px = O[e, buy], O[x, buy]
                if np.isfinite(pe) and np.isfinite(px) and pe > 0:
                    g = px / pe - 1
                    c = 2 * (FEE + float(slip(np.nan_to_num(v24[i, buy]))))
                    out.append(("pairs_long", f"{side} leg, exit {ex}", cols[buy], int(ts[e]), g, c))
                i = x  # one open trade per pair and variant
    return out


def main():
    t0 = time.time()
    P = panel()
    C, O, V = P["c"], P["o"].to_numpy(), P["v"]
    ts = C.index.to_numpy()
    cols = list(C.columns)
    v24 = V.rolling(24, min_periods=24).sum().to_numpy()
    LP = np.log(C.where(C > 0)).to_numpy()
    # NaN before a coin's first real candle: panel() ffills only after listing, so leading NaN stays NaN
    dv = (V / USDKRW).to_numpy()
    sel_h, trade_h = pairs.SEL_HOURS, pairs.TRADE_HOURS
    start_i = int(np.searchsorted(ts, START)) + sel_h
    rows, nsel = [], []
    for r0 in range(start_i, len(ts) - 1, trade_h):
        sel = slice(r0 - sel_h, r0)
        med = {}
        for j, m in enumerate(cols):
            if m in EXCL:
                continue
            seg = LP[sel, j]
            if np.isfinite(seg).sum() < sel_h * pairs.COVERAGE:
                continue
            med[j] = pairs.median_daily_dvol(dv[sel, j])
        names = [j for j, d in sorted(med.items(), key=lambda kv: -kv[1]) if d >= pairs.MIN_DVOL][:TOPN]
        logp = {cols[j]: LP[:, j] for j in names}
        spec = pairs.select_pairs([cols[j] for j in names], logp, sel)[:NPAIRS]
        nsel.append((int(ts[r0]), len(names), len(spec)))
        t1 = min(r0 + trade_h, len(ts) - 1)
        for p in spec:
            ja, jb = cols.index(p.a), cols.index(p.b)
            sp = pairs.spread(LP[:, ja], LP[:, jb], p.beta)
            z = zseries(sp)
            rows += trade_pair(O, z, ts, v24, ja, jb, r0, t1, cols)
        print(pd.Timestamp(ts[r0], unit="s").date(), len(names), len(spec), len(rows), flush=True)
    T = pd.DataFrame(rows, columns=["family", "variant", "market", "t", "gross", "cost"])
    T["net"] = T.gross - T.cost
    T["mirror"] = -T.gross - T.cost
    T["era"] = era(T.t.to_numpy())
    T["day"] = T.t // 86400
    T.to_parquet(DB / "b48_trades.parquet", index=False)
    L = ["# B48 / U7: Upbit pairs divergence, long one leg only", "",
         f"Rebalances {len(nsel)}; universe/pairs per rebalance median {np.median([a for _, a, _ in nsel]):.0f}/"
         f"{np.median([b for _, _, b in nsel]):.0f}. Net after Upbit fees + slippage. Mirror = short side (not executable).", "",
         "| variant | train n / mean | val n / mean | test n / mean | test win | test mirror |", "|---|---|---|---|---|---|"]
    best = None
    for var, g in T.groupby("variant", sort=False):
        cell = {er: (f"{(g.era == er).sum()} / {g[g.era == er].net.mean():+.2%}" if (g.era == er).any() else "0 / -")
                for er in ("train", "val", "test")}
        te = g[g.era == "test"]
        L.append(f"| {var} | {cell['train']} | {cell['val']} | {cell['test']} | {(te.net > 0).mean():.0%} | {te.mirror.mean():+.2%} |")
        va = g[g.era == "val"]
        if len(va) >= 30 and (best is None or va.net.mean() > best[1]):
            best = (var, va.net.mean())
    g = T[(T.variant == best[0]) & (T.era == "test")]
    lo, hi = boot(g.net.to_numpy(), g.day.to_numpy())
    vd = "PASS" if len(g) >= 30 and lo > 0 else "FAIL"
    L += ["", "## Verdict", "", "| chosen | val mean | test n | test mean | 95% CI | verdict |", "|---|---|---|---|---|---|",
          f"| {best[0]} | {best[1]:+.2%} | {len(g)} | {g.net.mean():+.2%} | [{lo:+.2%}, {hi:+.2%}] | **{vd}** |", "",
          f"Runtime {time.time() - t0:.0f}s."]
    (ROOT / "research/b48_pairs_long.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
