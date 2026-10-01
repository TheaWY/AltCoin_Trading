"""B27_INTRADAY (2026-10-01). "At what point can you tell if the day is good or bad?"
Day = 00:00..24:00 UTC (09:00 KST). For each hour h into the day, using only data up to h:
  (1) agreement: how often the sign of the return so far equals the sign of the full day (trivially rises with h)
  (2) the tradable question: does the return so far predict the REST of the day? AUC of ret_so_far vs sign(rest)
      (AUC < 0.5 = the move tends to reverse, > 0.5 = it tends to continue), and mean rest-of-day return after an
      up start vs a down start, net of nothing (signal check only).
Series: liquid-alt equal-weight index, BTC. Period 2024-05..2026-09, reported for the whole sample and 2026.
Also pump trades (+10% 1h trigger, 4h hold): does the first 15/30/60/120 minutes of the trade tell the 4h outcome?
Output data/reports/b27/intraday.{json,md}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b7_lib as L  # noqa: E402

OUT = ROOT / "data/reports/b27"
D = 86400
T0 = int(pd.Timestamp("2024-05-01").timestamp())
Y26 = int(pd.Timestamp("2026-01-01").timestamp())


def curve(r, ts):
    """r: hourly log returns indexed by ts (bar starting at ts). Return rows per h."""
    s = pd.Series(r, index=ts)
    s = s[(s.index >= T0)]
    day = s.index // D; hr = (s.index % D) // 3600
    M = pd.DataFrame({"d": day, "h": hr, "r": s.to_numpy()}).pivot(index="d", columns="h", values="r").dropna()
    cum = M.cumsum(1); full = cum[23]
    out = []
    for h in range(1, 24):
        so_far = cum[h - 1]; rest = full - so_far
        for tag, sel in (("all", np.ones(len(M), bool)), ("2026", M.index.to_numpy() * D >= Y26)):
            a = float(((so_far[sel] > 0) == (full[sel] > 0)).mean())
            auc = float(roc_auc_score((rest[sel] > 0).astype(int), so_far[sel]))
            up, dn = rest[sel][so_far[sel] > 0], rest[sel][so_far[sel] <= 0]
            out.append({"h": h, "kst": f"{(h + 9) % 24:02d}:00", "set": tag, "agree_full_day": a, "auc_rest": auc,
                        "rest_after_up_bp": float(up.mean() * 1e4), "rest_after_down_bp": float(dn.mean() * 1e4), "days": int(sel.sum())})
    return pd.DataFrame(out)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ts, codes, X = L.data()
    bi = codes.index("BTCUSDT")
    U = (np.nan_to_num(X["dv24"]) >= 5e6) & (X["age"] >= 720); U[:, bi] = False
    alt = np.nan_to_num(np.nanmean(np.where(U, X["r1"], np.nan), 1))
    R = {"ALT": curve(alt, ts), "BTC": curve(np.nan_to_num(X["r1"][:, bi]), ts)}
    # pumps from 1m klines are not in the panel; use the hourly panel: first 1h and 2h of the 4h trade
    c = X["c"]; dv = np.nan_to_num(X["dv24"])
    trig = (X["r1"] >= np.log(1.10)) & (dv >= 2e6) & (X["age"] >= 72)
    ti, tj = np.nonzero(trig[:-5])
    p0 = c[ti, tj]; out = {}
    for k in (1, 2, 3):
        early = c[ti + k, tj] / p0 - 1; total = c[ti + 4, tj] / p0 - 1; rest = c[ti + 4, tj] / c[ti + k, tj] - 1
        ok = np.isfinite(early) & np.isfinite(total) & np.isfinite(rest) & (ts[ti] >= T0)
        out[f"{k}h"] = {"n": int(ok.sum()), "agree_4h": float(((early[ok] > 0) == (total[ok] > 0)).mean()),
                        "auc_rest": float(roc_auc_score((rest[ok] > 0).astype(int), early[ok])),
                        "rest_after_up_bp": float(rest[ok][early[ok] > 0].mean() * 1e4), "rest_after_down_bp": float(rest[ok][early[ok] <= 0].mean() * 1e4)}
    json.dump({k: v.to_dict("records") for k, v in R.items()} | {"pumps": out}, open(OUT / "intraday.json", "w"), indent=1)
    Lm = ["# B27: when can you tell the day? (day = 09:00 KST to 09:00 KST)", ""]
    for k, df in R.items():
        Lm += [f"## {k}", "", "| hours in | KST | sign so far = full-day sign | AUC so-far -> rest of day | rest after up start (bp) | rest after down start (bp) | 2026 AUC |", "|---|---|---|---|---|---|---|"]
        a, b = df[df.set == "all"].set_index("h"), df[df.set == "2026"].set_index("h")
        for h in (1, 2, 3, 4, 6, 8, 12, 16, 20, 23):
            r = a.loc[h]
            Lm.append(f"| {h} | {r.kst} | {r.agree_full_day:.0%} | {r.auc_rest:.3f} | {r.rest_after_up_bp:+.0f} | {r.rest_after_down_bp:+.0f} | {b.loc[h].auc_rest:.3f} |")
        Lm.append("")
    Lm += ["## Pump trades (+10% in 1h, then 4h hold)", "", "| after | n | early sign = 4h sign | AUC early -> rest | rest after early up (bp) | rest after early down (bp) |", "|---|---|---|---|---|---|"]
    for k, v in out.items():
        Lm.append(f"| {k} | {v['n']} | {v['agree_4h']:.0%} | {v['auc_rest']:.3f} | {v['rest_after_up_bp']:+.0f} | {v['rest_after_down_bp']:+.0f} |")
    (OUT / "intraday.md").write_text("\n".join(Lm) + "\n")
    print("\n".join(Lm))


if __name__ == "__main__":
    main()
