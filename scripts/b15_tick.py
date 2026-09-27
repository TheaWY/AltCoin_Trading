"""B15_3 tick-level onset fingerprint (research/batch_B15_B16.yaml).
  .venv/bin/python -W ignore scripts/b15_tick.py fetch   # resumable; downloads each aggTrades day zip, extracts features, deletes the zip
  .venv/bin/python -W ignore scripts/b15_tick.py test
Sample: 600 discovery + 600 holdout pumps, 200 per size bucket per period (seed 153), as registered.
Windows (ts = open time of onset minute m0): onset window [m0-5m, m0+2m); baseline [m0-65m, m0-5m).
Features: big_ratio = largest trade (quote) in window / median baseline trade; buy_share = taker-buy quote share in window;
  gini = trade-size Gini in window; tps_onset = trades/sec in minute m0; tps_ratio = tps_onset / baseline trades/sec;
  sec_imb = (buy-sell)/(buy+sell) quote in the busiest second of the window.
  concentration = mean percentile rank (discovery-fitted) of big_ratio and gini.
Tests (holdout once):
  T1 concentration terciles (cutoffs from discovery): net of long-at-m0+1 24h and of short, top minus bottom tercile, day-clustered CI.
  T2 logistic model P(24h long net < 0) on base pump features vs base + tick features, fit on discovery sample, holdout AUC gain
     with day-clustered bootstrap CI. Pass: T2 gain CI > 0 OR a T1 spread (long or short) with net CI > 0 (BH q=0.10 over 3 tests).
Disk: never more than 4 zips on disk at once (< 5 GB)."""
from __future__ import annotations

import io
import json
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b15_models as M  # noqa: E402

TMP = ROOT / "data/tmp_aggtrades"
FEAT = ROOT / "data/cache/b15/tick_features.parquet"
URL = "https://data.binance.vision/data/futures/um/daily/aggTrades/{s}/{s}-aggTrades-{d}.zip"
RNG = np.random.default_rng(153)
TICK = ["big_ratio", "buy_share", "gini", "tps_onset", "tps_ratio", "sec_imb"]


def sample():
    P = M.pump_context()
    out = []
    for per in (False, True):
        for b in ("10-15", "15-25", "25+"):
            g = P[(P["hold"] == per) & (P["size_bucket"] == b)]
            out.append(g.sample(min(200, len(g)), random_state=153))
    return pd.concat(out)


def _day(t):
    return time.strftime("%Y-%m-%d", time.gmtime(int(t)))


def _load_day(code, day, ranges):
    """Download one day zip, keep trades inside any (lo_ms, hi_ms) range, delete the zip."""
    TMP.mkdir(parents=True, exist_ok=True)
    fp = TMP / f"{code}-{day}.zip"
    for a in range(4):
        try:
            r = requests.get(URL.format(s=code, d=day), timeout=120, stream=True)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            with open(fp, "wb") as f:
                for ch in r.iter_content(1 << 20):
                    f.write(ch)
            break
        except Exception:
            time.sleep(5 * (a + 1))
    else:
        return None
    keep = []
    try:
        with zipfile.ZipFile(fp) as z, z.open(z.namelist()[0]) as fh:
            first = fh.readline().decode()
            hdr = first.startswith("agg_trade_id")
            fh2 = z.open(z.namelist()[0])
            it = pd.read_csv(fh2, header=None, skiprows=1 if hdr else 0, usecols=[1, 2, 5, 6], names=["id", "p", "q", "f", "l", "t", "m"],
                             chunksize=2_000_000)
            for ch in it:
                t = ch["t"].to_numpy()
                mask = np.zeros(len(ch), bool)
                for lo, hi in ranges:
                    mask |= (t >= lo) & (t < hi)
                if mask.any():
                    keep.append(ch[mask])
    finally:
        fp.unlink(missing_ok=True)
    return pd.concat(keep) if keep else pd.DataFrame(columns=["p", "q", "t", "m"])


