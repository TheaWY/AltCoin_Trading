"""B17 F12c: clean random null for the F12b 60m-lookback y1 lead.  Registry F12 (follow-up to F12b).
Problem fixed: F12b 'near-miss' hard negatives were chosen as hours that rose 4-10% (outcome-selected). F12c draws negatives WITHOUT looking at the
forward window: ~3,400 random (coin, day) pairs uniform over tradable coin-days 2024-04..2026-09, 3 random minutes per day (>= 2h apart), each with
dv24 >= $2M and >= 72h listed, excluding +-2h of any B15 pump onset (those are positives). ids -200001.. -> data/cache/b17/sample_rand.parquet
Then: backfill_b17.py sec1_rand (1-second bars) -> F12C=1 F12_ONLY=60m:y1 b17_f12.py features/tab/seq/report
(models trained exactly as F12b, random windows excluded from training and only scored; BH + pass on the random null only).
Pre-registered pass: net_real (base rate 0.5%) CI > 0 with >= 20 flagged random windows, BH q=0.10 over the 5 models x 2 horizons."""
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
RNG = np.random.default_rng(17012)
T0, T1 = int(pd.Timestamp("2024-04-01").timestamp()), int(pd.Timestamp("2026-09-20").timestamp())
N_DAYS, PER_DAY = 3400, 3


def sample():
    P = pd.read_parquet(M.C / "b15/pumps.parquet", columns=["code", "ts"])
    on = {c: np.sort(g["ts"].to_numpy()) for c, g in P.groupby("code")}
    codes = sorted({p.stem for d in bp.K for p in d.glob("*USDT.parquet")} - {f"{t}USDT" for t in bp.TRADFI})
    elig = {}                                      # (code, day) -> eligible minute timestamps
    for n_, code in enumerate(codes):
        d = bp.load_minutes(code)
        if d is None or len(d) < 72 * 60 + 1440:
            continue
        t = d.index.to_numpy().astype("int64"); t = t // 10 ** 9 if t.max() > 1e12 else t
        dv24 = pd.Series(d["qv"].to_numpy(np.float64)).rolling(1440, min_periods=720).sum().to_numpy()
        ok = (dv24 >= 2e6) & (np.arange(len(t)) >= 72 * 60) & (t >= T0) & (t <= T1) & (t % 60 == 0)
        o = on.get(code, np.array([], np.int64))
        if len(o):
            k = np.searchsorted(o, t); near = np.minimum(np.abs(t - o[np.clip(k, 0, len(o) - 1)]), np.abs(t - o[np.clip(k - 1, 0, len(o) - 1)]))
            ok &= near > 2 * 3600
        idx = np.flatnonzero(ok)
        for day, g in pd.Series(idx).groupby(t[idx] // 86400):
            if len(g) >= 600:
                elig[(code, int(day))] = (t[g.to_numpy()], dv24[g.to_numpy()])
        if n_ % 100 == 0:
            print("coins", n_, len(codes), "coin-days", len(elig), flush=True)
    keys = list(elig); pick = RNG.choice(len(keys), min(N_DAYS, len(keys)), replace=False)
    rows = []
    for j in pick:
        code, day = keys[j]; tt, dv = elig[(code, day)]; chosen = []
        for i in RNG.permutation(len(tt)):
            if all(abs(tt[i] - c) >= 2 * 3600 for c in chosen):
                chosen.append(int(tt[i])); rows.append(dict(code=code, ts=int(tt[i]), dv24=float(dv[i])))
            if len(chosen) == PER_DAY:
                break
    S = pd.DataFrame(rows).sort_values(["code", "ts"]).reset_index(drop=True)
    S["pump_id"] = -(200001 + np.arange(len(S))); S["hold"] = S["ts"] >= M.DISC1; S["size_bucket"] = "rand"
    S.to_parquet(B / "sample_rand.parquet", index=False)
    print("random windows", len(S), "coin-days", S.assign(d=S.ts // 86400).groupby(["code", "d"]).ngroups, "coins", S["code"].nunique(),
          "val share", round(float((S["ts"] >= 1767225600).mean()), 3), flush=True)


if __name__ == "__main__":
    {"sample": sample}[sys.argv[1]]()
