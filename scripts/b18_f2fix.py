"""B18_F2FIX (registered 2026-09-30, after the main book lost -$8.07 on its first 6 F2 trades).
Can simple, pre-registered rules make the frozen pump CNN (F2) avoid its losing trades without curve-fitting?
Data: every +10% trigger hour of the frozen model's out-of-sample sets. DISCOVERY = 2024 holdout (Mar 2024 - Feb 2025),
VALIDATION = 2026 test (Jan - Sep 2026). Rules are chosen on discovery only; a rule passes if, on validation,
(a) mean net per trade has a day-clustered 95% CI above 0 and (b) it beats the unfiltered base in BOTH periods.
Rules:
  base      Dec-2025 thresholds (long p >= hi, short p <= lo), equal size, hold 4h (= live F2)
  tight     only the most confident trades: p above the 2024 85th pct / below the 15th pct
  short     short leg only
  breadth   shorts only when < 60% of liquid alts are up over 24h; longs only when > 40% (don't fight the tape)
  btc       skip shorts when BTC is up > 2% over 24h; skip longs when BTC is down > 2%
  cool24    one trade per coin per 24h
  volsize   size = 2% / (7-day hourly vol * sqrt(4)) capped at 1x (risk-equalised), scored by daily P&L Sharpe
Costs: the dataset's per-trade cost (2 x (fee + slippage by 24h volume)). Output data/reports/b18/f2fix.{json,md}.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b7_lib as L  # noqa: E402
from b2_models import boot_day  # noqa: E402
from m7_stress import load, score  # noqa: E402

OUT = ROOT / "data/reports/b18"
VA_END = int(pd.Timestamp("2026-01-01").timestamp())


def frame(z, mask, ms, meta):
    p = score(ms, z["img"][mask])
    d = pd.DataFrame({"ts": z["ts"][mask].astype(np.int64), "code": z["code"][mask].astype(str), "p": p,
                      "g": z["gross"][mask], "c": z["cost"][mask]})
    d["side"] = np.where(d.p >= meta["hi"], 1, np.where(d.p <= meta["lo"], -1, 0))
    return d


def add_context(d):
    ts, codes, X = L.data()
    idx = {c: j for j, c in enumerate(codes)}
    row = np.clip(np.searchsorted(ts, d.ts.to_numpy(), side="right") - 1, 0, len(ts) - 1)
    lc, U = X["lc"], X["U"]
    r24 = lc - L.lag(lc, 24)
    up = np.where(U, r24 > 0, np.nan)
    breadth = np.nanmean(up, 1)
    bi = codes.index("BTCUSDT")
    d["breadth"] = breadth[row]
    d["btc24"] = np.exp(r24[row, bi]) - 1
    vol = L.SD(X["r1"], 168)
    j = np.array([idx.get(c, -1) for c in d.code])
    d["vol7"] = np.where(j >= 0, vol[row, np.maximum(j, 0)], np.nan)
    return d


def stats(d, w=None):
    if len(d) < 20:
        return {"n": int(len(d))}
    w = np.ones(len(d)) if w is None else w
    net = w * (d.side * d.g - d.c)
    lo, hi = boot_day(net.to_numpy(), (d.ts // 86400).to_numpy())
    day = net.groupby(d.ts // 86400).sum()
    return {"n": int(len(d)), "mean": float(net.mean()), "ci": [float(lo), float(hi)], "total": float(net.sum()),
            "day_sharpe": float(day.mean() / day.std() * np.sqrt(365)) if day.std() > 0 else None,
            "win": float((net > 0).mean())}


def rules(d, q15, q85):
    t = d[d.side != 0]
    out = {"base": (t, None), "tight": (d.assign(side=np.where(d.p >= q85, 1, np.where(d.p <= q15, -1, 0))).query("side != 0"), None),
           "short": (t[t.side < 0], None),
           "breadth": (t[((t.side < 0) & (t.breadth < 0.6)) | ((t.side > 0) & (t.breadth > 0.4))], None),
           "btc": (t[~(((t.side < 0) & (t.btc24 > 0.02)) | ((t.side > 0) & (t.btc24 < -0.02)))], None)}
    s = t.sort_values("ts"); keep, last = [], {}
    for r in s.itertuples():
        if r.ts - last.get(r.code, -10 ** 12) >= 86400:
            keep.append(r.Index); last[r.code] = r.ts
    out["cool24"] = (s.loc[keep], None)
    w = np.minimum(1.0, 0.02 / (t.vol7.fillna(t.vol7.median()) * 2.0)).to_numpy()
    out["volsize"] = (t, w)
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ms, meta = load()
    z = np.load(ROOT / "data/cache/b2_ds_pump.npz", allow_pickle=True)
    h = np.load(ROOT / "data/cache/b2_ds_pump_2024.npz", allow_pickle=True)
    disc = add_context(frame(h, np.ones(len(h["ts"]), bool), ms, meta))
    val = add_context(frame(z, z["ts"] >= VA_END, ms, meta))
    q15, q85 = np.quantile(disc.p, [0.15, 0.85])
    R = {"thresholds": {"hi": meta["hi"], "lo": meta["lo"], "q15_2024": float(q15), "q85_2024": float(q85)}, "rules": {}}
    rd, rv = rules(disc, q15, q85), rules(val, q15, q85)
    for k in rd:
        sd, sv = stats(*rd[k]), stats(*rv[k])
        R["rules"][k] = {"disc": sd, "val": sv}
    bd, bv = R["rules"]["base"]["disc"], R["rules"]["base"]["val"]
    for k, v in R["rules"].items():
        sd, sv = v["disc"], v["val"]
        key = "day_sharpe" if k == "volsize" else "mean"
        v["pass"] = bool(k != "base" and sv.get("ci", [0])[0] > 0 and sd.get(key, -9) > bd[key] and sv.get(key, -9) > bv[key])
    json.dump(R, open(OUT / "f2fix.json", "w"), indent=1)
    Ls = ["# B18_F2FIX: can pre-registered filters fix F2?", "", "| rule | 2024 n | 2024 mean | 2024 CI | 2026 n | 2026 mean | 2026 CI | 2026 day Sharpe | pass |", "|---|---|---|---|---|---|---|---|---|"]
    for k, v in R["rules"].items():
        a, b = v["disc"], v["val"]
        f = lambda s: (f"{s['mean']*100:+.2f}%", f"[{s['ci'][0]*100:+.2f}, {s['ci'][1]*100:+.2f}]") if "mean" in s else ("-", "-")
        Ls.append(f"| {k} | {a['n']} | {f(a)[0]} | {f(a)[1]} | {b['n']} | {f(b)[0]} | {f(b)[1]} | {b.get('day_sharpe') or 0:.2f} | {'PASS' if v['pass'] else '-'} |")
    (OUT / "f2fix.md").write_text("\n".join(Ls) + "\n")
    print("\n".join(Ls))


if __name__ == "__main__":
    main()
