"""B17 F11 execution (registry F11_execution_001, 004, 005, 006) applied to the only B17 pass the main book trades: the oi_drop3 short
(5% stop, exit +4h; trades from data/cache/b17/f08_trades.parquet), simulated on real 1-minute klines (b2_panel.load_minutes) with the exact
stop model of b17_f08_exact.py. The registry's parent was 'F02 best', but F02 had no pass. Not runnable: 002 iceberg (no size-dependent
impact model; the paper size is tiny), 003 Bybit-vs-Binance spread (no historical spreads) -> not_run.
  .venv/bin/python -W ignore scripts/b17_f11.py   -> data/reports/b17/f11.json
Baseline: taker short at the close of the signal minute (fee 0.05% + dv24 slippage each side).
  001 maker entry: sell limit at +0.3% above the signal close, valid 5 minutes; filled only if a later 1m high exceeds the limit by 0.05%
      (trade-through, queue-conservative); maker fee 0.02%, no entry slippage; unfilled -> no trade (P&L 0). Variant 001b: unfilled -> taker at +5m.
  004 take-profit buy limit at -2% / -3% / -5% from entry (filled if a 1m low goes 0.05% through), otherwise the stop/+4h rules; maker exit fee.
  005 skip the trade when top-of-book depth / dv24 at entry is in the bottom quintile (threshold from discovery).
  006 latency: enter 1 / 2 / 5 / 10 minutes after the signal close (the live F6 service polls every 5 minutes).
All cells compared per signal (paired, skipped = 0) against the baseline; day-clustered bootstrap. Pass: validation net CI > 0 AND
cell - baseline CI > 0; BH q=0.10 over cells."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

os.environ["B2_ERA"] = "all"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b2_panel as bp  # noqa: E402
import b15_models as M  # noqa: E402
from b17_f03 import bh  # noqa: E402

B, OUT, C = ROOT / "data/cache/b17", ROOT / "data/reports/b17", M.C
RNG = np.random.default_rng(1711)
VAL0 = 1767225600
STOP, HZ, SLIP_STOP, TAKER, MAKER, THRU = 0.05, 240, 0.002, 0.0005, 0.0002, 0.0005


def short_path(c, h, l, k, pe, tp=None):
    """short entered at price pe at the close of minute k. Returns (gross, exit_kind). exact stop on highs; optional TP limit on lows."""
    lvl = pe * (1 + STOP)
    for m in range(1, HZ + 1):
        if k + m >= len(c):
            return None
        op = c[k + m - 1]
        if op >= lvl:
            return -(op / pe - 1) - SLIP_STOP, "stop_gap"
        if h[k + m] >= lvl:
            return -(lvl / pe - 1) - SLIP_STOP, "stop"
        if tp is not None and l[k + m] <= pe * (1 - tp) * (1 - THRU):
            return tp, "tp"
    return -(c[k + HZ] / pe - 1), "time"


def simulate():
    T = pd.read_parquet(B / "f08_trades.parquet"); T = T[T["trig"] == "oi_drop3"].copy()
    P = M.pump_context()[["pump_id", "dv24"]]; T = T.merge(P, on="pump_id", how="left")
    rows = []
    for n_, (code, g) in enumerate(T.groupby("code")):
        d = bp.load_minutes(code)
        if d is None:
            continue
        t = d.index.to_numpy().astype("int64"); t = t // 10 ** 9 if t.max() > 1e12 else t
        c, h, l = d["c"].to_numpy(float), d["h"].to_numpy(float), d["l"].to_numpy(float)
        fp = C / f"depth1h/{code}.parquet"
        dep = pd.read_parquet(fp, columns=["ts", "bid_1", "ask_1"]).sort_values("ts") if fp.exists() else None
        for r in g.itertuples(index=False):
            k = np.searchsorted(t, r.t_in)
            if k >= len(t) or t[k] != r.t_in or k + HZ + 12 >= len(t):
                continue
            sl = float(M.slip(np.array([r.dv24]))[0]); side = TAKER + sl
            out = {"ts": r.ts, "day": r.ts // 86400, "val": r.ts >= VAL0}
            b = short_path(c, h, l, k, c[k]); out["base"] = b[0] - 2 * side
            # 001 maker entry
            lim = c[k] * 1.003; fill = next((m for m in range(1, 6) if h[k + m] >= lim * (1 + THRU)), None)
            if fill is not None:
                s = short_path(c, h, l, k + fill, lim); out["m001"] = s[0] - MAKER - side
            else:
                out["m001"] = 0.0
            if fill is None:
                s = short_path(c, h, l, k + 5, c[k + 5]); out["m001b"] = s[0] - 2 * side
            else:
                out["m001b"] = out["m001"]
            # 004 take-profit limits
            for tp in (0.02, 0.03, 0.05):
                s = short_path(c, h, l, k, c[k], tp)
                out[f"m004_tp{int(tp * 100)}"] = s[0] - side - (MAKER if s[1] == "tp" else side)
            # 005 depth filter (value stored; threshold applied later)
            if dep is not None and len(dep):
                j = np.searchsorted(dep["ts"].to_numpy(), r.t_in, "right") - 1
                out["d2v"] = (dep["bid_1"].iloc[j] + dep["ask_1"].iloc[j]) / r.dv24 if j >= 0 else np.nan
            else:
                out["d2v"] = np.nan
            # 006 latency
            for lat in (1, 2, 5, 10):
                s = short_path(c, h, l, k + lat, c[k + lat]); out[f"m006_lat{lat}"] = s[0] - 2 * side
            rows.append(out)
        if n_ % 100 == 0:
            print("coins", n_, "trades", len(rows), flush=True)
    return pd.DataFrame(rows)


def boot(x, day, reps=2000):
    b = pd.DataFrame({"x": x, "d": day}).groupby("d")["x"].agg(["sum", "count"]); su, cn = b["sum"].to_numpy(), b["count"].to_numpy()
    bo = np.array([su[k].sum() / cn[k].sum() for k in (RNG.integers(0, len(su), len(su)) for _ in range(reps))])
    return [float(v) for v in np.percentile(bo, [2.5, 97.5])], float((bo <= 0).mean())


def main():
    fp = B / "f11_sim.parquet"
    D = pd.read_parquet(fp) if fp.exists() else simulate()
    D.to_parquet(fp, index=False)
    thr = np.nanquantile(D.loc[~D["val"], "d2v"], 0.2)
    D["m005_skipthin"] = np.where(D["d2v"] < thr, 0.0, D["base"])
    V, Dd = D[D["val"]], D[~D["val"]]
    res = {"not_run": {"002": "no size-dependent impact model (paper size tiny)", "003": "no historical Binance/Bybit spreads"},
           "n_val": int(len(V)), "n_disc": int(len(Dd)), "depth_threshold": float(thr)}
    ci, _ = boot(V["base"].to_numpy(), V["day"].to_numpy()); res["baseline_val"] = dict(mean=float(V["base"].mean()), ci=ci)
    res["baseline_disc"] = dict(mean=float(Dd["base"].mean()))
    pv, tests = {}, {}
    for col in [c for c in D.columns if c.startswith("m0")]:
        ci_v, p_v = boot(V[col].to_numpy(), V["day"].to_numpy()); ci_d, p_d = boot((V[col] - V["base"]).to_numpy(), V["day"].to_numpy())
        tests[col] = dict(val=float(V[col].mean()), val_ci=ci_v, minus_base=float((V[col] - V["base"]).mean()), minus_base_ci=ci_d,
                          disc=float(Dd[col].mean()), disc_minus_base=float((Dd[col] - Dd["base"]).mean()))
        if col == "m001":
            tests[col]["fill_rate_val"] = float((V["m001"] != 0).mean())
        pv[col] = max(p_v, p_d)
        print(col, json.dumps({k: (round(v, 5) if isinstance(v, float) else [round(a, 5) for a in v] if isinstance(v, list) else v) for k, v in tests[col].items()}), flush=True)
    res["tests"] = tests; res["bh_pass"] = bh(pv)
    res["pass"] = [k for k in res["bh_pass"] if tests[k]["val_ci"][0] > 0 and tests[k]["minus_base_ci"][0] > 0]
    OUT.mkdir(parents=True, exist_ok=True); json.dump(res, open(OUT / "f11.json", "w"), indent=1, default=float)
    print("baseline", res["baseline_val"], "\nF11 BH", res["bh_pass"], "PASS", res["pass"], flush=True)


if __name__ == "__main__":
    main()
