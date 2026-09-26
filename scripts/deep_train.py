#!/usr/bin/env python3
"""Train the deep-search models on the whole panel and save them for the live
forward test (src/engine/pump_watch.py scores every coin each hour with them).

Only features the live rebuild can compute are used (no Korea, no 30-day
windows that need more than the 9 days of live minutes).
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from src.research import deep_search as ds  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("deep_train")
OUT = PROJECT_ROOT / "data" / "models"
LIVE_EXCLUDE = ("kr_", "kimchi", "dhi_30d", "dlo_30d", "ret_10080m", "age_censored")


def main() -> int:
    import lightgbm as lgb
    p = pd.read_parquet(ds.PANEL)
    feats = [f for f in ds.feature_cols(p) if not f.startswith(LIVE_EXCLUDE) and not f.startswith(("trade_", "dir_"))]
    OUT.mkdir(parents=True, exist_ok=True)
    u, d = p["up10_24h"], p["dn10_24h"]
    p["move10_24h"] = np.where(u.isna(), np.nan, ((u == 1) | (d == 1)).astype(float))
    p["dir_24h"] = np.where((u == 1) & (d != 1), 1.0, np.where((d == 1) & (u != 1), 0.0, np.nan))
    for target in ("up10_24h", "win10_24h", "move10_24h", "dir_24h"):
        y = p[target].to_numpy()
        ok = np.where(np.isfinite(y))[0]
        rng = np.random.default_rng(0)
        idx = np.sort(rng.choice(ok, min(len(ok), 1_200_000), replace=False))
        X = p[feats].to_numpy(np.float32)[idx]
        params = {"objective": "binary", "learning_rate": 0.05, "num_leaves": 31, "min_data_in_leaf": 400,
                  "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 5.0, "verbose": -1,
                  "num_threads": 8}
        bst = lgb.train(params, lgb.Dataset(X, y[idx]), num_boost_round=300)
        name = f"deep_{target}"
        bst.save_model(str(OUT / f"{name}.txt"))
        (OUT / f"{name}.json").write_text(json.dumps({"target": target, "features": feats, "trained_at": int(time.time()),
                                                      "rows": int(len(idx))}))
        log.info("saved %s (%d features)", name, len(feats))
    # size models: how far a coin is likely to swing (quantiles), for stop / target placement
    p["down_24h"], p["up_24h"] = -p["mae_24h"], p["mfe_24h"]
    p["down_4h"], p["up_4h"] = -p["mae_4h"], p["mfe_4h"]
    cut = int(p["ts"].quantile(0.8))
    X = p[feats].to_numpy(np.float32)
    calib = {}
    for target, alpha in (("down_24h", 0.5), ("down_24h", 0.8), ("up_24h", 0.5), ("up_24h", 0.8),
                          ("down_4h", 0.8), ("up_4h", 0.8)):
        y = p[target].to_numpy()
        ok = np.isfinite(y)
        qp = {"objective": "quantile", "alpha": alpha, "learning_rate": 0.05, "num_leaves": 31, "min_data_in_leaf": 400,
              "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "verbose": -1, "num_threads": 8}
        rng = np.random.default_rng(1)
        # calibration check: fit on the first 80% of time, measure coverage on the last 20%
        tr_i = np.where(ok & (p["ts"].to_numpy() < cut - 86400))[0]
        te_i = np.where(ok & (p["ts"].to_numpy() >= cut))[0]
        tr_i = np.sort(rng.choice(tr_i, min(len(tr_i), 500_000), replace=False))
        b = lgb.train(qp, lgb.Dataset(X[tr_i], y[tr_i]), num_boost_round=200)
        pr = b.predict(X[te_i])
        top = p["rv_24h"].to_numpy()[te_i] >= np.nanquantile(p["rv_24h"].to_numpy()[te_i], 0.95)
        calib[f"{target}_q{int(alpha * 100)}"] = {"coverage": float((y[te_i] <= pr).mean()),
                                                  "coverage_volatile": float((y[te_i][top] <= pr[top]).mean()),
                                                  "median_pred_volatile": float(np.median(pr[top]))}
        all_i = np.sort(rng.choice(np.where(ok)[0], min(int(ok.sum()), 800_000), replace=False))
        b = lgb.train(qp, lgb.Dataset(X[all_i], y[all_i]), num_boost_round=200)
        name = f"deep_{target}_q{int(alpha * 100)}"
        b.save_model(str(OUT / f"{name}.txt"))
        (OUT / f"{name}.json").write_text(json.dumps({"target": target, "alpha": alpha, "features": feats,
                                                      "trained_at": int(time.time())}))
        log.info("saved %s calibration %s", name, calib[f"{target}_q{int(alpha * 100)}"])
    (OUT / "size_calibration.json").write_text(json.dumps(calib))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
