"""B49 (2026-10-05): do the Upbit avoid rules (B43/B44/B47) improve F2 high-confidence LONGS on Binance?

Trades: B39 frozen-CNN replay (data/cache/b39_f2_trades.parquet), side > 0 and margin >= 0.0230188 (F2L rule), baseline 4h net.
Flags, all from Upbit hourly candles that CLOSED at or before the signal ts (candle start <= ts - 3600):
  upbit_listed   base coin has an Upbit KRW market with a candle in the previous 24h
  upbit_pump24   any Upbit +10%/1h candle in the 24h before ts
  new72          Upbit KRW market younger than 72h at ts
  prem10         Upbit premium vs Binance (Upbit close / (Binance spot close * KRW-USDT) - 1) >= +10% at the last closed hour
Rule fixed before looking: a filter is adopted for the main book only if, in BOTH eras (2024 and 2026), the kept trades
beat the removed trades and the kept mean is >= the unfiltered mean; reported with day-clustered CI on the kept 2026 set.
Output research/b49_f2_upbit_avoid.md. Paper research only.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/upbit_db"
MARGIN = 0.0230188


def base(code):
    s = code.replace("USDT", "")
    for p in ("1000000", "10000", "1000"):
        if s.startswith(p):
            s = s[len(p):]
    return s


def boot(x, day, n=2000, seed=9):
    if len(x) < 5:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed); u = np.unique(day)
    g = {k: x[day == k] for k in u}
    return np.percentile([np.concatenate([g[k] for k in rng.choice(u, len(u))]).mean() for _ in range(n)], [2.5, 97.5])


def main():
    t = pd.read_parquet(ROOT / "data/cache/b39_f2_trades.parquet")
    t = t[(t.side > 0) & (t.margin >= MARGIN)].copy()
    t["net"] = t["baseline 4h"]
    usdt = pd.read_parquet(DB / "h1/KRW-USDT.parquet").set_index("ts")["c"]
    H, B = {}, {}
    flags = []
    for r in t.itertuples():
        b = base(r.code); m = f"KRW-{b}"
        if m not in H:
            f = DB / "h1" / f"{m}.parquet"
            H[m] = pd.read_parquet(f).set_index("ts").sort_index() if f.exists() else None
            fb = DB / "bn_h1" / f"{b}.parquet"
            B[m] = pd.read_parquet(fb).drop_duplicates("ts").set_index("ts")["c"] if fb.exists() else None
        g = H[m]
        last = r.ts - 3600                                   # start of the last fully closed hour
        if g is None or g.index[0] > last:
            flags.append((False, False, False, False)); continue
        w = g.loc[last - 23 * 3600: last]
        listed = len(w) > 0
        ret = (g["c"] / g["c"].shift(1) - 1).loc[last - 23 * 3600: last]
        pump = bool((ret >= 0.10).any())
        new = (r.ts - g.index[0]) < 72 * 3600
        prem = False
        if B[m] is not None and last in B[m].index and last in usdt.index and last in g.index:
            prem = g.at[last, "c"] / (B[m].at[last] * usdt.at[last]) - 1 >= 0.10
        flags.append((listed, pump, new, prem))
    t[["upbit_listed", "upbit_pump24", "new72", "prem10"]] = pd.DataFrame(flags, index=t.index)
    t["any_avoid"] = t.upbit_pump24 | t.new72 | t.prem10
    L = ["# B49: Upbit avoid rules as filters on F2 high-confidence longs (Binance)", "",
         "B39 replay, side > 0, margin >= 0.0230188, baseline 4h net. Flags use Upbit hours closed before the signal.", "",
         "| era | flag | flagged n / mean | kept n / mean | all n / mean |", "|---|---|---|---|---|"]
    adopt = {}
    for fl in ("upbit_listed", "upbit_pump24", "new72", "prem10", "any_avoid"):
        ok = []
        for er, g in t.groupby("era"):
            a, k = g[g[fl]], g[~g[fl]]
            L.append(f"| {er} | {fl} | {len(a)} / {a.net.mean():+.2%} | {len(k)} / {k.net.mean():+.2%} | {len(g)} / {g.net.mean():+.2%} |")
            ok.append(len(a) >= 5 and k.net.mean() > a.net.mean() and k.net.mean() >= g.net.mean())
        adopt[fl] = all(ok) and len(ok) >= 2
    L += ["", "| filter (drop flagged) | adopt? | kept 2026 n / mean | 95% CI |", "|---|---|---|---|"]
    for fl, a in adopt.items():
        k = t[(t.era == "2026") & ~t[fl]]
        lo, hi = boot(k.net.to_numpy(), k.day.to_numpy())
        L.append(f"| {fl} | {'YES' if a else 'no'} | {len(k)} / {k.net.mean():+.2%} | [{lo:+.2%}, {hi:+.2%}] |")
    (ROOT / "research/b49_f2_upbit_avoid.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