def _gini(x):
    x = np.sort(x)
    n = len(x)
    return float((2 * np.arange(1, n + 1) - n - 1).dot(x) / (n * x.sum())) if n > 1 and x.sum() > 0 else np.nan


def _features(tr, ts):
    ms = ts * 1000
    tr = tr.assign(v=tr["p"].astype(float) * tr["q"].astype(float), buy=~tr["m"].astype(str).str.lower().eq("true"))
    base = tr[(tr["t"] >= ms - 3_900_000) & (tr["t"] < ms - 300_000)]
    win = tr[(tr["t"] >= ms - 300_000) & (tr["t"] < ms + 180_000)]
    on = tr[(tr["t"] >= ms) & (tr["t"] < ms + 60_000)]
    if len(win) < 5 or len(base) < 5:
        return None
    sec = win.assign(s=win["t"] // 1000, sv=np.where(win["buy"], win["v"], -win["v"])).groupby("s").agg(n=("v", "size"), sv=("sv", "sum"), v=("v", "sum"))
    top = sec.loc[sec["n"].idxmax()]
    base_tps = len(base) / 3600
    return dict(big_ratio=float(win["v"].max() / base["v"].median()), buy_share=float(win.loc[win["buy"], "v"].sum() / win["v"].sum()),
                gini=_gini(win["v"].to_numpy()), tps_onset=len(on) / 60, tps_ratio=(len(on) / 60) / max(base_tps, 1 / 3600),
                sec_imb=float(top["sv"] / top["v"]), n_win=len(win))


def _one_code(code, g):
    ranges = [((t - 3900) * 1000, (t + 180) * 1000) for t in g["ts"]]
    days = sorted({_day(t - 3900) for t in g["ts"]} | {_day(t + 180) for t in g["ts"]})
    parts = [x for x in (_load_day(code, d, ranges) for d in days) if x is not None and len(x)]
    if not parts:
        return []
    tr = pd.concat(parts)
    out = []
    for pid, t in zip(g["pump_id"], g["ts"]):
        f = _features(tr, int(t))
        if f:
            out.append(dict(pump_id=int(pid), **f))
    return out


def fetch():
    S = sample()
    done = pd.read_parquet(FEAT) if FEAT.exists() else pd.DataFrame(columns=["pump_id"])
    todo = S[~S["pump_id"].isin(done["pump_id"])]
    rows = done.to_dict("records")
    groups = list(todo.groupby("code"))
    print("pumps", len(S), "todo", len(todo), "codes", len(groups), flush=True)
    with ThreadPoolExecutor(4) as ex:
        for i, res in enumerate(ex.map(lambda cg: _one_code(*cg), groups)):
            rows += res
            if i % 10 == 0:
                pd.DataFrame(rows).to_parquet(FEAT); print(i, len(rows), flush=True)
    pd.DataFrame(rows).to_parquet(FEAT)
    print("done", len(rows))


def _auc_gain_ci(y, p0, p1, days, reps=2000):
    from sklearn.metrics import roc_auc_score
    d = pd.DataFrame({"y": y, "a": p0, "b": p1, "d": days})
    ud = d["d"].unique()
    idx = {k: np.where(d["d"].to_numpy() == k)[0] for k in ud}
    g = []
    for _ in range(reps):
        ii = np.concatenate([idx[k] for k in RNG.choice(ud, len(ud))])
        s = d.iloc[ii]
        if s["y"].nunique() == 2:
            g.append(roc_auc_score(s["y"], s["b"]) - roc_auc_score(s["y"], s["a"]))
    g = np.array(g)
    return [float(v) for v in np.percentile(g, [2.5, 97.5])], float(2 * min((g <= 0).mean(), (g >= 0).mean()))


def test():
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    P = sample().merge(pd.read_parquet(FEAT), on="pump_id")
    P["lbig"] = np.log(P["big_ratio"].clip(1e-3)); P["ltps"] = np.log1p(P["tps_ratio"])
    P["long_net"] = P["ret24_from_entry"] - P["cost"]; P["short_net"] = -P["ret24_from_entry"] - P["cost"]
    D, H = P[~P["hold"]].copy(), P[P["hold"]].copy()
    ref = {c: np.sort(D[c].dropna().to_numpy()) for c in ("big_ratio", "gini")}
    for X in (D, H):
        X["conc"] = np.mean([np.searchsorted(ref[c], X[c].to_numpy()) / len(ref[c]) for c in ref], 0)
    q1, q2 = np.quantile(D["conc"], [1 / 3, 2 / 3])
    res = {"n_disc": int(len(D)), "n_hold": int(len(H)), "feature_means_hold": H[TICK].median().to_dict()}
    pv = {}
    for per, X in (("discovery", D), ("holdout", H)):
        top, bot = X[X["conc"] >= q2], X[X["conc"] < q1]
        r = {}
        for leg in ("long_net", "short_net"):
            spread = np.concatenate([top[leg].to_numpy(), -bot[leg].to_numpy()])
            days = np.concatenate([top["day"].to_numpy(), bot["day"].to_numpy()])
            # spread = long top-tercile book minus bottom-tercile book, equal-weight per trade
            ci = M.day_ci(spread, days)
            r[leg] = dict(top=float(top[leg].mean()), bottom=float(bot[leg].mean()), top_ci=M.day_ci(top[leg].to_numpy(), top["day"].to_numpy()),
                          bottom_ci=M.day_ci(bot[leg].to_numpy(), bot["day"].to_numpy()), spread_ci=ci)
            if per == "holdout":
                b = np.array([np.mean(np.r_[top[leg].sample(frac=1, replace=True).to_numpy(), -bot[leg].sample(frac=1, replace=True).to_numpy()]) for _ in range(2000)])
                pv[f"T1_{leg}"] = float(2 * min((b <= 0).mean(), (b >= 0).mean()))
        r["dd6h_top_vs_bottom"] = [float(top["dd_6h"].mean()), float(bot["dd_6h"].mean())]
        res[f"T1_{per}"] = r
    base = ["pump_size", "gain_5m", "vol_ratio_0_5", "taker_0_5", "dv24"]
    D["ldv"], H["ldv"] = np.log(D["dv24"]), np.log(H["dv24"])
    base = ["pump_size", "gain_5m", "vol_ratio_0_5", "taker_0_5", "ldv"]
    full = base + ["lbig", "buy_share", "gini", "ltps", "sec_imb"]
    y_d, y_h = (D["long_net"] < 0).astype(int), (H["long_net"] < 0).astype(int)
    mk = lambda: make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=2000))
    m0 = mk().fit(D[base].fillna(0), y_d); m1 = mk().fit(D[full].fillna(0), y_d)
    p0, p1 = m0.predict_proba(H[base].fillna(0))[:, 1], m1.predict_proba(H[full].fillna(0))[:, 1]
    ci, p = _auc_gain_ci(y_h.to_numpy(), p0, p1, H["day"].to_numpy())
    res["T2"] = dict(auc_base=float(roc_auc_score(y_h, p0)), auc_full=float(roc_auc_score(y_h, p1)), gain_ci=ci, p=p,
                     coefs=dict(zip(full, m1[-1].coef_[0].round(3).tolist())))
    pv["T2"] = p
    names = sorted(pv, key=pv.get)
    last = max([r for r, nm in enumerate(names) if pv[nm] <= 0.10 * (r + 1) / len(names)], default=-1)
    res["bh_pass"] = {nm: r <= last for r, nm in enumerate(names)}
    res["pass"] = bool((ci[0] > 0 and res["bh_pass"]["T2"]) or any(res["T1_holdout"][l]["spread_ci"][0] > 0 and res["bh_pass"][f"T1_{l}"] for l in ("long_net", "short_net")))
    json.dump(res, open(M.OUT / "b15_3.json", "w"), indent=1, default=float)
    print(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    {"fetch": fetch, "test": test}[sys.argv[1]]()
