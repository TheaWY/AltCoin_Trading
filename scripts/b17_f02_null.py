"""B17 F02 part B (registered 2026-09-28 before download): false-positive windows for the onset triggers.
Part A (b17_f02.py) conditions on a pump happening in the window, so it measures timing, not tradability. Part B samples, for the SAME
coins and the same period mix, 1-hour windows with NO pump onset within +-24h (universe dv24 >= $2M, listed >= 72h), downloads the same
1-second bars, runs the same triggers with the same baseline rule, and for every firing simulates the same entries/exits.
Verdict per cell: pooled net over (pump-window firings + null-window firings) weighted by the real base rate
(pumps per coin-hour in the universe, ~0.5%), i.e. net_real = p * net_pump + (1-p) * net_null where p = P(pump | trigger fired) estimated from
fire rates: p = base_rate * fire_pump / (base_rate * fire_pump + (1-base_rate) * fire_null). Pass = net_real CI > 0 and MAE p90 < 15%.
  .venv/bin/python scripts/b17_f02_null.py sample     -> data/cache/b17/sample_null.parquet (ids -1..-N, 1 per pump in the sample, same coin, random hour)
  .venv/bin/python scripts/backfill_b17.py sec1_null  -> data/cache/b17/sec1_null/{-id}.parquet
  .venv/bin/python scripts/b17_f02_null.py run        -> data/reports/b17/f02_null.json"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b15_models as M  # noqa: E402
import b17_f02 as F  # noqa: E402

B = ROOT / "data/cache/b17"
RNG = np.random.default_rng(1704)
BASE_RATE = 0.005


def sample():
    S = pd.read_parquet(B / "sample.parquet")
    P = pd.read_parquet(M.C / "b15/pumps.parquet", columns=["code", "ts"])
    on = {c: np.sort(g["ts"].to_numpy()) for c, g in P.groupby("code")}
    rows = []
    for k, (pid, code, ts) in enumerate(zip(S["pump_id"], S["code"], S["ts"])):
        t = on[code]
        for _ in range(50):
            cand = int(ts + RNG.integers(-45, 46) * 86400 + RNG.integers(0, 86400))     # same coin, within +-45 days, random time
            cand -= cand % 60
            i = np.searchsorted(t, cand)
            near = min(abs(t[i] - cand) if i < len(t) else 1e12, abs(t[i - 1] - cand) if i > 0 else 1e12)
            if near > 86400:
                rows.append(dict(pump_id=-(k + 1), code=code, ts=cand, hold=cand >= M.DISC1, size_bucket="null", dv24=S["dv24"].iloc[k]))
                break
    N = pd.DataFrame(rows); N.to_parquet(B / "sample_null.parquet"); print("null windows", len(N))


def run():
    N = pd.read_parquet(B / "sample_null.parquet").set_index("pump_id")
    rows = []
    for pid in N.index:
        fp = B / f"sec1_null/{-pid}.parquet"
        if not fp.exists():
            continue
        d = pd.read_parquet(fp)
        if len(d) != 7500:
            continue
        rows.append(F.triggers_one(int(pid), d, None))
    TRn = pd.DataFrame(rows).set_index("pump_id")
    TRp = pd.read_parquet(B / "f02_triggers.parquet").set_index("pump_id")
    Ep = pd.read_parquet(B / "f02_trades.parquet")
    recs = []
    for pid in TRn.index:
        d = pd.read_parquet(B / f"sec1_null/{-pid}.parquet")
        c = pd.Series(d["c"].to_numpy(float)).ffill().to_numpy(); h = np.where(np.isfinite(d["h"]), d["h"], c); l = np.where(np.isfinite(d["l"]), d["l"], c)
        atr1m = max(np.nanmean((h[:F.R0].reshape(5, 60).max(1) / l[:F.R0].reshape(5, 60).min(1)) - 1), 0.005)
        cost = 2 * (M.L.FEE + float(M.slip(np.array([N.loc[pid, "dv24"]]))[0]))
        ts = int(N.loc[pid, "ts"]); day = ts // 86400; val = ts >= F.VAL0
        mpath = np.zeros(1501)   # no minute continuation for null windows: horizons capped at the 1s window (+60m after "m0")
        for cell in TRp.columns:
            t_fire = int(TRn.loc[pid, cell]) if cell in TRn else -1
            if t_fire < 0:
                continue
            for lat in F.LAT:
                for ex in F.EXITS:
                    if t_fire + lat >= 7499:
                        continue
                    g, mae = F.leg(c, h, l, mpath, c[F.RM0C], atr1m, t_fire + lat, ex)
                    recs.append((pid, cell, lat, ex, g - cost, mae, day, val))
    En = pd.DataFrame(recs, columns=["pump_id", "cell", "lat", "exit", "net", "mae", "day", "val"]).dropna(subset=["net"])
    En.to_parquet(B / "f02_null_trades.parquet", index=False)
    res = {"n_null_windows": int(len(TRn)), "fire_rate_null": {c: float((TRn[c] >= 0).mean()) for c in TRn.columns},
           "fire_rate_pump": {c: float((TRp[c] >= 0).mean()) for c in TRp.columns}, "tests": {}}
    pv = {}
    for (cell, lat, ex), gp in Ep[(Ep["kind"] == "signal") & Ep["val"]].groupby(["cell", "lat", "exit"]):
        gn = En[(En["cell"] == cell) & (En["lat"] == lat) & (En["exit"] == ex) & En["val"]]
        if len(gp) < 30 or len(gn) < 30:
            continue
        fp_, fn_ = res["fire_rate_pump"][cell], res["fire_rate_null"].get(cell, 0)
        p = BASE_RATE * fp_ / (BASE_RATE * fp_ + (1 - BASE_RATE) * fn_) if fn_ > 0 else 1.0
        bo = []
        A = gp.groupby("day")["net"].agg(["sum", "count"]); Bn = gn.groupby("day")["net"].agg(["sum", "count"])
        for _ in range(2000):
            ka = RNG.integers(0, len(A), len(A)); kb = RNG.integers(0, len(Bn), len(Bn))
            bo.append(p * A["sum"].to_numpy()[ka].sum() / A["count"].to_numpy()[ka].sum() + (1 - p) * Bn["sum"].to_numpy()[kb].sum() / Bn["count"].to_numpy()[kb].sum())
        bo = np.array(bo)
        key = f"{cell}|{lat}s|{ex}"
        res["tests"][key] = dict(p_pump_given_fire=float(p), net_pump=float(gp["net"].mean()), net_null=float(gn["net"].mean()), n_null=int(len(gn)),
                                 net_real=float(p * gp["net"].mean() + (1 - p) * gn["net"].mean()), ci=[float(x) for x in np.percentile(bo, [2.5, 97.5])],
                                 p_value=float((bo <= 0).mean()), mae90_null=float(gn["mae"].quantile(0.9)))
        pv[key] = res["tests"][key]["p_value"]
    names = sorted(pv, key=pv.get)
    last = max([k for k, nm in enumerate(names) if pv[nm] <= 0.10 * (k + 1) / len(names)], default=-1)
    res["bh_pass"] = [nm for k, nm in enumerate(names) if k <= last]
    res["pass"] = [k for k in res["bh_pass"] if res["tests"][k]["ci"][0] > 0 and res["tests"][k]["mae90_null"] < 0.15]
    json.dump(res, open(F.OUT / "f02_null.json", "w"), indent=1, default=float)
    print("F02 part B: cells", len(res["tests"]), "PASS", res["pass"])
    print("fire rates pump vs null:", {c: (round(res["fire_rate_pump"][c], 3), round(res["fire_rate_null"].get(c, 0), 3)) for c in TRp.columns})
    for k, v in sorted(res["tests"].items(), key=lambda kv: -kv[1]["net_real"])[:10]:
        print(k, "p(pump|fire)", round(v["p_pump_given_fire"], 3), "net_pump", round(v["net_pump"], 4), "net_null", round(v["net_null"], 4), "real", round(v["net_real"], 4), [round(x, 4) for x in v["ci"]])


if __name__ == "__main__":
    {"sample": sample, "run": run}[sys.argv[1]]()
