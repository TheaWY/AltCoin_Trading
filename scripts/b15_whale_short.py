"""B15_6 (registered 2026-09-28 before running): short WHALE-DRIVEN pumps only AFTER the peak is confirmed.
Question from 유리: whale-driven onsets fall -12.5% from peak (vs -7.9% crowd) -> can a short capture it?
  Entry rule (identical to B15_5, no model, nothing tuned): first minute >= 3 min after onset where price is >= 3% below the
  running high since onset; short at the next minute close; exit when price retraces 50% of the pump (base = m0-60) or at +24h.
  Cost = b15_models cost (fees + liquidity slippage). Also records max adverse excursion (how far it ran against the short).
  Whale-driven = top tercile of onset concentration (mean rank of big_ratio and gini), ranks and cutoffs fit on all discovery
  tick samples (same as B15_3b). Crowd-driven = bottom tercile.
Tests on the NEW holdout pumps of B15_3b (never used for this question), BH q=0.10 over 2:
  P1 whale-driven post-peak short: mean net > 0 (day-clustered CI)
  P2 whale minus crowd short net > 0
  pass: P1 passes BH AND no size bucket / regime slice with CI < 0 AND 90th pct adverse excursion reported
Secondary: pooled holdout (original + new), discovery.
  .venv/bin/python -W ignore scripts/b15_whale_short.py   -> data/reports/b15/b15_6.json"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b15_models as M  # noqa: E402
import b15_tick as T  # noqa: E402

RNG = np.random.default_rng(156)


def post_peak_short(P):
    paths = np.load(M.C / "b15/paths.npy", mmap_mode="r")
    rows = []
    for pid in P["pump_id"]:
        cr = np.asarray(paths[pid][:, 0], np.float64)
        base = cr[0]
        runmax = np.maximum.accumulate(cr[60:])
        dd3 = np.flatnonzero(((1 + cr[60:]) / (1 + runmax) - 1) <= -0.03)
        dd3 = dd3[dd3 >= 3]
        if len(dd3) == 0 or dd3[0] + 1 >= len(cr) - 61:
            continue
        tk = dd3[0]
        level = base + 0.5 * (runmax[tk] - base)
        entry = cr[60 + tk + 1]
        fut = cr[60 + tk + 1:]
        hit = np.flatnonzero(fut <= level)
        path = fut[:hit[0] + 1] if len(hit) else fut
        exit_px = level if len(hit) else fut[-1]
        rows.append(dict(pump_id=int(pid), trig_min=int(tk), gross_short=float(-((1 + exit_px) / (1 + entry) - 1)),
                         mae=float((1 + np.nanmax(path)) / (1 + entry) - 1), hit50=int(len(hit) > 0)))
    return pd.DataFrame(rows)


def boot_p(x, days, reps=4000):
    d = pd.DataFrame({"x": x, "d": days}).groupby("d")["x"].agg(["sum", "count"])
    su, cn = d["sum"].to_numpy(), d["count"].to_numpy()
    b = np.array([su[k].sum() / cn[k].sum() for k in (RNG.integers(0, len(su), len(su)) for _ in range(reps))])
    return float((b <= 0).mean())


def main():
    old = T.sample(False).merge(pd.read_parquet(T.FEAT0), on="pump_id").assign(src="orig")
    new = T.sample(True).merge(pd.read_parquet(T.ROOT / "data/cache/b15/tick_features_confirm.parquet"), on="pump_id").assign(src="new")
    A = pd.concat([old, new], ignore_index=True)
    D = A[~A["hold"]]
    ref = {c: np.sort(D[c].dropna().to_numpy()) for c in ("big_ratio", "gini")}
    A["conc"] = np.mean([np.searchsorted(ref[c], A[c].to_numpy()) / len(ref[c]) for c in ref], 0)
    q1, q2 = np.quantile(A.loc[~A["hold"], "conc"], [1 / 3, 2 / 3])
    A["grp"] = np.select([A["conc"] >= q2, A["conc"] < q1], ["whale", "crowd"], "middle")
    E = post_peak_short(A).merge(A, on="pump_id")
    E["net"] = E["gross_short"] - E["cost"]
    res = {"trigger_rate": float(len(E) / len(A))}
    for name, H in (("primary_new_holdout", E[E["hold"] & (E["src"] == "new")]), ("pooled_holdout", E[E["hold"]]),
                    ("discovery", E[~E["hold"]])):
        r = {}
        for g in ("whale", "middle", "crowd"):
            x = H[H["grp"] == g]
            r[g] = dict(n=int(len(x)), mean=float(x["net"].mean()), median=float(x["net"].median()),
                        ci=M.day_ci(x["net"].to_numpy(), x["day"].to_numpy()), hit50=float(x["hit50"].mean()),
                        win_rate=float((x["net"] > 0).mean()), mae_p50=float(x["mae"].median()), mae_p90=float(x["mae"].quantile(0.9)),
                        mae_p99=float(x["mae"].quantile(0.99)))
        w, c = H[H["grp"] == "whale"], H[H["grp"] == "crowd"]
        diff = np.r_[w["net"].to_numpy() - c["net"].mean()]
        r["whale_minus_crowd"] = float(w["net"].mean() - c["net"].mean())
        r["whale_minus_crowd_ci"] = M.day_ci(diff, w["day"].to_numpy())
        if name == "primary_new_holdout":
            pv = {"P1": boot_p(w["net"].to_numpy(), w["day"].to_numpy()), "P2": boot_p(diff, w["day"].to_numpy())}
            names = sorted(pv, key=pv.get)
            last = max([k for k, nm in enumerate(names) if pv[nm] <= 0.10 * (k + 1) / len(names)], default=-1)
            r["p"] = pv
            r["bh_pass"] = {nm: k <= last for k, nm in enumerate(names)}
            r["slices_whale"] = M.slices(w, "net")
            bad = [f"{a}:{b}" for a, d in r["slices_whale"].items() for b, v in d.items() if v["ci"][1] < 0]
            r["pass"] = bool(r["bh_pass"]["P1"] and not bad)
        res[name] = r
    json.dump(res, open(M.OUT / "b15_6.json", "w"), indent=1, default=float)
    print(json.dumps({k: ({kk: vv for kk, vv in v.items() if kk != "slices_whale"} if isinstance(v, dict) else v) for k, v in res.items()},
                     indent=1, default=float))


if __name__ == "__main__":
    main()
