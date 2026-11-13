"""B14_4 tree baseline for the DL comparison: LightGBM on the tensor + hand-made sequence summaries, targets pump6 and sign.
Out: data/cache/b14/pred_dl_tree.parquet (ts, j, p_tree_pump6, p_tree_sign)."""
import sys

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import roc_auc_score

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import b14_lib as B  # noqa: E402
from b14_lib import wf_folds  # noqa: E402

T, G = B.load_tab()
cols = B.feature_sets(G)["d_full"]
S = np.asarray(np.load(B.B14 / "seq.npy", mmap_mode="r"))[T["seq_idx"].to_numpy()].astype(np.float32)
# sequence summaries: mean / std / last-6h mean of each channel
summ = np.concatenate([S.mean(1), S.std(1), S[:, -6:].mean(1)], 1)
sc = [f"sq{k}" for k in range(summ.shape[1])]
T[sc] = summ
cols = cols + sc
out = []
for q0, tr, va, te in wf_folds(T):
    ted = T[te][["ts", "j"]].copy()
    for target in ("pump6", "sign"):
        y = T["y_pump6"] if target == "pump6" else (T["y_res24"] > 0).astype(int)
        m = lgb.LGBMClassifier(n_estimators=400, learning_rate=0.03, num_leaves=63, min_child_samples=300, subsample=0.8, subsample_freq=1,
                               colsample_bytree=0.7, reg_lambda=10.0, verbose=-1, n_jobs=8,
                               scale_pos_weight=float((1 - y[tr].mean()) / max(y[tr].mean(), 1e-4)) if target == "pump6" else 1.0)
        m.fit(T[tr][cols], y[tr])
        iso = IsotonicRegression(out_of_bounds="clip").fit(m.predict_proba(T[va][cols])[:, 1], y[va])
        ted[f"p_tree_{target}"] = iso.predict(m.predict_proba(T[te][cols])[:, 1])
        print(q0, target, "auc", round(roc_auc_score(y[te], ted[f"p_tree_{target}"]), 4), flush=True)
    out.append(ted)
pd.concat(out).to_parquet(B.B14 / "pred_dl_tree.parquet", index=False)
