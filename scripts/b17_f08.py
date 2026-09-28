"""B17 F08 regime conditioning + F10 sizing for the F04 pass (oi_drop3 short, 5% stop, exit +4h).  Registry F08/F10.
  OMP_NUM_THREADS=6 .venv/bin/python -W ignore scripts/b17_f08.py trades|f08|f10|all   -> data/cache/b17/f08_trades.parquet, data/reports/b17/{f08,f10}.json
trades: rebuilds the oi_drop3 entries exactly as b17_f04.f04 and records per-trade net for three stop models:
  net      = F04 model (stop checked on minute closes, loss capped at stop+0.2%)
  net_cons = conservative intrabar stop: bar high bounded by min(prev close, close) * (1 + (h/l-1)); a touch fills at stop+0.2%;
             a bar that closes beyond the stop fills at that close + 0.2% (gap)
  net_gap  = closes only, but a close beyond the stop fills at that close (+0.2%), no cap
  also taker_flip re-run with the correct threshold (taker channel is share-0.5, so share<0.40 is tk<-0.10; F04 used tk<0.40 = share<0.90).
F08: variables HMM state (b14 regime), BTC 24h log return at onset (sign), funding at onset (neg / 0-0.01% / >0.01% / unknown), KST 6h block of
  entry, weekend (KST Sat/Sun). Rule per variable = keep levels whose discovery mean net_cons > 0 (n >= 30). Validation: filtered mean,
  excluded mean, day-clustered bootstrap P(filtered - excluded <= 0); BH q=0.10 over the variables; pass = BH AND filtered val CI > 0.
F10: time-ordered portfolio on each period with net_cons. Schemes: fixed 10% notional; vol-target (10% x median rv / rv, 3-30%); quarter-Kelly
  from discovery mean/var (cap 50%); each with concurrency caps none/5/3. Metrics: total return, daily Sharpe (ann. sqrt 365), max DD, skipped share.
  Scheme selected on discovery Sharpe, confirmed on validation (Sharpe > 0 and DD not worse than 2x discovery)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b7_lib as L  # noqa: E402
import b15_models as M  # noqa: E402
from b17_f03 import bh, load  # noqa: E402
from b17_f04 import oi_series  # noqa: E402

C, OUT, B = M.C, ROOT / "data/reports/b17", ROOT / "data/cache/b17"
RNG = np.random.default_rng(1708)
VAL0 = 1767225600
M0, STOP, HZ, SLIP = 60, 0.05, 240, 0.002


def short_leg(r, rg, e):
    """short entered at the close of minute M0+e; returns (net_f04, net_cons, net_gap, mae_cons, minutes_held) before costs."""
    pe = float(r[M0 + e])
    c = (1 + r[M0 + e + 1:M0 + e + 1 + HZ].astype(np.float64)) / (1 + pe) - 1          # close-to-entry return, long sense
    if len(c) == 0 or not np.isfinite(pe):
        return (np.nan,) * 5
    prev = np.r_[0.0, c[:-1]]
    hi = np.minimum(prev, c) + (1 + np.minimum(prev, c)) * rg[M0 + e + 1:M0 + e + 1 + len(c)].astype(np.float64)   # upper bound of bar high
    hi = np.maximum(hi, np.maximum(prev, c))
    # F04 model: stop on closes, capped
    k = np.flatnonzero(c >= STOP)
    f04 = -min(c[k[0]], STOP + SLIP) if len(k) else -c[-1]      # exactly b17_f03.leg: max(path, -stop-0.002)
    # gap model: stop on closes, fill at the close
    gap = -(c[k[0]] + SLIP) if len(k) else -c[-1]
    # conservative: first bar whose high bound touches the stop
    kh = np.flatnonzero(hi >= STOP)
    if len(kh):
        j = kh[0]; cons = -(max(c[j], STOP) + SLIP) if c[j] >= STOP else -(STOP + SLIP); held = j + 1
        mae = float(max(hi[:j + 1].max(), 0))
    else:
        cons = -c[-1]; held = len(c); mae = float(max(hi.max(), 0))
    return float(f04), float(cons), float(gap), mae, int(held)


def trades():
    P, A = load()
    n = len(P); OI = oi_series(P)
    rows = []
    for i in range(n):
        pid = int(P["pump_id"].iloc[i])
        r = np.asarray(A[i, :, 0], np.float64); rg = np.asarray(A[i, :, 3], np.float64); tk = np.asarray(A[i, :, 2], np.float64)
        ent = {}
        if pid in OI:
            t, oi = OI[pid]; post = t >= 0
            if post.sum() > 2:
                o = oi[post]; tt = t[post]; j = np.flatnonzero(o <= 0.97 * np.maximum.accumulate(o))
                if len(j) and tt[j[0]] // 60 + 2 < 1440:
                    ent["oi_drop3"] = int(tt[j[0]] // 60) + 1
        tk5 = np.nanmean(tk[M0:M0 + 1440].reshape(-1, 5), 1); j = np.flatnonzero(tk5 < -0.10); j = j[j >= 1]
        if len(j) and j[0] * 5 + 6 < 1440:
            ent["taker_flip_fixed"] = int(j[0] * 5 + 5)
        for trig, e in ent.items():
            if M0 + e + 1 >= A.shape[1]:
                continue
            f04, cons, gap, mae, held = short_leg(r, rg, e)
            rows.append(dict(trig=trig, i=i, pump_id=pid, code=P["code"].iloc[i], ts=int(P["ts"].iloc[i]), e=e, t_in=int(P["ts"].iloc[i]) + 60 * e,
                             t_out=int(P["ts"].iloc[i]) + 60 * (e + held), gross=f04, net=f04 - P["cost"].iloc[i], net_cons=cons - P["cost"].iloc[i],
                             net_gap=gap - P["cost"].iloc[i], mae_cons=mae, rv=float(np.nanstd(np.diff(r[:M0])) * np.sqrt(60))))
    T = pd.DataFrame(rows)
    T["val"] = T["ts"] >= VAL0; T["day"] = T["ts"] // 86400
    T.to_parquet(B / "f08_trades.parquet", index=False)
    for tr, g in T.groupby("trig"):
        for per, s in (("disc", ~g["val"]), ("val", g["val"])):
            x = g[s]
            print(tr, per, len(x), "net", round(x["net"].mean(), 4), "cons", round(x["net_cons"].mean(), 4), "gap", round(x["net_gap"].mean(), 4),
                  "ci_cons", [round(a, 4) for a in M.day_ci(x["net_cons"].to_numpy(), x["day"].to_numpy())], "mae90", round(x["mae_cons"].quantile(0.9), 3))
    return T


def regimes(T):
    P, _ = load()
    T = T.merge(P[["pump_id", "funding"]], on="pump_id", how="left")
    # HMM state of the PREVIOUS day (b14 state for day d is filtered on day d's full data -> intraday look-ahead for a pump on day d)
    R = pd.read_parquet(C / "b14/regime.parquet")[["day", "state"]]
    dcol = R["day"]; dnum = (pd.to_datetime(dcol).astype("datetime64[s]").astype("int64") // 86400) if not np.issubdtype(dcol.dtype, np.integer) else dcol
    smap = dict(zip(dnum.to_numpy(), R["state"].to_numpy()))
    T["state"] = [smap.get(d - 1, -1) for d in (T["t_in"] // 86400)]
    ts_h, codes, X = L.data(); bi = codes.index("BTCUSDT")
    hi = np.clip(np.searchsorted(ts_h, T["ts"].to_numpy(), "right") - 1, 24, len(ts_h) - 1)      # last closed hourly bar at onset
    T["btc24"] = X["lc"][hi, bi] - X["lc"][hi - 24, bi]
    kst = pd.to_datetime(T["t_in"] + 9 * 3600, unit="s")
    V = {"hmm_state": T["state"].astype("Int64").astype(str),
         "btc24_sign": np.where(T["btc24"] > 0, "up", "down"),
         "funding": np.select([T["funding"].isna(), T["funding"] < 0, T["funding"] <= 0.0001], ["unknown", "neg", "normal"], "high"),
         "kst_block": (kst.dt.hour // 6).map({0: "00-06", 1: "06-12", 2: "12-18", 3: "18-24"}).to_numpy(),
         "weekend": np.where(kst.dt.dayofweek >= 5, "weekend", "weekday")}
    for k, v in V.items():
        T[k] = np.asarray(v)
    return T, list(V)


def cl_boot(x, d, reps=2000):
    b = pd.DataFrame({"x": x, "d": d}).groupby("d")["x"].agg(["sum", "count"]); su, cn = b["sum"].to_numpy(), b["count"].to_numpy()
    return np.array([su[k].sum() / cn[k].sum() for k in (RNG.integers(0, len(su), len(su)) for _ in range(reps))])


def f08(T, col="net_cons"):
    T = T[T["trig"] == "oi_drop3"].copy(); T, VARS = regimes(T)
    res = {"col": col, "tests": {}, "levels": {}}; pv = {}
    D, V = T[~T["val"]], T[T["val"]]
    for var in VARS:
        lv = D.groupby(var)[col].agg(["mean", "count"]); res["levels"][var] = {
            str(k): dict(disc_mean=float(r["mean"]), disc_n=int(r["count"]),
                         val_mean=float(V.loc[V[var] == k, col].mean()) if (V[var] == k).any() else None, val_n=int((V[var] == k).sum())) for k, r in lv.iterrows()}
        keep = [k for k, r in lv.iterrows() if r["count"] >= 30 and r["mean"] > 0]
        if len(keep) == len(lv) or not keep:
            res["tests"][var] = dict(keep=keep, note="rule keeps all levels or none -> no conditioning"); continue
        fv, xv = V[V[var].isin(keep)], V[~V[var].isin(keep)]
        if len(fv) < 30 or len(xv) < 10:
            res["tests"][var] = dict(keep=keep, note="too few validation trades"); continue
        # paired-by-day bootstrap of filtered minus excluded
        days = np.unique(V["day"]); dg = {d: g for d, g in V.groupby("day")}; m = V[var].isin(keep).to_numpy()
        bo = []
        for _ in range(2000):
            s = pd.concat([dg[d] for d in RNG.choice(days, len(days))]); ms = s[var].isin(keep)
            if ms.any() and (~ms).any():
                bo.append(s.loc[ms, col].mean() - s.loc[~ms, col].mean())
        bo = np.array(bo)
        res["tests"][var] = dict(keep=keep, disc_filtered=float(D.loc[D[var].isin(keep), col].mean()), disc_excluded=float(D.loc[~D[var].isin(keep), col].mean()),
                                 val_filtered=float(fv[col].mean()), val_filtered_n=int(len(fv)), val_filtered_ci=M.day_ci(fv[col].to_numpy(), fv["day"].to_numpy()),
                                 val_excluded=float(xv[col].mean()), val_excluded_n=int(len(xv)), diff_ci=[float(a) for a in np.percentile(bo, [2.5, 97.5])],
                                 p=float((bo <= 0).mean()), share_kept=float(m.mean()))
        pv[var] = res["tests"][var]["p"]
    res["bh_pass"] = bh(pv) if pv else []
    res["pass"] = [k for k in res["bh_pass"] if res["tests"][k]["val_filtered_ci"][0] > 0]
    json.dump(res, open(OUT / "f08.json", "w"), indent=1, default=float)
    print("F08", col, "BH", res["bh_pass"], "PASS", res["pass"])
    for var, t in res["tests"].items():
        print(" ", var, json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in t.items()}))
    return res


def portfolio(T, w, cap):
    """event-driven equity: open at t_in with notional w_i * equity, settle at t_out; skip when `cap` positions are open."""
    ev = T.sort_values("t_in").reset_index(drop=True)
    eq, open_, skipped, pnl_day, curve = 1.0, [], 0, {}, []
    for _, r in ev.iterrows():
        still = []
        for (to, notional, ret) in open_:
            if to <= r["t_in"]:
                eq += notional * ret; pnl_day[to // 86400] = pnl_day.get(to // 86400, 0) + notional * ret; curve.append(eq)
            else:
                still.append((to, notional, ret))
        open_ = still
        if cap and len(open_) >= cap:
            skipped += 1; continue
        open_.append((r["t_out"], w(r) * eq, r["net_cons"]))
    for (to, notional, ret) in sorted(open_):
        eq += notional * ret; pnl_day[to // 86400] = pnl_day.get(to // 86400, 0) + notional * ret; curve.append(eq)
    curve = np.array([1.0] + curve); dd = float((curve / np.maximum.accumulate(curve) - 1).min())
    d0, d1 = int(ev["t_in"].min() // 86400), int(ev["t_out"].max() // 86400)
    daily = np.array([pnl_day.get(d, 0.0) for d in range(d0, d1 + 1)])
    sh = float(daily.mean() / daily.std() * np.sqrt(365)) if daily.std() > 0 else 0.0
    return dict(total=float(eq - 1), sharpe=sh, maxdd=dd, n=int(len(ev) - skipped), skipped=float(skipped / len(ev)), days=int(d1 - d0 + 1))


def f10():
    T = pd.read_parquet(B / "f08_trades.parquet"); T = T[T["trig"] == "oi_drop3"]
    D, V = T[~T["val"]], T[T["val"]]
    mu, var = D["net_cons"].mean(), D["net_cons"].var(); kelly = float(np.clip(0.25 * mu / var, 0, 0.5)) if mu > 0 else 0.0
    rv_med = float(D["rv"].median())
    SCH = {"fixed10": lambda r: 0.10, "voltarget": lambda r: float(np.clip(0.10 * rv_med / max(r["rv"], 1e-6), 0.03, 0.30)), "qkelly": lambda r: kelly}
    res = {"kelly_quarter": kelly, "rv_median": rv_med, "tests": {}}
    for s, w in SCH.items():
        for cap in (0, 5, 3):
            k = f"{s}|cap{cap or 'none'}"; res["tests"][k] = {"discovery": portfolio(D, w, cap), "validation": portfolio(V, w, cap)}
    best = max(res["tests"], key=lambda k: res["tests"][k]["discovery"]["sharpe"]); b = res["tests"][best]
    res["selected"] = best
    res["confirmed"] = bool(b["validation"]["sharpe"] > 0 and b["validation"]["maxdd"] >= 2 * b["discovery"]["maxdd"])
    json.dump(res, open(OUT / "f10.json", "w"), indent=1, default=float)
    print("F10 quarter-Kelly", round(kelly, 3), "selected", best, "confirmed", res["confirmed"])
    for k, v in res["tests"].items():
        print(" ", k, " | ".join(f"{p}: tot {x['total']:+.2%} sh {x['sharpe']:.2f} dd {x['maxdd']:.1%} n {x['n']} skip {x['skipped']:.0%}" for p, x in v.items()))
    return res


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd in ("trades", "all"):
        trades()
    if cmd in ("f08", "all"):
        T = pd.read_parquet(B / "f08_trades.parquet")
        f08(T, "net")                 # F04 stop model, for comparison (printed only)
        f08(T, "net_cons")            # primary = conservative stop; this call writes f08.json
    if cmd in ("f10", "all"):
        f10()
    print("F08_F10_DONE", flush=True)
