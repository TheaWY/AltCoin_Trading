"""B17 F12: tick-level ML on 1-second windows (sec1 = pump windows, sec1_null = matched no-pump windows). Registry F12_ml_onset (45 cells).
  OMP_NUM_THREADS=6 .venv/bin/python -W ignore scripts/b17_f12.py features            -> data/cache/b17/f12_{X5,X15,X60}.npy, f12_meta.parquet
  OMP_NUM_THREADS=6 .venv/bin/python -W ignore scripts/b17_f12.py tab  lgbm|hawkes    (no torch)
  OMP_NUM_THREADS=6 .venv/bin/python -W ignore scripts/b17_f12.py seq  tcn|gru|transformer   (torch process)
  .venv/bin/python scripts/b17_f12.py report                                          -> data/reports/b17/f12.json
Decision points: every window gives decision points at t = 5, 15, 60 minutes after the window start (rows 300+k*60 ... ), i.e. the model
sees the FIRST 5/15/60 minutes of 1-second bars after the 5-minute baseline and must predict, at that point:
  T1  +5% within the next 15 min      T2  the window's running high is reached within the next 5 min (peak-within-5m)      T3  drawdown > 10% from the current
      price within the next 6 h (minute paths for pump windows; capped at the 1s window for null windows -> T3 is evaluated on pump windows only, noted)
Both pump and null windows are used, so the label base rate is the sample's, not the market's; the trade is base-rate weighted like F02 part B.
Sequence input (seq models): 1-second bars aggregated to 5-second bars over the lookback (60/180/720 steps) x 6 channels
(log return, log volume ratio to baseline, taker share, trades/sec ratio, max-trade share, range). Tabular input: 40 summary features
of the same lookback (+ Hawkes dispersion estimates for the 'hawkes' model). Purged quarterly walk-forward by window start time;
validation = 2026. Trade: long at the decision point when P(T1) > thr (thr = top 10% of training-fold scores), exit +15m or +1h;
net_real = p * net_pump + (1-p) * net_null with p = P(pump | flagged) from the fire rates at BASE_RATE 0.5%."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b15_models as M  # noqa: E402

B = ROOT / "data/cache/b17"
OUT = ROOT / "data/reports/b17"
RNG = np.random.default_rng(1712)
VAL0 = 1767225600
R0 = 300
LOOK = {"5m": 300, "15m": 900, "60m": 3600}
BASE_RATE = 0.005


def channels(d):
    c = pd.Series(d["c"].to_numpy(float)).ffill().bfill().to_numpy(); h = np.where(np.isfinite(d["h"]), d["h"], c); l = np.where(np.isfinite(d["l"]), d["l"], c)
    qv, n, tb, mx = (d[k].to_numpy(float) for k in ("qv", "n", "tbqv", "maxq"))
    k = 5; T = len(c) // k
    cb = c[:T * k].reshape(T, k)[:, -1]; hb = h[:T * k].reshape(T, k).max(1); lb = l[:T * k].reshape(T, k).min(1)
    qb = qv[:T * k].reshape(T, k).sum(1); nb = n[:T * k].reshape(T, k).sum(1); tbb = tb[:T * k].reshape(T, k).sum(1); mxb = mx[:T * k].reshape(T, k).max(1)
    bq, bn = max(qb[:R0 // k].mean(), 1e-9), max(nb[:R0 // k].mean(), 1e-9)
    X = np.stack([np.diff(np.log(np.where(cb > 0, cb, np.nan)), prepend=np.nan), np.log1p(qb / bq), tbb / np.where(qb > 0, qb, np.nan) - 0.5,
                  np.log1p(nb / bn), mxb / np.where(qb > 0, qb, np.nan), hb / np.where(lb > 0, lb, np.nan) - 1], 1).astype(np.float32)
    return np.nan_to_num(X), c, h, l


def tab_feats(Xs):
    """40 summary features of a (steps, 6) block."""
    f = []
    for j in range(6):
        x = Xs[:, j]; f += [x.mean(), x.std(), x[-12:].mean(), x[-12:].max(), x.max(), x.min()]
    r = Xs[:, 0]; cum = np.cumsum(r)
    f += [cum[-1], cum.max(), cum[-1] - cum.max(), (r > 0).mean()]
    return np.array(f, np.float32)


def hawkes_feats(Xs):
    n = np.expm1(Xs[:, 3])
    out = []
    for w in (12, 36, 120):
        x = n[-w:]; m, v = x.mean(), x.var()
        out.append(1 - 1 / np.sqrt(max(v / max(m, 1e-9), 1.0)))
    return np.array(out, np.float32)


def features():
    S = pd.concat([pd.read_parquet(B / "sample.parquet").assign(is_pump=1), pd.read_parquet(B / "sample_null.parquet").assign(is_pump=0)])
    paths = np.load(M.C / "b15/paths.npy", mmap_mode="r")
    meta, seq, tab, hk = [], {k: [] for k in LOOK}, {k: [] for k in LOOK}, {k: [] for k in LOOK}
    for pid, code, ts, is_pump, dv in zip(S["pump_id"], S["code"], S["ts"], S["is_pump"], S["dv24"]):
        fp = B / (f"sec1/{pid}.parquet" if is_pump else f"sec1_null/{-pid}.parquet")
        if not fp.exists():
            continue
        d = pd.read_parquet(fp)
        if len(d) != 7500:
            continue
        X, c, h, l = channels(d)
        for lk, secs in LOOK.items():
            t = R0 + secs                                                    # decision second
            s = t // 5
            block = X[max(0, s - secs // 5):s]
            if len(block) < secs // 5:
                block = np.vstack([np.zeros((secs // 5 - len(block), 6), np.float32), block])
            e = c[t]
            fut_h = h[t + 1:t + 901]; y1 = int(np.nanmax(fut_h) >= e * 1.05) if len(fut_h) else 0
            hi_all = np.nanmax(h[t + 1:]); y2 = int(np.nanmax(h[t + 1:t + 301]) >= hi_all * 0.999) if t + 301 < 7500 else 0
            if is_pump:
                mret = np.asarray(paths[pid][60:60 + 360, 0], np.float64); px6h = c[3959] * (1 + mret)
                lo6 = min(np.nanmin(l[t + 1:]), np.nanmin(px6h)); y3 = int(lo6 <= e * 0.90)
            else:
                y3 = -1
            g15 = c[min(t + 900, 7499)] / e - 1; g60 = c[min(t + 3600, 7499)] / e - 1
            mae15 = 1 - np.nanmin(l[t + 1:t + 901]) / e
            seq[lk].append(block); tab[lk].append(tab_feats(block)); hk[lk].append(hawkes_feats(block))
            meta.append(dict(pump_id=int(pid), code=code, ts=int(ts), is_pump=int(is_pump), look=lk, y1=y1, y2=y2, y3=y3, g15=g15, g60=g60, mae15=mae15,
                             cost=2 * (M.L.FEE + float(M.slip(np.array([dv]))[0])), day=int(ts // 86400), val=int(ts >= VAL0)))
    for lk in LOOK:
        np.save(B / f"f12_seq_{lk}.npy", np.stack(seq[lk])); np.save(B / f"f12_tab_{lk}.npy", np.stack(tab[lk])); np.save(B / f"f12_hk_{lk}.npy", np.stack(hk[lk]))
    pd.DataFrame(meta).to_parquet(B / "f12_meta.parquet", index=False)
    print("windows", len(meta) // 3, "labels", pd.DataFrame(meta).groupby("look")[["y1", "y2"]].mean().round(3).to_dict())


def folds(meta):
    ts = meta["ts"].to_numpy(); q = np.arange(ts.min(), ts.max() + 1, 91 * 86400)
    for q0, q1 in zip(q[1:], list(q[2:]) + [ts.max() + 1]):
        tr = np.flatnonzero(ts < q0 - 2 * 86400); te = np.flatnonzero((ts >= q0) & (ts < q1))
        if len(te) and len(tr) > 200:
            yield tr, te


def fit_tab(model):
    import lightgbm as lgb
    meta = pd.read_parquet(B / "f12_meta.parquet")
    for lk in LOOK:
        m = meta[meta["look"] == lk].reset_index(drop=True)
        X = np.load(B / f"f12_tab_{lk}.npy")
        if model == "hawkes":
            X = np.hstack([X, np.load(B / f"f12_hk_{lk}.npy")])
        for tgt in ("y1", "y2", "y3"):
            y = m[tgt].to_numpy(); ok = y >= 0
            P = np.full(len(m), np.nan, np.float32); thr = np.full(len(m), np.nan, np.float32)
            for tr, te in folds(m):
                tr = tr[ok[tr]]
                if y[tr].sum() < 30:
                    continue
                clf = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.03, num_leaves=31, min_child_samples=50, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                                         reg_lambda=5, verbose=-1, n_jobs=6).fit(X[tr], y[tr])
                thr[te] = np.quantile(clf.predict_proba(X[tr])[:, 1], 0.90); P[te] = clf.predict_proba(X[te])[:, 1]
            np.save(B / f"f12_p_{model}_{lk}_{tgt}.npy", np.stack([P, thr], 1))
            print(model, lk, tgt, "done", flush=True)


def fit_seq(model):
    import torch
    import torch.nn as nn
    torch.manual_seed(1712); dev = torch.device("mps" if torch.backends.mps.is_available() else "cpu")

    class GRU(nn.Module):
        def __init__(s): super().__init__(); s.g = nn.GRU(6, 48, batch_first=True); s.o = nn.Linear(48, 1)
        def forward(s, x): return s.o(s.g(x)[0][:, -1]).squeeze(-1)

    class TCN(nn.Module):
        def __init__(s):
            super().__init__(); L, ch = [], 6
            for d in (1, 2, 4, 8, 16): L += [nn.Conv1d(ch, 48, 3, padding=d, dilation=d), nn.GELU()]; ch = 48
            s.net = nn.Sequential(*L); s.o = nn.Linear(48, 1)
        def forward(s, x): return s.o(s.net(x.transpose(1, 2))[:, :, -1]).squeeze(-1)

    class Trans(nn.Module):
        def __init__(s, T):
            super().__init__(); s.inp = nn.Linear(6, 48); s.pos = nn.Parameter(torch.randn(1, T, 48) * 0.02)
            s.enc = nn.TransformerEncoder(nn.TransformerEncoderLayer(48, 4, 96, 0.1, batch_first=True), 2); s.o = nn.Linear(48, 1)
        def forward(s, x): return s.o(s.enc(s.inp(x) + s.pos)[:, -1]).squeeze(-1)

    meta = pd.read_parquet(B / "f12_meta.parquet")
    for lk in LOOK:
        m = meta[meta["look"] == lk].reset_index(drop=True)
        X = np.load(B / f"f12_seq_{lk}.npy"); X = np.clip(X / (X.reshape(-1, 6).std(0) + 1e-6), -8, 8).astype(np.float32)
        if lk == "60m":
            X = X[:, ::4]        # 720 -> 180 steps for the sequence models
        Xt = torch.tensor(X)
        for tgt in ("y1", "y2", "y3"):
            y = m[tgt].to_numpy(); ok = y >= 0
            P = np.full(len(m), np.nan, np.float32); thr = np.full(len(m), np.nan, np.float32)
            for tr, te in folds(m):
                tr = tr[ok[tr]]
                if y[tr].sum() < 30:
                    continue
                net = {"gru": GRU, "tcn": TCN, "transformer": lambda: Trans(X.shape[1])}[model]().to(dev)
                opt = torch.optim.AdamW(net.parameters(), 1e-3, weight_decay=1e-4); pw = torch.tensor(float((1 - y[tr].mean()) / max(y[tr].mean(), 1e-3)), device=dev)
                lossf = nn.BCEWithLogitsLoss(pos_weight=pw.clamp(max=20)); yt = torch.tensor(y[tr].astype(np.float32))
                cal = tr[-max(50, len(tr) // 10):]; trn = tr[:-max(50, len(tr) // 10)]
                best, state, bad = 1e9, None, 0
                for ep in range(30):
                    net.train()
                    for b in np.array_split(RNG.permutation(len(trn)), max(1, len(trn) // 256)):
                        opt.zero_grad(); l_ = lossf(net(Xt[trn[b]].to(dev)), yt[np.searchsorted(tr, trn[b])].to(dev)); l_.backward(); opt.step()
                    net.eval()
                    with torch.no_grad():
                        vl = float(lossf(net(Xt[cal].to(dev)), yt[np.searchsorted(tr, cal)].to(dev)))
                    if vl < best - 1e-4: best, state, bad = vl, {k: v.clone() for k, v in net.state_dict().items()}, 0
                    else:
                        bad += 1
                        if bad >= 4: break
                net.load_state_dict(state); net.eval()
                with torch.no_grad():
                    ptr = torch.sigmoid(net(Xt[tr].to(dev))).cpu().numpy(); P[te] = torch.sigmoid(net(Xt[te].to(dev))).cpu().numpy()
                thr[te] = np.quantile(ptr, 0.90)
            np.save(B / f"f12_p_{model}_{lk}_{tgt}.npy", np.stack([P, thr], 1))
            print(model, lk, tgt, "done", flush=True)


def report():
    from sklearn.metrics import average_precision_score, roc_auc_score
    meta = pd.read_parquet(B / "f12_meta.parquet")
    res = {"tests": {}}; pv = {}
    for fp in sorted(B.glob("f12_p_*.npy")):
        model, lk, tgt = fp.stem.replace("f12_p_", "").rsplit("_", 2)
        m = meta[meta["look"] == lk].reset_index(drop=True); PT = np.load(fp); P, thr = PT[:, 0], PT[:, 1]
        v = (m["val"] == 1) & np.isfinite(P) & (m[tgt] >= 0)
        if v.sum() < 50 or m.loc[v, tgt].nunique() < 2:
            continue
        auc = roc_auc_score(m.loc[v, tgt], P[v]); ap = average_precision_score(m.loc[v, tgt], P[v])
        flag = v & (P > thr)
        out = dict(auc=float(auc), pr_auc=float(ap), base_rate=float(m.loc[v, tgt].mean()), n_flag=int(flag.sum()),
                   fire_pump=float((flag & (m["is_pump"] == 1)).sum() / max(1, (v & (m["is_pump"] == 1)).sum())),
                   fire_null=float((flag & (m["is_pump"] == 0)).sum() / max(1, (v & (m["is_pump"] == 0)).sum())))
        for hz in ("g15", "g60"):
            gp = m[flag & (m["is_pump"] == 1)]; gn = m[flag & (m["is_pump"] == 0)]
            if len(gp) < 20 or len(gn) < 20:
                continue
            p = BASE_RATE * out["fire_pump"] / (BASE_RATE * out["fire_pump"] + (1 - BASE_RATE) * out["fire_null"]) if out["fire_null"] > 0 else 1.0
            np_, nn_ = (gp[hz] - gp["cost"]).to_numpy(), (gn[hz] - gn["cost"]).to_numpy()
            bo = np.array([p * np.mean(RNG.choice(np_, len(np_))) + (1 - p) * np.mean(RNG.choice(nn_, len(nn_))) for _ in range(2000)])
            out[f"trade_{hz}"] = dict(p_pump_given_flag=float(p), net_pump=float(np_.mean()), net_null=float(nn_.mean()), net_real=float(p * np_.mean() + (1 - p) * nn_.mean()),
                                      ci=[float(x) for x in np.percentile(bo, [2.5, 97.5])], p_value=float((bo <= 0).mean()), mae15_p90_null=float(gn["mae15"].quantile(0.9)))
            pv[f"{model}|{lk}|{tgt}|{hz}"] = out[f"trade_{hz}"]["p_value"]
        res["tests"][f"{model}|{lk}|{tgt}"] = out
    names = sorted(pv, key=pv.get)
    last = max([k for k, nm in enumerate(names) if pv[nm] <= 0.10 * (k + 1) / len(names)], default=-1)
    res["bh_pass"] = [nm for k, nm in enumerate(names) if k <= last]
    res["pass"] = [nm for nm in res["bh_pass"] if res["tests"][nm.rsplit("|", 1)[0]][f"trade_{nm.rsplit('|', 1)[1]}"]["ci"][0] > 0]
    OUT.mkdir(parents=True, exist_ok=True); json.dump(res, open(OUT / "f12.json", "w"), indent=1, default=float)
    print("F12 PASS", res["pass"])
    for k, v in sorted(res["tests"].items(), key=lambda kv: -kv[1]["auc"])[:15]:
        t = v.get("trade_g15", {}); print(k, "auc", round(v["auc"], 3), "pr", round(v["pr_auc"], 3), "base", round(v["base_rate"], 3), "fire p/n", round(v["fire_pump"], 3), round(v["fire_null"], 3),
                                          "real15", round(t.get("net_real", np.nan), 4), t.get("ci"))


if __name__ == "__main__":
    {"features": features, "tab": lambda: fit_tab(sys.argv[2]), "seq": lambda: fit_seq(sys.argv[2]), "report": report}[sys.argv[1]]()
