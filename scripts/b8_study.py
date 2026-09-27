"""B8 pump-precursor study (research/batch_B8.yaml).
  .venv/bin/python -W ignore scripts/b8_study.py features   # build aligned feature matrices -> data/cache/b8/*.npy
  .venv/bin/python -W ignore scripts/b8_study.py cc         # B8_1 case-control + B8_2 lead-time profile
  .venv/bin/python -W ignore scripts/b8_study.py model      # B8_3 pump-probability model (LightGBM; no torch in process)
Out: data/reports/b8/*.json"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b7_lib as L  # noqa: E402
from b7_lib import M, S, lag, safe_div  # noqa: E402

FD = ROOT / "data/cache/b8"
OUT = ROOT / "data/reports/b8"
H_, D_ = 3600, 86400
REG = ["K1_upbit_surge_6h", "K2_upbit_vs_binance_surge", "K3_krw_premium", "K4_krw_premium_change_6h",
       "K5_upbit_share_24h_change", "D1_oi_change_6h", "D2_oi_change_24h", "D3_oi_vs_price_6h", "D4_top_pos_ls_change_24h",
       "D5_global_ls_change_24h", "D6_taker_ratio_6h", "D7_oi_to_volume", "F1_funding_level", "F2_taker_imbalance_6h",
       "F3_quarter_hour_flow_6h", "N1_listing_notice_prior_24h"]
PRICE = ["P1_ret_6h", "P2_ret_24h", "P3_volume_surge_6h", "P4_range_6h"]


# ------------------------------------------------------------------ feature matrices
def aligned(folder, codes, ts, cols, keymap):
    out = {c: np.full((len(ts), len(codes)), np.nan, np.float32) for c in cols}
    for f in Path(folder).glob("*.parquet"):
        j, mult = keymap(f.stem)
        if j is None:
            continue
        d = pd.read_parquet(f)
        pos = np.searchsorted(ts, d["ts"].to_numpy())
        ok = (pos < len(ts)) & (ts[np.minimum(pos, len(ts) - 1)] == d["ts"].to_numpy())
        for c in cols:
            v = d[c].to_numpy(np.float64)
            out[c][pos[ok], j] = (v[ok] * (mult if c in ("o", "h", "l", "c") else 1)).astype(np.float32)
    return out


def features():
    FD.mkdir(parents=True, exist_ok=True)
    ts, codes, X = L.data()
    idx = {c: j for j, c in enumerate(codes)}
    # --- derivatives metrics (hourly)
    Mx = aligned(ROOT / "data/cache/metrics1h", codes, ts, ["oi", "oi_usd", "ls_top_pos", "ls_global", "taker_ratio"],
                 lambda s: (idx.get(s), 1))

    # --- Upbit KRW: map BASE -> Binance code (handles 1000x contracts: Upbit price per token = Binance / 1000)
    def upmap(base):
        for pre, mult in (("", 1.0), ("1000", 1000.0), ("1000000", 1e6)):
            if f"{pre}{base}USDT" in idx:
                return idx[f"{pre}{base}USDT"], mult
        return None, 1
    U = aligned(ROOT / "data/cache/upbit1h_hist", codes, ts, ["c", "value_krw"], upmap)
    usdt = pd.read_parquet(ROOT / "data/cache/upbit1h_hist/USDT.parquet").set_index("ts")["c"]
    fx = pd.Series(usdt).reindex(ts).ffill(limit=6).to_numpy()[:, None].astype(np.float32)
    up_usd = U["value_krw"] / fx
    qv = np.nan_to_num(X["qv"])
    c = X["c"]
    lc = X["lc"]
    F = {}
    up = np.nan_to_num(up_usd)
    has_up = np.isfinite(U["c"])
    base7 = M(up, 168) * 6
    F["K1_upbit_surge_6h"] = np.where(has_up, np.log(safe_div(S(up, 6) + 1, base7 + 1)), np.nan)
    F["K2_upbit_vs_binance_surge"] = F["K1_upbit_surge_6h"] - np.log(safe_div(S(qv, 6) + 1, M(qv, 168) * 6 + 1))
    prem = safe_div(U["c"], c * fx) - 1        # U['c'] already scaled to Binance contract units by upmap multiplier
    prem[np.abs(prem) > 0.5] = np.nan
    F["K3_krw_premium"] = prem
    F["K4_krw_premium_change_6h"] = prem - lag(prem, 6)
    share = safe_div(S(up, 24), S(up, 24) + S(qv, 24))
    F["K5_upbit_share_24h_change"] = np.where(has_up, share - M(share, 168), np.nan)
    oi = Mx["oi"]
    F["D1_oi_change_6h"] = np.log(safe_div(oi, lag(oi, 6)))
    F["D2_oi_change_24h"] = np.log(safe_div(oi, lag(oi, 24)))
    F["D3_oi_vs_price_6h"] = F["D1_oi_change_6h"] - (lc - lag(lc, 6))
    F["D4_top_pos_ls_change_24h"] = Mx["ls_top_pos"] - lag(Mx["ls_top_pos"], 24)
    F["D5_global_ls_change_24h"] = Mx["ls_global"] - lag(Mx["ls_global"], 24)
    F["D6_taker_ratio_6h"] = M(Mx["taker_ratio"], 6)
    F["D7_oi_to_volume"] = safe_div(Mx["oi_usd"], X["dv24"])
    F["F1_funding_level"] = X["f8"]
    flow = 2 * np.nan_to_num(X["tbq"]) - qv
    F["F2_taker_imbalance_6h"] = safe_div(S(flow, 6), S(qv, 6))
    qq = np.nan_to_num(X["qv_q"])
    F["F3_quarter_hour_flow_6h"] = safe_div(S(2 * np.nan_to_num(X["tbq_q"]) - qq, 6), S(qq, 6))
    # notices
    from dotenv import load_dotenv
    import os
    import psycopg
    load_dotenv(ROOT / ".env")
    con = psycopg.connect(os.environ["DATABASE_URL"])
    rows = con.execute("SELECT ts, symbols FROM exchange_notices WHERE kind='listing' AND source IN ('upbit','binance')").fetchall()
    N = np.zeros((len(ts), len(codes)), np.float32)
    for t, syms in rows:
        t = int(t) // 1000 if int(t) > 1e11 else int(t)
        for s_ in (syms or "").replace(";", ",").split(","):
            s_ = s_.strip().upper().replace("/USDT", "").replace("USDT", "").replace("KRW-", "")
            j, _ = upmap(s_) if s_ else (None, 1)
            if j is None:
                continue
            k0 = np.searchsorted(ts, t)                      # first hour close >= notice time
            N[k0:k0 + 24, j] = 1.0                            # visible for the next 24 hour-closes
    F["N1_listing_notice_prior_24h"] = N
    F["P1_ret_6h"] = lc - lag(lc, 6)
    F["P2_ret_24h"] = lc - lag(lc, 24)
    F["P3_volume_surge_6h"] = np.log(safe_div(S(qv, 6) + 1, M(qv, 168) * 6 + 1))
    F["P4_range_6h"] = np.log(safe_div(L.MX(X["h"], 6), L.MN(X["l"], 6)))
    for k, v in F.items():
        np.save(FD / f"{k}.npy", v.astype(np.float32))
    cov = {k: float(np.isfinite(v[X["U"]]).mean()) for k, v in F.items()}
    json.dump(cov, open(FD / "coverage.json", "w"), indent=1)
    print(json.dumps(cov, indent=1))


def load_feats(names):
    return {k: np.load(FD / f"{k}.npy", mmap_mode="r") for k in names}


# ------------------------------------------------------------------ events + controls
def events():
    ts, codes, X = L.data()
    c = X["c"]
    T, N = c.shape
    fwdmax = np.full((T, N), np.nan, np.float32)
    for k in range(1, 7):
        fwdmax = np.fmax(fwdmax, lag(c, -k))
    up = fwdmax / c - 1
    univ = (X["dv24"] >= 2e6) & (X["age"] >= 720) & np.isfinite(c)
    pump = univ & (up >= 0.20)
    ev = []
    for j in range(N):
        last = -10 ** 9
        for i in np.flatnonzero(pump[:, j]):
            if i - last >= 72:
                ev.append((i, j))
                last = i
    ev = pd.DataFrame(ev, columns=["i", "j"])
    ev["ts"] = ts[ev["i"]]
    near = np.zeros_like(pump)                                   # any pump within +-72h (for control exclusion)
    csum = np.cumsum(np.vstack([np.zeros((1, N)), pump]), 0)
    for i in range(T):
        a, b = max(0, i - 72), min(T, i + 73)
        near[i] = (csum[b] - csum[a]) > 0
    vol = L.VOL7()
    rng = np.random.default_rng(8)
    ctl = []
    for e in ev.itertuples():
        i = e.i
        ok = univ[i] & ~near[i] & np.isfinite(vol[i])
        ok[e.j] = False
        cand = np.flatnonzero(ok)
        if len(cand) < 5 or not np.isfinite(vol[i, e.j]):
            continue
        dq = np.quantile(X["dv24"][i][univ[i]], [1 / 3, 2 / 3])
        vq = np.quantile(vol[i][univ[i] & np.isfinite(vol[i])], [1 / 3, 2 / 3])
        tb = lambda v, q: np.digitize(v, q)
        same = cand[(tb(X["dv24"][i][cand], dq) == tb(X["dv24"][i, e.j], dq)) & (tb(vol[i][cand], vq) == tb(vol[i, e.j], vq))]
        if len(same) == 0:
            continue
        for jj in rng.choice(same, min(5, len(same)), replace=False):
            ctl.append((e.Index, i, jj))
    ctl = pd.DataFrame(ctl, columns=["ev", "i", "j"])
    return ev, ctl


def pair_auc(fv_case, fv_ctl, ev_of_ctl):
    """Matched AUC: per event, share of its controls below the case (ties 0.5); returns per-event scores."""
    df = pd.DataFrame({"ev": ev_of_ctl, "ctl": fv_ctl})
    df["case"] = fv_case[df["ev"].to_numpy()]
    df = df[np.isfinite(df["ctl"]) & np.isfinite(df["case"])]
    df["s"] = (df["case"] > df["ctl"]) + 0.5 * (df["case"] == df["ctl"])
    return df.groupby("ev")["s"].mean()


def boot_auc(s, day, reps=4000, rng=np.random.default_rng(9)):
    d = pd.DataFrame({"s": s.to_numpy(), "d": day})
    g = d.groupby("d")["s"].agg(["sum", "count"])
    su, cn = g["sum"].to_numpy(), g["count"].to_numpy()
    b = []
    for _ in range(reps):
        k = rng.integers(0, len(su), len(su))
        b.append(su[k].sum() / cn[k].sum())
    b = np.array(b)
    return np.percentile(b, [2.5, 97.5]).tolist(), float((b <= 0.5).mean())


def cc():
    OUT.mkdir(parents=True, exist_ok=True)
    ts, codes, X = L.data()
    ev, ctl = events()
    names = REG + PRICE
    F = load_feats(names)
    ev["disc"] = (ev["ts"] >= L.DISC[0]) & (ev["ts"] < L.DISC[1])
    ev["hold"] = (ev["ts"] >= L.HOLD[0]) & (ev["ts"] < L.HOLD[1])
    res = {"n_events": {"disc": int(ev["disc"].sum()), "hold": int(ev["hold"].sum())}, "features": {}}
    for k in names:
        f = F[k]
        case = np.array([f[i, j] for i, j in zip(ev["i"], ev["j"])], np.float64)
        cv = np.array([f[i, j] for i, j in zip(ctl["i"], ctl["j"])], np.float64)
        s = pair_auc(case, cv, ctl["ev"].to_numpy())
        out = {}
        for per in ("disc", "hold"):
            ss = s[s.index.isin(np.flatnonzero(ev[per]))]
            if len(ss) < 20:
                out[per] = dict(n=int(len(ss)))
                continue
            out[per] = dict(n=int(len(ss)), auc=float(ss.mean()))
        sign = 1 if out["disc"].get("auc", 0.5) >= 0.5 else -1
        ss = s[s.index.isin(np.flatnonzero(ev["hold"]))]
        if len(ss) >= 20:
            sa = ss if sign == 1 else 1 - ss
            ci, p = boot_auc(sa, ev.loc[sa.index, "ts"].to_numpy() // D_)
            out["hold"].update(auc_signed=float(sa.mean()), ci=ci, p=p)
        out["sign"] = sign
        res["features"][k] = out
        print(k, json.dumps(out), flush=True)
    # BH across registered features only
    reg = [(k, res["features"][k]["hold"].get("p", 1.0)) for k in REG]
    reg.sort(key=lambda x: x[1])
    m = len(reg)
    passed = set()
    for r, (k, p) in enumerate(reg, 1):
        if p <= 0.10 * r / m:
            passed = {kk for kk, _ in reg[:r]}
    for k in REG:
        h = res["features"][k]["hold"]
        res["features"][k]["pass"] = bool(k in passed and h.get("ci", [0])[0] > 0.5)
    res["passed"] = [k for k in REG if res["features"][k]["pass"]]
    # B8_2 lead-time profile for passing features
    prof = {}
    for k in res["passed"]:
        f, sign = F[k], res["features"][k]["sign"]
        hold_ev = np.flatnonzero(ev["hold"])
        row = {}
        for L_ in range(0, 25):
            case = np.array([f[i - L_, j] if i - L_ >= 0 else np.nan for i, j in zip(ev["i"], ev["j"])], np.float64)
            cv = np.array([f[i - L_, j] if i - L_ >= 0 else np.nan for i, j in zip(ctl["i"], ctl["j"])], np.float64)
            s = pair_auc(case, cv, ctl["ev"].to_numpy())
            s = s[s.index.isin(hold_ev)]
            row[L_] = float((s if sign == 1 else 1 - s).mean())
        prof[k] = row
    res["lead_profile"] = prof
    # joint conditional logit on passing features (holdout), if any
    if res["passed"]:
        try:
            from statsmodels.discrete.conditional_models import ConditionalLogit
            rows = []
            for e in np.flatnonzero(ev["hold"]):
                sub = ctl[ctl["ev"] == e]
                rows.append([e, 1] + [F[k][ev.at[e, "i"], ev.at[e, "j"]] for k in res["passed"]])
                for r in sub.itertuples():
                    rows.append([e, 0] + [F[k][r.i, r.j] for k in res["passed"]])
            D = pd.DataFrame(rows, columns=["g", "y"] + res["passed"]).dropna()
            Z = (D[res["passed"]] - D[res["passed"]].mean()) / D[res["passed"]].std()
            fit = ConditionalLogit(D["y"], Z, groups=D["g"]).fit(disp=0)
            res["joint_clogit"] = dict(coef=fit.params.round(3).to_dict(), p=fit.pvalues.round(4).to_dict(), n=int(len(D)))
        except Exception as ex:
            res["joint_clogit"] = str(ex)
    json.dump(res, open(OUT / "b8_cc.json", "w"), indent=1, default=float)
    print("PASSED", res["passed"])


# ------------------------------------------------------------------ B8_3 model
def model():
    import lightgbm as lgb
    from sklearn.isotonic import IsotonicRegression
    from sklearn.metrics import average_precision_score
    OUT.mkdir(parents=True, exist_ok=True)
    ts, codes, X = L.data()
    c = X["c"]
    T, N = c.shape
    fwdmax = np.full((T, N), np.nan, np.float32)
    for k in range(1, 7):
        fwdmax = np.fmax(fwdmax, lag(c, -k))
    y = (fwdmax / c - 1 >= 0.20)
    univ = (X["dv24"] >= 2e6) & (X["age"] >= 720) & np.isfinite(c)
    rows = np.flatnonzero(ts % (4 * H_) == 0)
    F = load_feats(REG + PRICE)
    extra = {"rv7": L.VOL7(), "ldv": np.log(X["dv24"] + 1), "ret7d": X["lc"] - lag(X["lc"], 168)}
    base_cols = PRICE + ["rv7", "ldv", "ret7d", "F1_funding_level", "F2_taker_imbalance_6h"]
    full_cols = base_cols + [k for k in REG if k not in base_cols]
    ri, cj = np.nonzero(univ[rows])
    I = rows[ri]
    df = pd.DataFrame({"i": I, "j": cj, "ts": ts[I], "y": y[I, cj].astype(int)})
    for k in full_cols:
        src = F[k] if k in F else extra[k]
        df[k] = np.asarray(src)[I, cj]
    g6 = c[np.minimum(I + 6, T - 1), cj] / c[I, cj] - 1
    g24 = c[np.minimum(I + 24, T - 1), cj] / c[I, cj] - 1
    df["g6"], df["g24"] = g6, g24
    df["cost"] = 2 * (L.FEE + L.slip(X["dv24"][I, cj]))
    fund = X["f8"][I, cj]
    df["f6"], df["f24"] = np.nan_to_num(fund) * 0.75, np.nan_to_num(fund) * 3
    tr = df[(df["ts"] >= L.DISC[0]) & (df["ts"] < L.DISC[1] - 60 * D_)]
    ca = df[(df["ts"] >= L.DISC[1] - 60 * D_) & (df["ts"] < L.DISC[1] - D_)]
    te = df[(df["ts"] >= L.HOLD[0]) & (df["ts"] < L.HOLD[1])].copy()
    res = {"base_rate_test": float(te["y"].mean()), "n_test": int(len(te))}
    for nm, cols in (("price_only", base_cols), ("with_b8", full_cols)):
        m = lgb.LGBMClassifier(n_estimators=600, learning_rate=0.02, num_leaves=31, min_child_samples=200, subsample=0.8,
                               subsample_freq=1, colsample_bytree=0.8, reg_lambda=5, verbose=-1, n_jobs=8)
        m.fit(tr[cols], tr["y"])
        iso = IsotonicRegression(out_of_bounds="clip").fit(m.predict_proba(ca[cols])[:, 1], ca["y"])
        te[f"p_{nm}"] = iso.predict(m.predict_proba(te[cols])[:, 1])
        te[f"raw_{nm}"] = m.predict_proba(te[cols])[:, 1]
        pr = average_precision_score(te["y"], te[f"raw_{nm}"])
        top = te[f"raw_{nm}"] >= te[f"raw_{nm}"].quantile(0.995)
        sel = te.sort_values(f"raw_{nm}", ascending=False).groupby("ts").head(5)
        out = dict(pr_auc=float(pr), precision_top_0p5pct=float(te.loc[top, "y"].mean()))
        for h in ("6", "24"):
            net = sel[f"g{h}"] - sel["cost"] - sel[f"f{h}"]
            dayn = net.groupby(sel["ts"] // D_).mean()
            out[f"top5_net_{h}h_per_trade"] = float(net.mean())
            out[f"top5_net_{h}h_ci"] = L.boot_ci(dayn.dropna().to_numpy())
        imp = pd.Series(m.booster_.feature_importance("gain"), index=cols).sort_values(ascending=False)
        out["top_gain"] = imp.head(8).round(1).to_dict()
        res[nm] = out
        print(nm, json.dumps(out, default=float), flush=True)
    # PR-AUC gain CI via day bootstrap
    days = te["ts"] // D_
    ud = days.unique()
    rng = np.random.default_rng(4)
    gains = []
    grp = {d: g for d, g in te.groupby(days)}
    for _ in range(300):
        s = pd.concat([grp[d] for d in rng.choice(ud, len(ud))])
        if s["y"].sum() == 0:
            continue
        gains.append(average_precision_score(s["y"], s["raw_with_b8"]) - average_precision_score(s["y"], s["raw_price_only"]))
    res["pr_auc_gain"] = float(np.mean(gains))
    res["pr_auc_gain_ci"] = np.percentile(gains, [2.5, 97.5]).tolist()
    b = res["with_b8"]
    res["pass"] = bool(res["pr_auc_gain_ci"][0] > 0 and (b["top5_net_6h_ci"][0] > 0 or b["top5_net_24h_ci"][0] > 0))
    json.dump(res, open(OUT / "b8_model.json", "w"), indent=1, default=float)
    print(json.dumps({k: v for k, v in res.items() if k not in ("price_only", "with_b8")}, default=float))


if __name__ == "__main__":
    {"features": features, "cc": cc, "model": model}[sys.argv[1]]()
