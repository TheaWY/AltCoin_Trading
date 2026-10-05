"""B57 runner (prereg v8): tune one model on 2018-04..2021 -> 2022, freeze, walk-forward 2023-01..2026-10 (13-week retrain).
Usage: b57_run.py MODEL H TARGET   (TARGET = sel (demeaned, whole universe) | tim (raw, BTC/ETH only))
Writes data/upbit_db/b57/preds/<tag>.parquet (dev, < 2025-07-01) and data/upbit_db/b57/sealed/<tag>.parquet (sealed)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import b57_models as Z  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "data/upbit_db/b57"
TUNE_END, VAL_END = pd.Timestamp("2022-01-01"), pd.Timestamp("2023-01-01")
WF_START, SEAL = pd.Timestamp("2023-01-01"), pd.Timestamp("2025-07-01")
MAJ = ["KRW-BTC", "KRW-ETH"]


def load(model, h, target):
    D = pd.read_parquet(DIR / "rows.parquet")
    ycol = f"yd{h}" if target == "sel" else f"yr{h}"
    if h == 7:
        D = D[D.day.dt.weekday == 6]
    elif h == 28:
        D = D[(D.day.dt.weekday == 6) & (((D.day - pd.Timestamp("2018-04-01")).dt.days // 7) % 4 == 0)]
    if target == "tim":
        D = D[D.coin.isin(MAJ)]
    fcols = [c for c in D.columns if c not in ("day", "coin") and not c.startswith(("yd", "yr"))]
    D = D.reset_index(drop=True)
    if model in Z.SEQ_MODELS:
        key = pd.read_parquet(DIR / "seq_index.parquet").reset_index().rename(columns={"index": "si"})
        D = D.merge(key, on=["day", "coin"], how="inner").reset_index(drop=True)
        SQ = np.load(DIR / "seq_weekly.npy", mmap_mode="r")
        X = np.asarray(SQ[D.si.to_numpy()])
    else:
        X = D[fcols].to_numpy(float)
    return D, X, D[ycol].to_numpy(float)


def prep(Xtr, Xte):
    if Xtr.ndim == 3:
        return Xtr, Xte
    med = np.nanmedian(Xtr, 0); med = np.where(np.isfinite(med), med, 0)
    Xtr = np.where(np.isfinite(Xtr), Xtr, med); Xte = np.where(np.isfinite(Xte), Xte, med)
    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9
    return np.clip((Xtr - mu) / sd, -5, 5), np.clip((Xte - mu) / sd, -5, 5)


def score(D, pred, y, target):
    ok = np.isfinite(y) & np.isfinite(pred)
    if target == "tim":
        return pd.Series(pred[ok]).rank().corr(pd.Series(y[ok]).rank())
    df = pd.DataFrame({"d": D.day.to_numpy()[ok], "p": pred[ok], "y": y[ok]})
    return df.groupby("d").apply(lambda g: g.p.rank().corr(g.y.rank()) if len(g) > 5 else np.nan).mean()


def main():
    model, h, target = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    tag = f"{model}_h{h}_{target}"; t0 = time.time()
    (DIR / "preds").mkdir(exist_ok=True); (DIR / "sealed").mkdir(exist_ok=True); (DIR / "tune").mkdir(exist_ok=True)
    D, X, y = load(model, h, target)
    days = D.day.to_numpy(); end = days + np.timedelta64(h + 1, "D")
    clip = lambda v: np.clip(v, *np.nanpercentile(v, [1, 99]))  # noqa: E731
    # ---- tuning (train ends before 2022, validate on 2022; purged by target end)
    tr = (end < np.datetime64(TUNE_END)) & np.isfinite(y)
    va = (days >= np.datetime64(TUNE_END)) & (end < np.datetime64(VAL_END)) & np.isfinite(y)
    Xtr, Xva = prep(X[tr], X[va]); ytr = clip(y[tr])
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def objective(t):
        p = Z.space(model, t)
        return score(D[va], Z.fit(model, p, Xtr, ytr, Xva), y[va], target)
    st = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))
    st.optimize(objective, n_trials=Z.TRIALS[model])
    best = st.best_trial.params
    p = {**best}
    if "lr" in p and model in ("lgbm", "xgb", "cat"):
        p["learning_rate"] = p.pop("lr")
    ren = {"mcs": "min_child_samples", "colsample": "colsample_bytree", "mcw": "min_child_weight", "l2": "l2_leaf_reg"}
    p = {ren.get(k, k): v for k, v in p.items()}
    json.dump({"tag": tag, "best_val": st.best_value, "params": p, "n_train": int(tr.sum()), "n_val": int(va.sum())},
              open(DIR / "tune" / f"{tag}.json", "w"), indent=1, default=float)
    print(time.strftime("%T"), tag, "tuned val score", round(st.best_value, 4), p, flush=True)
    # ---- walk-forward with frozen params
    pred = np.full(len(y), np.nan); R = WF_START
    seeds = (1, 2, 3) if model in Z.SEQ_MODELS or model == "mlp" else (1,)
    while R <= pd.Timestamp(days.max()):
        te = (days >= np.datetime64(R)) & (days < np.datetime64(R + pd.Timedelta(weeks=13)))
        trw = (end < np.datetime64(R)) & np.isfinite(y)
        if te.any() and trw.sum() > 200:
            A, Bx = prep(X[trw], X[te]); yy = clip(y[trw])
            pred[te] = np.mean([Z.fit(model, p, A, yy, Bx, seed=s) for s in seeds], axis=0)
            print(time.strftime("%T"), tag, "wf", R.date(), int(trw.sum()), int(te.sum()), flush=True)
        R += pd.Timedelta(weeks=13)
    out = D[["day", "coin"]].copy(); out["pred"] = pred; out["y"] = y
    out = out[out.day >= WF_START]
    out[out.day < SEAL].to_parquet(DIR / "preds" / f"{tag}.parquet", index=False)
    out[out.day >= SEAL].to_parquet(DIR / "sealed" / f"{tag}.parquet", index=False)
    print(time.strftime("%T"), tag, "done", f"{time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
