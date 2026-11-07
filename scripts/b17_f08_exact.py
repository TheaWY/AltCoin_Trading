"""B17 F04/F08 follow-up: exact stop for the oi_drop3 short using real minute highs (b2_panel.load_minutes), not the close-only / range-bound models.
  .venv/bin/python -W ignore scripts/b17_f08_exact.py   -> adds net_exact, mae_exact, held_exact to data/cache/b17/f08_trades.parquet (oi_drop3 rows)
Short entered at the close of the entry minute (index ts + 60e). For each later minute up to +240: if open (= previous close) >= stop level the
fill is that open + 0.2% (gap); elif high >= stop level the fill is stop + 0.2%. Otherwise exit at the +240 close. Also checks that the kline
trades that were not stopped match the close-only model (sanity)."""
from __future__ import annotations

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

B = ROOT / "data/cache/b17"
STOP, HZ, SLIP = 0.05, 240, 0.002


def main():
    T = pd.read_parquet(B / "f08_trades.parquet")
    P = M.pump_context()[["pump_id", "cost"]]
    idx = T.index[T["trig"] == "oi_drop3"]
    out = {k: np.full(len(T), np.nan) for k in ("net_exact", "mae_exact", "held_exact")}
    cost = T["pump_id"].map(P.set_index("pump_id")["cost"]).to_numpy()
    for n_, (code, g) in enumerate(T.loc[idx].groupby("code")):
        d = bp.load_minutes(code)
        if d is None:
            continue
        t = d.index.to_numpy().astype("int64"); t = t // 10 ** 9 if t.max() > 1e12 else t
        c, h = d["c"].to_numpy(np.float64), d["h"].to_numpy(np.float64)
        for j, row in g.iterrows():
            k = np.searchsorted(t, row["t_in"])
            if k >= len(t) or t[k] != row["t_in"] or k + HZ >= len(t):
                continue
            pe = c[k]; lvl = pe * (1 + STOP)
            op = c[k:k + HZ]; hi = h[k + 1:k + 1 + HZ]; cl = c[k + 1:k + 1 + HZ]
            gap = np.flatnonzero(op >= lvl); touch = np.flatnonzero(hi >= lvl)
            m = min(gap[0] if len(gap) else HZ, touch[0] if len(touch) else HZ)
            if m < HZ:
                fill = op[m] if (len(gap) and gap[0] == m) else lvl
                g_ = -(fill / pe - 1) - SLIP; held = m + 1; mae = hi[:m + 1].max() / pe - 1
            else:
                g_ = -(cl[-1] / pe - 1); held = HZ; mae = max(hi.max() / pe - 1, 0)
            out["net_exact"][j] = g_ - cost[j]; out["mae_exact"][j] = mae; out["held_exact"][j] = held
        if n_ % 100 == 0:
            print("coins", n_, flush=True)
    for k, v in out.items():
        T[k] = v
    T.to_parquet(B / "f08_trades.parquet", index=False)
    x = T.loc[idx]
    for per, s in (("disc", ~x["val"]), ("val", x["val"])):
        y = x[s].dropna(subset=["net_exact"])
        print(per, len(y), "of", int(s.sum()), "exact", round(y["net_exact"].mean(), 4), "ci", [round(a, 4) for a in M.day_ci(y["net_exact"].to_numpy(), y["day"].to_numpy())],
              "| f04", round(y["net"].mean(), 4), "cons", round(y["net_cons"].mean(), 4), "gap", round(y["net_gap"].mean(), 4),
              "| stopped", round((y["held_exact"] < HZ).mean(), 3), "mae90", round(y["mae_exact"].quantile(0.9), 3), flush=True)
    u = x[(x["held_exact"] == HZ) & (x["net_gap"] > -STOP)].dropna(subset=["net_exact"])
    print("sanity: unstopped trades, |exact - gap| median", round(float((u["net_exact"] - u["net_gap"]).abs().median()), 5), "n", len(u))
    print("EXACT_DONE", flush=True)


if __name__ == "__main__":
    main()
