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
    for target in ("up10_24h", "win10_24h"):
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
