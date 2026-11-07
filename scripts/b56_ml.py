"""B56: prereg v7 (research/prereg_v7_ml.md) - ML/DL weekly return prediction on Upbit daily data, long only.
Models: Ridge, LightGBM, MLP, LSTM, rank-ensemble. Purged expanding walk-forward, quarterly retrain.
Output research/b56_ml.md, data/upbit_db/b56_preds.parquet, data/upbit_db/b56_results.parquet. Paper research only."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import b51_prereg_v2 as B  # noqa: E402
import b54_sweep100 as S  # noqa: E402

IDX, CF, O, V = S.IDX, S.CF, S.O, S.V
MAJ = ["KRW-BTC", "KRW-ETH"]
UNIV = S.U30.copy()
for m in MAJ:
    UNIV[m] = CF[m].notna().cumsum() >= 180
SUNDAYS = IDX[(IDX.weekday == 6) & (IDX >= pd.Timestamp("2018-03-01"))]
OOS_START = pd.Timestamp("2019-01-06")
SEQ = 60


def features():
    lc = np.log(CF); lr = lc.diff()
    F = {}
    for n in (1, 3, 7, 14, 28, 56, 90, 180):
        F[f"r{n}"] = lc - lc.shift(n)
    for n in (7, 30, 90):
        F[f"rv{n}"] = lr.rolling(n, min_periods=max(n // 2, 5)).std()
    F["rv7_90"] = F["rv7"] / F["rv90"]; F["rv30_90"] = F["rv30"] / F["rv90"]
    for n in (7, 30):
        F[f"max{n}"] = lr.rolling(n, min_periods=n // 2).max(); F[f"min{n}"] = lr.rolling(n, min_periods=n // 2).min()
    for n in (20, 50, 200):
        F[f"dsma{n}"] = CF / CF.rolling(n, min_periods=n // 2).mean() - 1
    F["dhi365"] = CF / CF.rolling(365, min_periods=120).max() - 1
    F["dlo365"] = CF / CF.rolling(365, min_periods=120).min() - 1
    medv = V.rolling(30, min_periods=20).median()
    F["lmedv"] = np.log(medv.replace(0, np.nan))
    F["v7_90"] = V.rolling(7, min_periods=5).median() / V.rolling(90, min_periods=45).median()
    F["trendw"] = pd.DataFrame({m: S.trendw(CF[m]) for m in CF.columns})
    btc = lc["KRW-BTC"]; aidx = np.log(S.alt_index())
    mkt = {"btc_r7": btc - btc.shift(7), "btc_r28": btc - btc.shift(28), "btc_tw": S.trendw(CF["KRW-BTC"]),
           "alt_r7": aidx - aidx.shift(7), "alt_rv20": aidx.diff().rolling(20, min_periods=15).std(),
           "breadth": ((CF > CF.rolling(50, min_periods=50).mean()) & UNIV).sum(axis=1) / UNIV.sum(axis=1).replace(0, np.nan)}
    return F, mkt


RANKED = ["r7", "r28", "r90", "rv30", "v7_90", "dhi365", "trendw"]


def dataset():
    F, mkt = features()
    cols = list(CF.columns); pos = {d: i for i, d in enumerate(IDX)}
    On = O.to_numpy(); lr = np.log(CF).diff().to_numpy(); lv = np.log(V.replace(0, np.nan)).diff().to_numpy()
    blr = lr[:, cols.index("KRW-BTC")]
    Fn = {k: v.reindex(index=IDX, columns=cols).to_numpy() for k, v in F.items()}
    Mn = {k: v.reindex(IDX).to_numpy() for k, v in mkt.items()}
    Un = UNIV.reindex(index=IDX, columns=cols).fillna(False).to_numpy()
    rows, seqs = [], []
    for d in SUNDAYS:
        i = pos[d]
        js = np.flatnonzero(Un[i])
        if len(js) < 10:
            continue
        y = np.full(len(js), np.nan)
        if i + 8 < len(IDX):
            y = np.log(On[i + 8, js] / On[i + 1, js])
        feat = {k: Fn[k][i, js] for k in Fn}
        for k in RANKED:
            feat[f"rk_{k}"] = pd.Series(feat[k]).rank(pct=True).to_numpy()
        for k in Mn:
            feat[k] = np.repeat(Mn[k][i], len(js))
        df = pd.DataFrame(feat); df["day"] = d; df["coin"] = [cols[j] for j in js]; df["y_raw"] = y
        df["y"] = y - np.nanmean(y) if np.isfinite(y).any() else np.nan
        rows.append(df)
        for j in js:
            w = np.stack([lr[i - SEQ + 1:i + 1, j], lv[i - SEQ + 1:i + 1, j], blr[i - SEQ + 1:i + 1]], axis=1)
            w = np.nan_to_num((w - np.nanmean(w, 0)) / (np.nanstd(w, 0) + 1e-9))
            seqs.append(w.astype(np.float32))
    D = pd.concat(rows, ignore_index=True)
    return D, np.stack(seqs)


def prep(Xtr, Xte):
    med = np.nanmedian(Xtr, 0); Xtr = np.where(np.isnan(Xtr), med, Xtr); Xte = np.where(np.isnan(Xte), med, Xte)
    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9
    return np.clip((Xtr - mu) / sd, -5, 5), np.clip((Xte - mu) / sd, -5, 5)


def fit_ridge(Xtr, ytr, Xte):
    from sklearn.linear_model import Ridge
    return Ridge(alpha=10.0).fit(Xtr, ytr).predict(Xte)


def fit_lgb(Xtr, ytr, Xte):
    import lightgbm as lgb
    m = lgb.LGBMRegressor(n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=50, subsample=0.8,
                          subsample_freq=1, colsample_bytree=0.8, verbose=-1, random_state=1)
    return m.fit(Xtr, ytr).predict(Xte)


def _torch_fit(make, Xtr, ytr, Xte, epochs, seeds=(1, 2, 3)):
    import torch
    torch.set_num_threads(8)
    preds = []
    for sd in seeds:
        torch.manual_seed(sd); np.random.seed(sd)
        net = make(); opt = torch.optim.Adam(net.parameters(), lr=1e-3, weight_decay=1e-4)
        X = torch.tensor(Xtr, dtype=torch.float32); y = torch.tensor(ytr, dtype=torch.float32)
        n = len(X)
        for _ in range(epochs):
            net.train(); perm = torch.randperm(n)
            for k in range(0, n, 256):
                b = perm[k:k + 256]
                opt.zero_grad(); loss = torch.nn.functional.mse_loss(net(X[b]).squeeze(-1), y[b]); loss.backward(); opt.step()
        net.eval()
        with torch.no_grad():
            preds.append(net(torch.tensor(Xte, dtype=torch.float32)).squeeze(-1).numpy())
    return np.mean(preds, 0)


def fit_mlp(Xtr, ytr, Xte):
    import torch.nn as nn
    k = Xtr.shape[1]
    return _torch_fit(lambda: nn.Sequential(nn.Linear(k, 64), nn.ReLU(), nn.Dropout(0.2), nn.Linear(64, 32), nn.ReLU(),
                                            nn.Dropout(0.2), nn.Linear(32, 1)), Xtr, ytr, Xte, 30)


def fit_lstm(Str, ytr, Ste):
    import torch.nn as nn

    class Net(nn.Module):
        def __init__(self):
            super().__init__(); self.rnn = nn.LSTM(3, 32, batch_first=True); self.head = nn.Linear(32, 1)

        def forward(self, x):
            o, _ = self.rnn(x)
            return self.head(o[:, -1])
    return _torch_fit(Net, Str, ytr, Ste, 15)


MODELS = {"M1_ridge": fit_ridge, "M2_lgbm": fit_lgb, "M3_mlp": fit_mlp, "M4_lstm": fit_lstm}


def walk_forward(D, SQ, only=None):
    fcols = [c for c in D.columns if c not in ("day", "coin", "y", "y_raw")]
    X = D[fcols].to_numpy(float); y = D["y"].to_numpy(float); days = D["day"].to_numpy()
    use = {m: f for m, f in MODELS.items() if only is None or m == only}
    preds = {m: np.full(len(D), np.nan) for m in use}
    R = OOS_START
    while R <= D["day"].max():
        te = (days >= R) & (days < R + pd.Timedelta(weeks=13))
        tr = (days + pd.Timedelta(days=9) <= R) & np.isfinite(y)        # target window (t+1..t+8) closed before R
        if te.any() and tr.sum() > 300:
            ytr = np.clip(y[tr], *np.nanpercentile(y[tr], [1, 99]))
            Xtr, Xte = prep(X[tr], X[te])
            for m, f in use.items():
                preds[m][te] = f(SQ[tr], ytr, SQ[te]) if m == "M4_lstm" else f(Xtr, ytr, Xte)
            print(time.strftime("%T"), "retrain", R.date(), "train", int(tr.sum()), "test", int(te.sum()), flush=True)
        R += pd.Timedelta(weeks=13)
    P = D[["day", "coin", "y", "y_raw"]].copy()
    for m in use:
        P[m] = preds[m]
    return P


def weights(P, model, gated):
    W = S.zeros()
    btcw = S.trendw(CF["KRW-BTC"]).fillna(0)
    for d, g in P.dropna(subset=[model]).groupby("day"):
        top = g.nlargest(5, model).coin.tolist()
        k = float(btcw.get(d, 0)) if gated else 1.0
        W.loc[d:d + pd.Timedelta(days=6), :] = 0.0
        W.loc[d:d + pd.Timedelta(days=6), top] = 0.2 * k
    return W


def weekly_ic(P, model):
    g = P.dropna(subset=[model, "y"]).groupby("day")
    return g.apply(lambda x: x[model].rank().corr(x["y"].rank()) if len(x) > 5 else np.nan).dropna()


def main():
    """LightGBM and PyTorch deadlock in one process on macOS (two OpenMP runtimes), so each model runs in its own
    process: `b56_ml.py --model M2_lgbm` writes its predictions; `b56_ml.py --eval` combines and evaluates."""
    t0 = time.time()
    if "--model" in sys.argv:
        m = sys.argv[sys.argv.index("--model") + 1]
        D, SQ = dataset(); print("dataset", D.shape, SQ.shape, flush=True)
        walk_forward(D, SQ, only=m).to_parquet(B.ROOT / f"data/upbit_db/b56_preds_{m}.parquet", index=False)
        return
    D = pd.read_parquet(B.ROOT / "data/upbit_db/b56_preds_M1_ridge.parquet")[["day", "coin", "y", "y_raw"]]
    P = D.copy()
    for m in MODELS:
        P[m] = pd.read_parquet(B.ROOT / f"data/upbit_db/b56_preds_{m}.parquet")[m].to_numpy()
    P["M5_ens"] = P.groupby("day")[list(MODELS)].rank(pct=True).mean(axis=1)
    P.to_parquet(B.ROOT / "data/upbit_db/b56_preds.parquet", index=False)
    f17 = S.run(S.W_f17()); ewu = S.run(S.ew(UNIV))
    rows = []
    for model in list(MODELS) + ["M5_ens"]:
        ic = weekly_ic(P, model)
        for gated in (False, True):
            sid = f"{model}_{'PG' if gated else 'P'}"
            r = S.run(weights(P, model, gated))
            rec = dict(id=sid, model=model, gated=gated)
            for tag, (a, z) in (("d", ("2019-01-07", "2024-01-01")), ("h", S.HOLD)):
                s, b, e = S.window(r, a, z), S.window(f17, a, z), S.window(ewu, a, z)
                obs, p = B.boot_p(s.to_numpy(), b.to_numpy(), lambda x: B.sharpe(x), 20, draws=2000)
                icw = ic[(ic.index >= a) & (ic.index < z)]
                rec.update({f"{tag}_sh": B.sharpe(s), f"{tag}_f17": B.sharpe(b), f"{tag}_ew": B.sharpe(e), f"{tag}_diff": obs,
                            f"{tag}_p": p, f"{tag}_cagr": B.cagr(s), f"{tag}_dd": B.maxdd(s), f"{tag}_f17dd": B.maxdd(b),
                            f"{tag}_ic": icw.mean(), f"{tag}_ict": icw.mean() / (icw.std() / np.sqrt(len(icw)))})
            rows.append(rec)
            print(sid, f"disc Sh {rec['d_sh']:.2f} IC {rec['d_ic']:+.3f} | hold Sh {rec['h_sh']:.2f} IC {rec['h_ic']:+.3f}", flush=True)
    R = pd.DataFrame(rows)
    R["bhy"] = S.bhy(R.d_p.to_numpy())
    R["survivor"] = R.bhy & (R.d_diff > 0) & (R.d_dd >= R.d_f17dd - 0.10)
    ns = int(R.survivor.sum())
    R["PASS"] = R.survivor & (R.h_diff > 0) & (R.h_p < 0.05 / max(ns, 1)) & (R.h_dd >= R.h_f17dd - 0.10)
    R.to_parquet(B.ROOT / "data/upbit_db/b56_results.parquet", index=False)
    L = ["# B56: ML/DL weekly prediction (prereg v7)", "",
         f"Run {time.strftime('%Y-%m-%d %H:%M')}, {time.time() - t0:.0f}s. Rows {len(D)}, OOS from {OOS_START.date()}. "
         f"F17 Sharpe disc {R.d_f17.iloc[0]:.2f} / hold {R.h_f17.iloc[0]:.2f}; EW universe {R.d_ew.iloc[0]:.2f} / {R.h_ew.iloc[0]:.2f}. "
         f"BHY survivors {ns}.", "",
         "| strategy | disc IC (t) | disc Sharpe | vs F17 p | disc CAGR / maxDD | hold IC (t) | hold Sharpe | hold CAGR / maxDD | verdict |",
         "|---|---|---|---|---|---|---|---|---|"]
    for r in R.itertuples():
        v = "PASS" if r.PASS else ("survivor FAIL" if r.survivor else "-")
        L.append(f"| {r.id} | {r.d_ic:+.3f} ({r.d_ict:.1f}) | {r.d_sh:.2f} | {r.d_p:.3f} | {r.d_cagr:+.0%} / {r.d_dd:.0%} | "
                 f"{r.h_ic:+.3f} ({r.h_ict:.1f}) | {r.h_sh:.2f} | {r.h_cagr:+.0%} / {r.h_dd:.0%} | {v} |")
    (B.ROOT / "research/b56_ml.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
