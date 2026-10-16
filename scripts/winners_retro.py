#!/usr/bin/env python3
"""Retrospective: what did coins that moved 10%+ in the next 24h look like
beforehand, and could we have picked only those?

Part A  the signal book's own trades: every closed signal_xs trade with its
        return and the indicators at entry (winners >= +10% vs the rest)
Part B  the whole universe, sampled every hour over the 1m history: label
        each (coin, hour) by its NEXT 24h close-to-close return (>= +10% ->
        long winner, <= -10% -> short winner). For every indicator: AUC on
        the first and second half of time, and precision when you only take
        the most extreme 1% / 5% (how often did that pick actually make 10%)
        against the base rate.

Writes data/reports/retro/latest.{json,md}.
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

from src.data.storage import get_storage  # noqa: E402
from src.research import indicators as ind  # noqa: E402
from src.research.pump_precursors import _auc  # noqa: E402

OUT = PROJECT_ROOT / "data" / "reports" / "retro"
H = 1440
MOVE = 0.10
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("retro")


def main() -> int:
    t0 = time.time()
    storage = get_storage()
    ctx = ind.build_ctx(storage, 14 * 1440)
    c = ctx.c
    n = len(c)
    close = c.to_numpy()
    rows = np.arange(0, n - H, 60)                       # hourly samples with a full 24h ahead
    fwd = close[rows + H] / close[rows] - 1
    liquid = (ctx.qv_sum(60).to_numpy()[rows] >= 100_000) & np.isfinite(fwd)
    up, dn = (fwd >= MOVE) & liquid, (fwd <= -MOVE) & liquid
    half = len(rows) // 2
    log.info("samples %d liquid, up10 %d (%.2f%%), down10 %d (%.2f%%)", liquid.sum(), up.sum(),
             100 * up.sum() / liquid.sum(), dn.sum(), 100 * dn.sum() / liquid.sum())

    # ---- Part A: the signal book's trades
    ts_index = {int(t): i for i, t in enumerate(c.index)}
    with storage._connect() as conn:  # noqa: SLF001
        trades = [dict(r) for r in conn.execute(
            "SELECT symbol, direction, entry_price, exit_price, opened_at, closed_at, pnl, quantity FROM paper_trades "
            "WHERE status='closed' AND strategy='signal_xs'").fetchall()]
    keep = ("ret_60", "ret_1440", "ret_7d", "rv_1440", "vol_ratio_60", "oi_chg_1440", "oi_to_mcap", "funding",
            "ls_top_pos", "xs_rank_1440", "dist_high_1440", "dist_ma20d", "mcap", "dvol_1440")
    frames = {i.name: f for i, f in ind.iter_indicators(ctx, set(keep))}
    part_a = []
    for t in trades:
        i = ts_index.get(int(t["opened_at"]) // 60 * 60 - 60)
        side = 1 if t["direction"] == "LONG" else -1
        ret = side * (t["exit_price"] / t["entry_price"] - 1)
        feats = {}
        if i is not None and t["symbol"] in c.columns:
            j = c.columns.get_loc(t["symbol"])
            feats = {k: (None if not np.isfinite(frames[k].iat[i, j]) else round(float(frames[k].iat[i, j]), 4))
                     for k in keep if k in frames}
        part_a.append({"symbol": t["symbol"], "side": t["direction"], "ret": round(ret, 4),
                       "usd_in": round(t["quantity"] * t["entry_price"], 2), "pnl": round(t["pnl"], 2),
                       "winner10": ret >= MOVE, "features": feats})
    del frames

    # ---- Part B: every indicator vs next-24h 10% movers
    res = []
    for i, f in ind.iter_indicators(ctx):
        a = f.to_numpy()[rows]
        row = {"name": i.name, "ko": i.ko, "category": i.cat}
        for lab, y in (("up", up), ("down", dn)):
            for part, sl in (("h1", slice(0, half)), ("h2", slice(half, None))):
                aa, yy, ll = a[sl], y[sl], liquid[sl]
                ok = ll & np.isfinite(aa)
                row[f"{lab}_auc_{part}"] = _auc(aa[ok & yy], aa[ok & ~yy])
            # precision of the extreme tail on the second half, tail direction from the first half
            h1 = row[f"{lab}_auc_h1"]
            if h1 is None:
                continue
            sign = 1 if h1 > 0.5 else -1
            aa, yy, ll = a[half:], y[half:], liquid[half:]
            ok = ll & np.isfinite(aa)
            v, t_ = aa[ok] * sign, yy[ok]
            for top in (0.01, 0.05):
                if len(v) > 200:
                    cut = np.quantile(v, 1 - top)
                    sel = v >= cut
                    row[f"{lab}_prec_top{int(top * 100)}"] = float(t_[sel].mean()) if sel.any() else None
            row[f"{lab}_base"] = float(t_.mean()) if len(t_) else None
            row[f"{lab}_sign"] = sign
        res.append(row)
    for lab in ("up", "down"):
        for r in res:
            a1, a2 = r.get(f"{lab}_auc_h1"), r.get(f"{lab}_auc_h2")
            r[f"{lab}_holds"] = bool(a1 and a2 and ((a1 > 0.55 and a2 > 0.55) or (a1 < 0.45 and a2 < 0.45)))
    report = {"run_at": int(time.time()), "samples": int(liquid.sum()), "up10": int(up.sum()), "down10": int(dn.sum()),
              "base_up": float(up.sum() / liquid.sum()), "base_down": float(dn.sum() / liquid.sum()),
              "signal_book": part_a, "indicators": res}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "latest.json").write_text(json.dumps(report, default=float))
    log.info("done in %.0fs", time.time() - t0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
