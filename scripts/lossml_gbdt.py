"""B6 tree models: LightGBM or CatBoost per walk-forward fold, isotonic-calibrated on the validation window.
  .venv/bin/python -W ignore scripts/lossml_gbdt.py lgb|cat
Out: data/cache/lossml_pred_{lgb,cat}.parquet (row index, fold, p) and data/models/lossml/{model}_f{k}.*"""
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lossml_data import FEATURES  # noqa: E402
from lossml_folds import folds  # noqa: E402

MOD = ROOT / "data/models/lossml"


def main(which: str) -> None:
    MOD.mkdir(parents=True, exist_ok=True)
    T = pd.read_parquet(ROOT / "data/cache/lossml_tab.parquet", columns=["ts"] + FEATURES + ["loss24"])
    ts, y = T["ts"].to_numpy(), T["loss24"].to_numpy()
    X = T[FEATURES].to_numpy(np.float32)
    preds = []
    for f in folds():
        tr, va = ts < f["train1"], (ts >= f["val0"]) & (ts < f["val1"])
        te = (ts >= f["test0"]) & (ts < f["test1"])
        if which == "lgb":
            import lightgbm as lgb
            m = lgb.train(dict(objective="binary", learning_rate=0.03, num_leaves=63, min_data_in_leaf=500,
                               feature_fraction=0.7, bagging_fraction=0.7, bagging_freq=1, lambda_l2=10.0,
                               verbose=-1, num_threads=8, seed=7),
                          lgb.Dataset(X[tr], y[tr]), 2000, valid_sets=[lgb.Dataset(X[va], y[va])],
                          callbacks=[lgb.early_stopping(100, verbose=False)])
            pv, pt = m.predict(X[va], num_iteration=m.best_iteration), m.predict(X[te], num_iteration=m.best_iteration)
            m.save_model(str(MOD / f"lgb_f{f['k']}.txt"), num_iteration=m.best_iteration)
        else:
            from catboost import CatBoostClassifier
            m = CatBoostClassifier(iterations=1500, learning_rate=0.05, depth=6, l2_leaf_reg=10, loss_function="Logloss",
                                   random_seed=7, verbose=False, thread_count=8, early_stopping_rounds=100)
            m.fit(X[tr], y[tr], eval_set=(X[va], y[va]))
            pv, pt = m.predict_proba(X[va])[:, 1], m.predict_proba(X[te])[:, 1]
            m.save_model(str(MOD / f"cat_f{f['k']}.cbm"))
        iso = IsotonicRegression(out_of_bounds="clip").fit(pv, y[va])
        pickle.dump(iso, open(MOD / f"{which}_iso_f{f['k']}.pkl", "wb"))
        pc = iso.predict(pt)
        preds.append(pd.DataFrame({"row": np.flatnonzero(te), "fold": f["k"], "p": pc, "p_raw": pt}))
        print(which, "fold", f["k"], "train", int(tr.sum()), "test", int(te.sum()), "val auc", round(roc_auc_score(y[va], pv), 4),
              "test auc", round(roc_auc_score(y[te], pt), 4), flush=True)
    pd.concat(preds).to_parquet(ROOT / f"data/cache/lossml_pred_{which}.parquet", index=False)


if __name__ == "__main__":
    main(sys.argv[1])
