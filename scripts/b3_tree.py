"""B3 tree/linear stage: fit M1 (logistic) and M2 (LightGBM) exactly as in B2 on the 2025 pump train split,
predict on the Dec-2025 validation events and on the 2024 holdout pump events. Separate process (OpenMP clash)."""
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

ROOT = Path(__file__).resolve().parents[1]
TR_END = int(pd.Timestamp("2025-12-01").timestamp())
VA_END = int(pd.Timestamp("2026-01-01").timestamp())


def prep(tab, tr, ref=None):
    t = np.nan_to_num(tab, nan=0.0)
    if ref is None:
        lo, hi = np.nanquantile(t[tr], 0.01, axis=0), np.nanquantile(t[tr], 0.99, axis=0)
        c = np.clip(t, lo, hi)
        ref = (lo, hi, c[tr].mean(0), c[tr].std(0) + 1e-9)
    lo, hi, mu, sd = ref
    return (np.clip(t, lo, hi) - mu) / sd, ref


def main() -> None:
    z = np.load(ROOT / "data/cache/b2_ds_pump.npz", allow_pickle=True)
    h = np.load(ROOT / "data/cache/b2_ds_pump_2024.npz", allow_pickle=True)
    ts = z["ts"]
    y = (z["gross"] > 0).astype(np.float32)
    tr, va = ts < TR_END, (ts >= TR_END) & (ts < VA_END)
    X, ref = prep(z["tab"], tr)
    X24, _ = prep(h["tab"], None, ref)
    m1 = LogisticRegression(C=0.1, max_iter=2000).fit(X[tr], y[tr])
    d = lgb.train(dict(objective="binary", learning_rate=0.03, num_leaves=15, min_data_in_leaf=50, feature_fraction=0.8,
                       bagging_fraction=0.8, bagging_freq=1, lambda_l2=5.0, verbose=-1, num_threads=4, seed=7),
                  lgb.Dataset(X[tr], y[tr]), 500, valid_sets=[lgb.Dataset(X[va], y[va])],
                  callbacks=[lgb.early_stopping(40, verbose=False)])
    np.savez(ROOT / "data/cache/b3_tree_preds.npz",
             M1_va=m1.predict_proba(X[va])[:, 1], M1_24=m1.predict_proba(X24)[:, 1],
             M2_va=d.predict(X[va], num_iteration=d.best_iteration), M2_24=d.predict(X24, num_iteration=d.best_iteration))


if __name__ == "__main__":
    main()
    sys.exit(0)
