"""B45 (2026-10-04, Upbit queue U8, 유리: "all long, make longs profitable"): does a BTC trend filter make LONG trades pay?

Filter: trade only when Binance BTCUSDT daily close (previous complete UTC day) is above its N-day moving average,
N in {20, 50, 100} (and the "below" side shown for reference). Applied, unchanged, to:
  (a) F2 long signals from B39 (frozen CNN replays on Binance; all longs and high-confidence longs, 4h and trail exits);
      N chosen on the 2024 era, judged on 2026.
  (b) every B43 Upbit long-only variant (events and daily baskets); N chosen per family on val (2025H2), judged on TEST 2026.
Data: data/upbit_db/bn_h1/BTC.parquet (Binance spot hourly). No new parameters beyond N. Paper research only.
Output research/b45_long_regime.md.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/upbit_db"


def btc_up():
    b = pd.read_parquet(DB / "bn_h1" / "BTC.parquet")
    d = b.assign(day=b.ts // 86400).groupby("day").c.last()
    out = {}
    for n in (20, 50, 100):
        up = (d > d.rolling(n).mean()).shift(1)            # decided on the previous complete day
        out[n] = up
    return out


def boot(x, day, n=2000):
    rng = np.random.default_rng(8); u = np.unique(day)
    if len(u) < 5:
        return (np.nan, np.nan)
    g = {k: x[day == k] for k in u}
    m = [np.concatenate([g[k] for k in rng.choice(u, len(u))]).mean() for _ in range(n)]
    return tuple(np.percentile(m, [2.5, 97.5]))


def main():
    UP = btc_up()
    L = ["# B45 BTC trend filter for long-only trades (2026-10-04)", "",
         "Trade only when BTC's previous daily close is above its N-day average. N chosen on the earlier era, judged on 2026.", ""]
    # (a) F2 longs from B39
    e = pd.read_parquet(ROOT / "data/cache/b39_f2_trades.parquet")
    e = e[(e.side > 0) & (e.era != "insample")].copy()
    e["day"] = e.ts // 86400
    L += ["## (a) F2 long signals (Binance, frozen CNN)", "", "| set | exit | filter | 2024 n / mean | 2026 n / mean | 2026 95% CI |", "|---|---|---|---|---|---|"]
    for nm, s in (("all longs", e), ("high-confidence longs", e[e.margin >= 0.0230188])):
        for ex in ("baseline 4h", "trail arm 15% give-back 10%"):
            rows = [("none", s)]
            for n, up in UP.items():
                f = s.day.map(up).fillna(False).astype(bool)
                rows += [(f"BTC > MA{n}", s[f]), (f"BTC < MA{n}", s[~f])]
            for fl, x in rows:
                a, b = x[x.era == "2024"], x[x.era == "2026"]
                lo, hi = boot(b[ex].to_numpy(), b.day.to_numpy())
                L.append(f"| {nm} | {ex} | {fl} | {len(a)} / {a[ex].mean():+.2%} | {len(b)} / {b[ex].mean():+.2%} | [{lo:+.2%}, {hi:+.2%}] |")
    # (b) B43 Upbit long variants
    T = pd.read_parquet(DB / "b43_trades.parquet")
    T["d"] = T.t // 86400
    L += ["", "## (b) Upbit long-only variants (B43) with the filter; N chosen on val per variant, judged on TEST 2026", "",
          "| family | variant | best N (val) | val mean filtered | test n filtered | test mean filtered | test 95% CI | test mean unfiltered |", "|---|---|---|---|---|---|---|---|"]
    passes = []
    for (fam, var), g in T.groupby(["family", "variant"], sort=False):
        best = None
        for n, up in UP.items():
            f = g.d.map(up).fillna(False).astype(bool)
            va = g[f & (g.era == "val")]
            if len(va) >= 20 and (best is None or va.net.mean() > best[1]):
                best = (n, va.net.mean())
        if not best:
            continue
        f = g.d.map(UP[best[0]]).fillna(False).astype(bool)
        te = g[f & (g.era == "test")]
        if len(te) < 10:
            continue
        lo, hi = boot(te.net.to_numpy(), te.d.to_numpy())
        L.append(f"| {fam} | {var} | MA{best[0]} | {best[1]:+.2%} | {len(te)} | {te.net.mean():+.2%} | [{lo:+.2%}, {hi:+.2%}] | {g[g.era == 'test'].net.mean():+.2%} |")
        if lo > 0:
            passes.append((fam, var, best[0], te.net.mean(), lo))
    L += ["", f"Variants whose filtered TEST CI is above 0: {len(passes)}" + ("" if not passes else ": " + "; ".join(f"{a} / {b} (MA{c}, {d:+.2%}, CI lo {e:+.2%})" for a, b, c, d, e in passes)),
          "Note: many variants were tried (multiple testing); a lone CI > 0 among ~60 needs forward confirmation before trading."]
    (ROOT / "research/b45_long_regime.md").write_text("\n".join(L) + "\n")
    print("\n".join(L[-4:]))


if __name__ == "__main__":
    main()
