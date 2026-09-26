#!/usr/bin/env python3
"""Follow-up to the deep search: combine 'will it move 10%' with 'which way',
and price the best rules under different exits (not just TP +10% / SL -5%).

Exits on the 24h path (entry = next minute open, 0.3% round trip):
  tp10_sl5     +10% take-profit, -5% stop, else 24h close   (deep search default)
  tp10_sl10    +10% / -10%
  tp20_sl10    +20% / -10% (approx: uses mfe/mae; order unknown -> counted as loss)
  hold24       plain 24h close
Short side: same with the signs flipped (a dump predictor as a short).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from src.research import deep_search as ds  # noqa: E402
from src.research import deep_tests as dt  # noqa: E402

COST = 0.003


def exits(p: pd.DataFrame, m: np.ndarray, side: int = 1) -> dict[str, float]:
    mfe, mae, ret = (p[k].to_numpy()[m] for k in ("mfe_24h", "mae_24h", "ret_24h"))
    if side < 0:
        mfe, mae, ret = -mae, -mfe, -ret
    win10 = p["win10_24h"].to_numpy()[m] if side > 0 else np.full(m.sum(), np.nan)
    out = {"n": int(m.sum()), "hold24": float(np.nanmean(ret)) - COST}
    # conservative when both touched and order unknown: loss
    for tp, sl in ((0.10, 0.05), (0.10, 0.10), (0.20, 0.10), (0.05, 0.05)):
        hit_tp, hit_sl = mfe >= tp, mae <= -sl
        if side > 0 and tp == 0.10 and sl == 0.05:
            r = np.where(win10 == 1, tp, np.where(hit_sl, -sl, ret))
        else:
            r = np.where(hit_sl, -sl, np.where(hit_tp, tp, ret))
        out[f"tp{int(tp * 100)}_sl{int(sl * 100)}"] = float(np.nanmean(r)) - COST
    out["hit_up10"] = float(np.nanmean(p["up10_24h"].to_numpy()[m]))
    out["hit_dn10"] = float(np.nanmean(p["dn10_24h"].to_numpy()[m]))
    return out


def main() -> int:
    p = pd.read_parquet(ds.PANEL)
    p = dt.add_trade_labels(p)
    tr, te, cut = dt.split(p)
    feats = [f for f in ds.feature_cols(p) if not f.startswith(("trade_", "dir_"))]
    res: dict = {}
    # 1. move model x direction model
    mv = p["up10_24h"].fillna(0).clip(0, 1).astype(float)
    p["move10_24h"] = np.where(p["up10_24h"].isna(), np.nan, ((p["up10_24h"] == 1) | (p["dn10_24h"] == 1)).astype(float))
    del mv
    a = dt.fit_eval(p, feats, "move10_24h", tr, te)
    b_tr = tr.copy()
    b = dt.fit_eval(p, feats, "dir_24h", b_tr, te)          # trained only where a move happened (dir is NaN otherwise)
    # score all test rows with the direction model
    import lightgbm as lgb  # noqa: F401
    Xall, cols = dt._matrix(p)
    ci = [cols[f] for f in feats]
    tem = te & np.isfinite(p["up10_24h"].to_numpy())
    idx = np.where(tem)[0]
    s_move = a["_booster"].predict(Xall[np.ix_(idx, ci)])
    s_dir = b["_booster"].predict(Xall[np.ix_(idx, ci)])
    res["move_auc"], res["dir_auc"] = a["auc"], b["auc"]
    rows = []
    for mq in (0.5, 0.8, 0.9, 0.95, 0.99):
        for dq in (0.5, 0.8, 0.9, 0.95):
            sel = (s_move >= np.quantile(s_move, mq)) & (s_dir >= np.quantile(s_dir[s_move >= np.quantile(s_move, mq)], dq))
            m = np.zeros(len(p), bool)
            m[idx[sel]] = True
            r = exits(p, m, 1)
            rows.append({"move_top": round(1 - mq, 3), "dir_top_within": round(1 - dq, 3), **r,
                         "units": int(p.loc[m, ["symbol"]].assign(d=p.loc[m, "ts"] // 86400).drop_duplicates().shape[0])})
            # short: high move, low up-probability
            sel_s = (s_move >= np.quantile(s_move, mq)) & (s_dir <= np.quantile(s_dir[s_move >= np.quantile(s_move, mq)], 1 - dq))
            ms = np.zeros(len(p), bool)
            ms[idx[sel_s]] = True
            rs = exits(p, ms, -1)
            rows.append({"move_top": round(1 - mq, 3), "dir_top_within": -round(1 - dq, 3), "side": "short", **rs})
    res["combo"] = rows
    # monthly stability of the best long combo
    best = max((r for r in rows if r.get("side") != "short" and r["n"] >= 300), key=lambda r: r["tp10_sl10"])
    res["best_long"] = best
    # 2. the strongest rule from the cell search, priced with every exit
    q = lambda f, lo=None, hi=None: (p[f] >= lo if lo is not None else True) & (p[f] < hi if hi is not None else True)  # noqa: E731
    rule = (q("ret_10080m", 0.0577) & q("dhi_7d", None, -0.187) & q("vsurge_60m", 1.27) & q("taker_1440m", 0.505)).to_numpy()
    res["rule_breakout_after_drop"] = {"train": exits(p, rule & tr, 1), "test": exits(p, rule & te, 1)}
    # 3. per-month test ev of best combo selection
    out = PROJECT_ROOT / "data" / "reports" / "deep" / "combo.json"
    out.write_text(json.dumps(res, ensure_ascii=False, default=float))
    for r in rows:
        print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()})
    print("move auc", a["auc"], "dir auc", b["auc"])
    print("rule", json.dumps(res["rule_breakout_after_drop"], default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
