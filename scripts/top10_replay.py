#!/usr/bin/env python3
"""Out-of-sample replay of the hourly TOP 10 'will move 10%' list.

The move and direction models are retrained on panel rows BEFORE --cut only,
then every hour after the cut is scored exactly like the live job (liquid
coins, top 10 by move probability) and written to move_top10_log with
replay=1, outcomes taken from the same 1-minute data. Live picks (replay=0)
are never touched, and the dashboard keeps the two apart.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
warnings.filterwarnings("ignore")
from src.data.storage import get_storage  # noqa: E402
from src.engine import pump_watch as pw  # noqa: E402
from src.research import deep_search as ds  # noqa: E402

LIVE_EXCLUDE = ("kr_", "kimchi", "dhi_30d", "dlo_30d", "ret_10080m", "age_censored")


def main() -> int:
    import lightgbm as lgb
    ap = argparse.ArgumentParser()
    ap.add_argument("--cut", default="2026-09-01")
    a = ap.parse_args()
    cut = int(pd.Timestamp(a.cut, tz="UTC").timestamp())
    p = pd.read_parquet(ds.PANEL)
    u, d = p["up10_24h"], p["dn10_24h"]
    p["move10_24h"] = np.where(u.isna(), np.nan, ((u == 1) | (d == 1)).astype(float))
    p["dir_24h"] = np.where((u == 1) & (d != 1), 1.0, np.where((d == 1) & (u != 1), 0.0, np.nan))
    feats = [f for f in ds.feature_cols(p) if not f.startswith(LIVE_EXCLUDE) and not f.startswith(("trade_", "dir_", "move10"))]
    trm = (p["ts"] < cut - 86400).to_numpy()
    params = {"objective": "binary", "learning_rate": 0.05, "num_leaves": 31, "min_data_in_leaf": 400, "feature_fraction": 0.8,
              "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 5.0, "verbose": -1, "num_threads": 8}
    X = p[feats].to_numpy(np.float32)
    models = {}
    for target in ("move10_24h", "dir_24h"):
        y = p[target].to_numpy()
        idx = np.where(trm & np.isfinite(y))[0]
        idx = np.sort(np.random.default_rng(0).choice(idx, min(len(idx), 1_000_000), replace=False))
        models[target] = lgb.train(params, lgb.Dataset(X[idx], y[idx]), num_boost_round=300)
    te = (p["ts"] >= cut) & (p["dv24_log"] >= np.log1p(2e6)) & p["mfe_24h"].notna()
    q = p[te].copy()
    Xq = q[feats].to_numpy(np.float32)
    q["move_p"] = models["move10_24h"].predict(Xq)
    q["up_p"] = models["dir_24h"].predict(Xq)
    top = q.sort_values("move_p", ascending=False).groupby("ts").head(10).copy()
    top["rank"] = top.groupby("ts")["move_p"].rank(ascending=False, method="first").astype(int)
    storage = get_storage()
    with storage._connect() as c:  # noqa: SLF001
        for s in pw.SCHEMA:
            c.execute(s)
        try:
            c.execute("ALTER TABLE move_top10_log ADD COLUMN IF NOT EXISTS replay INTEGER NOT NULL DEFAULT 0")
        except Exception:  # noqa: BLE001
            pass
        c.execute("DELETE FROM move_top10_log WHERE replay = 1")
        n = 0
        for r in top.itertuples():
            c.execute("INSERT INTO move_top10_log (ts, symbol, rank, move_p, up_p, price, status, mfe, mae, ret, moved10, up10, "
                      "dn10, replay) VALUES (?,?,?,?,?,?,'closed',?,?,?,?,?,?,1) ON CONFLICT DO NOTHING",
                      (int(r.ts), r.symbol, int(r.rank), float(r.move_p), float(r.up_p), None, float(r.mfe_24h),
                       float(r.mae_24h), float(r.ret_24h), int(r.up10_24h == 1 or r.dn10_24h == 1), int(r.up10_24h == 1),
                       int(r.dn10_24h == 1)))
            n += 1
    first = top.assign(day=top["ts"] // 86400).sort_values("ts").drop_duplicates(["symbol", "day"])
    lean = first[(first["up_p"] - 0.5).abs() >= 0.1]
    lean = lean[(lean["up10_24h"] == 1) ^ (lean["dn10_24h"] == 1)]
    print(json.dumps({"hours": int(top["ts"].nunique()), "picks": n, "moved10_all": float(((top["up10_24h"] == 1) | (top["dn10_24h"] == 1)).mean()),
                      "coin_days": len(first), "moved10_coin_days": float(((first["up10_24h"] == 1) | (first["dn10_24h"] == 1)).mean()),
                      "up10": float((first["up10_24h"] == 1).mean()), "dn10": float((first["dn10_24h"] == 1).mean()),
                      "lean_n": len(lean), "lean_right": float(((lean["up_p"] > 0.5) == (lean["up10_24h"] == 1)).mean()) if len(lean) else None}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
