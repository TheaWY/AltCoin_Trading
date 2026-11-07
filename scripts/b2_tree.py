"""Tree/linear stage of the B2 model layer (separate process: lightgbm and torch OpenMP clash on macOS)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TR_END = int(pd.Timestamp("2025-12-01").timestamp())
VA_END = int(pd.Timestamp("2026-01-01").timestamp())


def tree_stage(name: str) -> None:
    import lightgbm as lgb
    from sklearn.linear_model import LogisticRegression
    z = np.load(ROOT / f"data/cache/b2_ds_{name}.npz", allow_pickle=True)
    tab, gross, ts = z["tab"], z["gross"], z["ts"]
    y = (gross > 0).astype(np.float32)
    tr, va = ts < TR_END, (ts >= TR_END) & (ts < VA_END)
    tabf = np.nan_to_num(tab, nan=0.0)
    lo, hi = np.nanquantile(tabf[tr], 0.01, axis=0), np.nanquantile(tabf[tr], 0.99, axis=0)
    tabf = np.clip(tabf, lo, hi)
    tabf = (tabf - tabf[tr].mean(0)) / (tabf[tr].std(0) + 1e-9)
    m1 = LogisticRegression(C=0.1, max_iter=2000).fit(tabf[tr], y[tr]).predict_proba(tabf)[:, 1]
    d = lgb.train(dict(objective="binary", learning_rate=0.03, num_leaves=15, min_data_in_leaf=50,
                       feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=5.0, verbose=-1,
                       num_threads=4, seed=7),
                  lgb.Dataset(tabf[tr], y[tr]), 500, valid_sets=[lgb.Dataset(tabf[va], y[va])],
                  callbacks=[lgb.early_stopping(40, verbose=False)])
    m2 = d.predict(tabf, num_iteration=d.best_iteration)
    imp = pd.Series(d.feature_importance("gain"), index=range(tab.shape[1]))
    np.savez(ROOT / f"data/cache/b2_tree_{name}.npz", M1=m1, M2=m2, imp=imp.to_numpy())


if __name__ == "__main__":
    tree_stage(sys.argv[1])
