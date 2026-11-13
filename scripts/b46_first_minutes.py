"""B46 / U5 (2026-10-04, Upbit queue): first-minutes entry timing after an Upbit +10%/1h pump, LONG ONLY.

Data: 1-second bars. Upbit's own s1 (last ~88 days, all in the TEST era) and the Binance aggTrades proxy bn1s
(older events, coins also listed on Binance). T = close of the +10% hour (known at T, no look-ahead in selection).

Pre-registered protocol (fixed before any result):
  eras            train < 2025-07-01 | val 2025-07-01..2025-12-31 | TEST >= 2026-01-01 (touched once)
                  train/val use bn1s (proxy path); TEST is reported on Upbit s1 (primary) and bn1s 2026 (secondary)
  entry           at T + d seconds, d in {5, 30, 60, 180, 300, 600}; price = last 1s close before entry
  conditions      (all from bars strictly before entry)
                    all        every event
                    dip_k      price is >= k% below the max close of [T-3600, entry), k in {3, 6}
                    up60       last-60s return > 0
                    down60     last-60s return < 0
                    calm       |last-300s return| < 1%
                    dip3_up60  dip_3 and up60
  holds           1h, 4h (exit at last close at or before entry + hold)
  cost            2 x 0.05% Upbit fee + 2 x slippage by 24h KRW value (same tiers as B42)
  selection       pick (delay, cond, hold) with best val mean net among cells with >= 30 val trades
  pass            TEST (Upbit s1) mean net > 0 and day-clustered 95% CI lower bound > 0
  mirror          -gross - cost reported for every row (shorts impossible on Upbit; used only as avoid evidence)
Look-ahead guard: features use bars with ts < entry (searchsorted 'left' - 1); asserted below.
Output: research/b46_first_minutes.md, data/upbit_db/b46_trades.parquet. Paper research only.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/upbit_db"
FEE, USDKRW = 0.0005, 1370.0
DELAYS = [5, 30, 60, 180, 300, 600]
HOLDS = {"1h": 3600, "4h": 14400}
CONDS = ["all", "dip3", "dip6", "up60", "down60", "calm", "dip3_up60"]
TRAIN_END, VAL_END = pd.Timestamp("2025-07-01").timestamp(), pd.Timestamp("2026-01-01").timestamp()


def slip(v):
    dv = v / USDKRW
    return 0.0002 if dv > 1e8 else 0.0005 if dv > 2e7 else 0.0010 if dv > 5e6 else 0.0020


def era(t):
    return "train" if t < TRAIN_END else "val" if t < VAL_END else "test"


def rows_for(src, f, v24):
    mkt, T = f.stem.rsplit("_", 1)
    T = int(T)
    x = pd.read_parquet(f, columns=["ts", "c"]).sort_values("ts")
    ts, c = x.ts.to_numpy(np.int64), x.c.to_numpy(float)
    if len(ts) < 100:
        return []
    cost = 2 * (FEE + slip(v24))
    out = []
    for d in DELAYS:
        e = T + d
        j = np.searchsorted(ts, e, "left") - 1          # last bar strictly before entry
        if j < 0 or ts[j] < e - 300:
            continue
        assert ts[j] < e
        p = c[j]
        h0 = np.searchsorted(ts, T - 3600, "left")
        hi = c[h0:j + 1].max()
        k60 = np.searchsorted(ts, e - 60, "left") - 1
        k300 = np.searchsorted(ts, e - 300, "left") - 1
        r60 = p / c[k60] - 1 if k60 >= 0 else np.nan
        r300 = p / c[k300] - 1 if k300 >= 0 else np.nan
        dd = 1 - p / hi
        flags = {"all": True, "dip3": dd >= 0.03, "dip6": dd >= 0.06, "up60": r60 > 0, "down60": r60 < 0,
                 "calm": abs(r300) < 0.01, "dip3_up60": dd >= 0.03 and r60 > 0}
        for hn, H in HOLDS.items():
            xi = np.searchsorted(ts, e + H, "right") - 1
            if xi <= j or ts[xi] < e + H - 600:
                continue
            g = c[xi] / p - 1
            for cn, ok in flags.items():
                if ok:
                    out.append((src, mkt, T, era(T), d, cn, hn, g, g - cost, -g - cost))
    return out


def boot(x, day, n=2000, seed=9):
    if len(x) < 3:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed); u = np.unique(day)
    g = {k: x[day == k] for k in u}
    m = [np.concatenate([g[k] for k in rng.choice(u, len(u))]).mean() for _ in range(n)]
    return np.percentile(m, [2.5, 97.5])


def main():
    t0 = time.time()
    ev = pd.read_parquet(DB / "events.parquet")
    v = {(m, int(t)): x for m, t, x in zip(ev.market, ev["T"], ev.v24_krw)}
    R = []
    for src in ["s1", "bn1s"]:
        for f in sorted((DB / src).glob("*.parquet")):
            mkt, T = f.stem.rsplit("_", 1)
            if (mkt, int(T)) in v:
                R += rows_for(src, f, v[(mkt, int(T))])
    D = pd.DataFrame(R, columns=["src", "market", "T", "era", "delay", "cond", "hold", "gross", "net", "mirror"])
    D["day"] = (D["T"] // 86400).astype(int)
    D.to_parquet(DB / "b46_trades.parquet")
    key = ["delay", "cond", "hold"]
    proxy = D[D.src == "bn1s"]
    tab = proxy.pivot_table(index=key, columns="era", values="net", aggfunc=["mean", "count"])
    tab.columns = [f"{a}_{b}" for a, b in tab.columns]
    cand = tab[tab.get("count_val", 0) >= 30].sort_values("mean_val", ascending=False)
    best = cand.index[0]
    L = ["# B46 / U5: first-minutes entry timing after Upbit +10%/1h pumps (long only)", "",
         f"Run {time.strftime('%Y-%m-%d %H:%M')}, {time.time() - t0:.0f}s. Protocol in the script docstring.", "",
         f"Events: Upbit s1 {D[D.src == 's1'][['market','T']].drop_duplicates().shape[0]}, "
         f"Binance proxy bn1s {proxy[['market','T']].drop_duplicates().shape[0]}.", "",
         "## Proxy (bn1s) grid: mean net by era (long), with mirror on train", "",
         "| delay s | cond | hold | train | n | val | n | test(proxy) | n | mirror train |", "|---|---|---|---|---|---|---|---|---|---|"]
    mir = proxy[proxy.era == "train"].groupby(key).mirror.mean()
    for k, r in tab.sort_values("mean_val", ascending=False).iterrows():
        L.append(f"| {k[0]} | {k[1]} | {k[2]} | {r.get('mean_train', np.nan):+.2%} | {r.get('count_train', 0):.0f} | "
                 f"{r.get('mean_val', np.nan):+.2%} | {r.get('count_val', 0):.0f} | {r.get('mean_test', np.nan):+.2%} | "
                 f"{r.get('count_test', 0):.0f} | {mir.get(k, np.nan):+.2%} |")
    L += ["", "## Upbit s1 (actual Upbit 1s, all TEST era): every cell", "",
          "| delay s | cond | hold | mean net | n | win | mirror |", "|---|---|---|---|---|---|---|"]
    up = D[(D.src == "s1") & (D.era == "test")]
    for k, g in up.groupby(key):
        L.append(f"| {k[0]} | {k[1]} | {k[2]} | {g.net.mean():+.2%} | {len(g)} | {(g.net > 0).mean():.0%} | {g.mirror.mean():+.2%} |")
    L += ["", "## Verdict (selected on validation, TEST touched once)", "",
          "| selected | val | TEST Upbit s1 | n | 95% CI (day-clustered) | TEST proxy | verdict |", "|---|---|---|---|---|---|---|"]
    t = up.set_index(key).loc[best] if best in up.set_index(key).index else up.iloc[:0]
    t = t if isinstance(t, pd.DataFrame) else t.to_frame().T
    lo, hi = boot(t.net.to_numpy(float), t.day.to_numpy()) if len(t) else (np.nan, np.nan)
    m = t.net.mean() if len(t) else np.nan
    ok = len(t) >= 10 and m > 0 and lo > 0
    L.append(f"| d={best[0]}s {best[1]} hold {best[2]} | {cand.loc[best, 'mean_val']:+.2%} | {m:+.2%} | {len(t)} | "
             f"[{lo:+.2%}, {hi:+.2%}] | {cand.loc[best].get('mean_test', np.nan):+.2%} | **{'PASS' if ok else 'FAIL'}** |")
    (ROOT / "research/b46_first_minutes.md").write_text("\n".join(L) + "\n")
    print("\n".join(L[-3:]))


if __name__ == "__main__":
    main()
