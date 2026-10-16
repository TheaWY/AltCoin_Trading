"""B23_SEP28 (2026-10-01). Since 2026-09-28 the F2 pump CNN lost on 11 of 15 forward trades; "doing the opposite would have
made money". Questions, each answered with data:
  A  What happened to every +10% trigger since 09-27 (traded or not): 4h return, best/worst excursion, and did the model's
     probability still rank outcomes (Spearman p vs return)?
  B  How unusual is this week in the 2024 + 2026 backtests? Weekly F2 mean net: how often this bad, lag-1 autocorrelation,
     and the P&L of (i) always-inverse and (ii) invert-the-next-week-after-a-losing-week.
  C  Pre-registered regime switch RS: trade side x sign(mean 4h return of all triggers closed in the previous 72h).
     Chosen before looking at 2026/live: discovery 2024, validation 2026, then this week.
Output data/reports/b23/sep28.{json,md}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch

torch.set_num_threads(1)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import b18_f2fix as FX  # noqa: E402
from m7_stress import load  # noqa: E402
from src.data.storage import get_storage  # noqa: E402

OUT = ROOT / "data/reports/b23"
FEE = 0.0005


def q(sql, params=()):
    st = get_storage()
    with st._connect() as c:  # noqa: SLF001
        cur = c.raw.cursor(); cur.execute(sql.replace("?", "%s"), params)
        return pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])


def live():
    d = q("SELECT symbol, ts_signal, p_mean, side, cost, net, status FROM pump_cnn_paper ORDER BY ts_signal")
    rows = []
    for r in d.itertuples():
        e0, e1 = int(r.ts_signal) + 60, int(r.ts_signal) + 60 + 240 * 60
        px = q("SELECT ts, open, high, low FROM prices_1m WHERE symbol=? AND ts >= ? AND ts <= ? ORDER BY ts", (r.symbol, e0, e1))
        if len(px) < 200 or px.ts.iloc[-1] != e1:
            continue
        p0, p1 = float(px.open.iloc[0]), float(px.open.iloc[-1])
        rows.append(dict(symbol=r.symbol, ts=int(r.ts_signal), p=float(r.p_mean), side=int(r.side), ret4h=p1 / p0 - 1,
                         mfe=float(px.high.max()) / p0 - 1, mae=float(px.low.min()) / p0 - 1, cost=float(r.cost or 2 * FEE)))
    return pd.DataFrame(rows)


def weekly(t):
    w = t.assign(wk=(t.ts // (7 * 86400))).groupby("wk").net.agg(["mean", "size", "sum"])
    return w[w["size"] >= 5]


def regime(t, all_trig):
    """sign of mean raw 4h return of all triggers whose 4h window closed in the previous 72h (known at entry)."""
    a = all_trig.sort_values("ts")
    close_t = a.ts.to_numpy() + 4 * 3600
    r = a.ret.to_numpy()
    cs = np.concatenate([[0], np.cumsum(r)])
    sgn = []
    for ts in t.ts.to_numpy():
        hi = np.searchsorted(close_t, ts, side="right"); lo = np.searchsorted(close_t, ts - 72 * 3600, side="left")
        n = hi - lo
        sgn.append(np.sign((cs[hi] - cs[lo]) / n) if n >= 5 else 1.0)
    return np.array(sgn)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    R = {}
    # ---------- A: live triggers
    L = live()
    L["long_ret"] = L.ret4h
    since = int(pd.Timestamp("2026-09-28", tz="Asia/Seoul").timestamp())
    for tag, d in (("before_0928", L[L.ts < since]), ("since_0928", L[L.ts >= since])):
        tr = d[d.side != 0]
        net = tr.side * tr.ret4h - tr.cost
        R[f"A_{tag}"] = {
            "triggers": int(len(d)), "mean_4h_all_triggers": float(d.ret4h.mean()), "median_4h": float(d.ret4h.median()),
            "share_up": float((d.ret4h > 0).mean()), "mean_best_excursion": float(d.mfe.mean()), "mean_worst_excursion": float(d.mae.mean()),
            "spearman_p_vs_ret": float(d.p.rank().corr(d.ret4h.rank())) if len(d) > 5 else None,
            "trades": int(len(tr)), "mean_net": float(net.mean()) if len(tr) else None, "inverse_mean_net": float((-tr.side * tr.ret4h - tr.cost).mean()) if len(tr) else None,
            "longs_mean_ret": float(tr[tr.side > 0].ret4h.mean()) if (tr.side > 0).any() else None,
            "shorts_mean_ret": float(tr[tr.side < 0].ret4h.mean()) if (tr.side < 0).any() else None,
            "skipped_mean_ret": float(d[d.side == 0].ret4h.mean()) if (d.side == 0).any() else None}
    # ---------- B: backtest weekly behaviour
    ms, meta = load()
    bt = {}
    for tag, f, sel in (("2024", "data/cache/b2_ds_pump_2024.npz", False), ("2026", "data/cache/b2_ds_pump.npz", True)):
        z = np.load(ROOT / f, allow_pickle=True)
        m = (z["ts"] >= FX.VA_END) if sel else np.ones(len(z["ts"]), bool)
        d = FX.frame(z, m, ms, meta).sort_values("ts").reset_index(drop=True)
        d["ret"] = d.g
        bt[tag] = d
    live_wk = L[L.ts >= since]
    this = float((live_wk[live_wk.side != 0].side * live_wk[live_wk.side != 0].ret4h - live_wk[live_wk.side != 0].cost).mean())
    for tag, d in bt.items():
        t = d[d.side != 0].copy(); t["net"] = t.side * t.ret - t.c
        w = weekly(t)
        inv = (-t.side * t.ret - t.c)
        prev_bad = (w["mean"].shift(1) < 0).reindex(t.ts // (7 * 86400)).to_numpy()
        flip = np.where(prev_bad == True, -1, 1)  # noqa: E712
        R[f"B_{tag}"] = {"weeks": int(len(w)), "mean_weekly": float(w["mean"].mean()),
                         "weeks_as_bad_as_now": int((w["mean"] <= this).sum()), "share_as_bad": float((w["mean"] <= this).mean()),
                         "lag1_autocorr": float(w["mean"].autocorr(1)), "always_inverse_mean": float(inv.mean()),
                         "invert_after_bad_week_mean": float((flip * t.side * t.ret - t.c).mean())}
    R["this_week_mean_net"] = this
    # ---------- C: regime switch
    for tag, d in bt.items():
        t = d[d.side != 0].copy()
        s = regime(t, d[["ts", "ret"]])
        base = t.side * t.ret - t.c; rs = s * t.side * t.ret - t.c
        lo, hi = FX.boot_day(rs.to_numpy(), (t.ts // 86400).to_numpy())
        R[f"C_{tag}"] = {"base": float(base.mean()), "rs": float(rs.mean()), "rs_ci": [float(lo), float(hi)], "share_flipped": float((s < 0).mean())}
    lt = L[L.side != 0].copy()
    s = regime(lt, L.rename(columns={"ret4h": "ret"})[["ts", "ret"]])
    for tag, mk in (("before_0928", lt.ts < since), ("since_0928", lt.ts >= since)):
        x = lt[mk]; sx = s[mk.to_numpy()]
        R[f"C_live_{tag}"] = {"n": int(len(x)), "base": float((x.side * x.ret4h - x.cost).mean()), "rs": float((sx * x.side * x.ret4h - x.cost).mean()),
                              "share_flipped": float((sx < 0).mean())}
    R["C_pass"] = bool(R["C_2024"]["rs"] > R["C_2024"]["base"] and R["C_2026"]["rs_ci"][0] > 0 and R["C_2026"]["rs"] > R["C_2026"]["base"])
    json.dump(R, open(OUT / "sep28.json", "w"), indent=1, default=float)
    out = ["# B23: why F2 lost since 2026-09-28", ""]
    for k, v in R.items():
        out.append(f"- **{k}**: {json.dumps(v, default=float) if not isinstance(v, float) else f'{v:+.4f}'}")
    L.to_csv(OUT / "live_triggers.csv", index=False)
    (OUT / "sep28.md").write_text("\n".join(out) + "\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()
