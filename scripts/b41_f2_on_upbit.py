"""B41 (2026-10-04, asked by 유리): does the frozen F2 pump CNN work on UPBIT KRW pumps, executed long-only on Upbit?

Events: every Upbit KRW hour with close/close(-1h) >= +10% (upbit_1h, 2026-03-16..now), 24h KRW value >= 2.7bn (~$2M),
>= 72 hourly candles of history. For each event the 1-minute window [T-60m, T+60s+4h] is fetched from Upbit REST
(cached in data/cache/b41). The SAME frozen ensemble and thresholds as F2 score the last 60 one-minute bars (same image
renderer). Long trades only (Upbit spot cannot short): entry = open of T+60s, exit = open 4h later, or the F2b trailing
exit (arm +15% on closes, give back 10 points). Cost = 2 x 0.05% Upbit fee + slippage by 24h value (same tiers as F2,
KRW converted at 1,370/USD). Compared with (a) all pump events, (b) events the CNN would short (avoid set).
No parameter is fitted here; this is a transfer test. Output research/b41_f2_on_upbit.md. Paper research only.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import b37_upbit_only as b37  # noqa: E402
from b2_dataset import image  # noqa: E402
from b2_models import CNN2D  # noqa: E402
from scripts.upbit_listing_watcher import db  # noqa: E402

b37.CACHE = ROOT / "data/cache/b41"; b37.CACHE.mkdir(exist_ok=True)
MOD, FEE, USDKRW, H = ROOT / "data/models/pump_cnn", 0.0005, 1370.0, 240
META = json.loads((MOD / "meta.json").read_text())


def slip(dv_usd):
    return 0.0002 if dv_usd > 1e8 else 0.0005 if dv_usd > 2e7 else 0.0010 if dv_usd > 5e6 else 0.0020


def events():
    d = pd.DataFrame(db("SELECT symbol, ts, close, value_krw FROM upbit_1h ORDER BY symbol, ts", ()))
    out = []
    for sym, g in d.groupby("symbol"):
        g = g.set_index("ts").sort_index()
        g = g.reindex(range(int(g.index[0]), int(g.index[-1]) + 3600, 3600))
        g["close"] = g["close"].ffill(); g["value_krw"] = g["value_krw"].fillna(0)
        r1 = g["close"] / g["close"].shift(1) - 1
        v24 = g["value_krw"].rolling(24, min_periods=24).sum()
        age = np.arange(len(g))
        m = (r1 >= 0.10) & (v24 >= 2.7e9) & (age >= 72)
        for ts in g.index[m.to_numpy()]:
            out.append((sym.split("/")[0], int(ts) + 3600, float(r1.at[ts]), float(v24.at[ts])))
    return pd.DataFrame(out, columns=["base", "T", "ret_1h", "v24_krw"])


def main():
    t0 = time.time()
    ms = []
    for s in META["seeds"]:
        m = CNN2D(); m.load_state_dict(torch.load(MOD / f"seed{s}.pt", map_location="cpu")); m.eval(); ms.append(m)
    ev = events()
    print("events", len(ev), flush=True)
    rows = []
    for i, e in ev.iterrows():
        w = b37.upbit_1m(f"KRW-{e.base}", e.T - 3600, e.T + 60 + H * 60)
        if w is None or not len(w):
            continue
        w = w.set_index("ts").reindex(range(e.T - 3600, e.T + 60 + (H + 1) * 60, 60))
        w["c"] = w["c"].ffill().bfill()
        for k in ("o", "h", "l"):
            w[k] = w[k].fillna(w["c"])
        w["v"] = w["v"].fillna(0)
        pre = w.loc[e.T - 3600:e.T - 60]
        if len(pre) != 60 or pre["v"].sum() <= 0:
            continue
        img = image(*(pre[k].to_numpy(float) for k in ("o", "h", "l", "c", "v")))
        x = torch.tensor(img.astype(np.float32)[None, None] / 255.0)
        with torch.no_grad():
            p = float(np.mean([torch.sigmoid(m(x)).item() for m in ms]))
        post = w.loc[e.T + 60:]
        o, c = post["o"].to_numpy(float), post["c"].to_numpy(float)
        cost = 2 * (FEE + slip(e.v24_krw / USDKRW))
        ro, rc = o / o[0] - 1, c / o[0] - 1
        base = ro[H] - cost
        trail, pk = base, -1e9
        for k in range(H):
            pk = max(pk, rc[k])
            if pk >= 0.15 and rc[k] <= pk - 0.10:
                trail = ro[k + 1] - cost - cost / 2
                break
        rows.append(dict(base=e.base, T=e.T, ret_1h=e.ret_1h, v24_krw=e.v24_krw, p=p,
                         cnn="long" if p >= META["hi"] else "short" if p <= META["lo"] else "none",
                         long_4h=base, long_trail=trail, worst_1m=float(post["l"].min() / o[0] - 1)))
        if i % 100 == 0:
            print(i, len(rows), f"{time.time() - t0:.0f}s", flush=True)
    r = pd.DataFrame(rows)
    r.to_parquet(ROOT / "data/cache/b41_upbit_f2.parquet")
    r["day"] = r["T"] // 86400
    L = ["# B41 F2 pump CNN on Upbit KRW pumps, long-only on Upbit (2026-10-04)", "",
         f"{len(r)} Upbit +10%/1h events, {pd.Timestamp(r['T'].min(), unit='s'):%Y-%m-%d}..{pd.Timestamp(r['T'].max(), unit='s'):%Y-%m-%d}. "
         "Frozen F2 ensemble, unchanged thresholds. Net after 2x0.05% fee + slippage. Long = buy on Upbit at T+60s.", "",
         "| set | n | mean 4h | median | win | p10 | worst | mean trail | trail win |", "|---|---|---|---|---|---|---|---|---|"]

    def boot(x, d):
        rng = np.random.default_rng(5); u = np.unique(d)
        g = {k: x[d == k] for k in u}
        b = [np.concatenate([g[k] for k in rng.choice(u, len(u))]).mean() for _ in range(2000)]
        return np.percentile(b, [2.5, 97.5])
    for nm, s in (("all pumps (buy every one)", r), ("CNN says LONG", r[r.cnn == "long"]),
                  ("CNN says SHORT (avoid set)", r[r.cnn == "short"]), ("CNN no trade", r[r.cnn == "none"])):
        if not len(s):
            continue
        L.append(f"| {nm} | {len(s)} | {s.long_4h.mean():+.2%} | {s.long_4h.median():+.2%} | {(s.long_4h > 0).mean():.0%} | "
                 f"{s.long_4h.quantile(.1):+.1%} | {s.long_4h.min():+.1%} | {s.long_trail.mean():+.2%} | {(s.long_trail > 0).mean():.0%} |")
    lg = r[r.cnn == "long"]
    if len(lg) >= 10:
        lo, hi = boot(lg.long_4h.to_numpy(), lg.day.to_numpy()); lo2, hi2 = boot(lg.long_trail.to_numpy(), lg.day.to_numpy())
        L += ["", f"CNN-LONG on Upbit: 4h mean 95% CI (day-clustered) [{lo:+.2%}, {hi:+.2%}]; trail [{lo2:+.2%}, {hi2:+.2%}]."]
    L += ["", f"Runtime {time.time() - t0:.0f}s."]
    (ROOT / "research/b41_f2_on_upbit.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
