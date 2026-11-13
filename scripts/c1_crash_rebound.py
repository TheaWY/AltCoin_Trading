"""C1 market-wide crash rebound (research/batch_C1.yaml): 2024 holdout + in-sample reference, hourly-close net24."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lossml_data import FEE, panel, slip  # noqa: E402
from lossml_feat import cooldown  # noqa: E402

H_, D_ = 3600, 86400
RNG = np.random.default_rng(3)


def ci_day(x, day, reps=5000):
    u, inv = np.unique(day, return_inverse=True)
    s, c = np.bincount(inv, x), np.bincount(inv)
    b = [s[i].sum() / c[i].sum() for i in (RNG.integers(0, len(u), len(u)) for _ in range(reps))]
    return np.percentile(b, [2.5, 97.5]).round(4).tolist()


def main():
    P = panel()
    out = []
    for code, g in P.groupby("code", sort=False):
        g = g.reset_index(drop=True)
        ts, c = g["ts"].to_numpy(), g["c"].to_numpy(float)
        f24 = g["fund24"].to_numpy(float)
        n = len(g)
        fwd = np.full(n, np.nan)
        ok = np.zeros(n, bool)
        ok[:-24] = ts[24:] - ts[:-24] == 24 * H_
        fwd[:-24] = c[24:] / c[:-24] - 1
        fn = np.zeros(n)
        fn[:-24] = np.nan_to_num(f24[24:]) * 3
        m = (g["ret_24h"] <= -0.25) & (g["dv24"] >= 2e6) & (g["age_h"] >= 72) & ok
        e = g.loc[m, ["code", "ts", "btc_ret_24h", "dlo30", "dv24"]].copy()
        e["net24"] = fwd[m.to_numpy()] - 2 * (FEE + slip(e["dv24"])) - fn[m.to_numpy()]
        out.append(e)
    E = cooldown(pd.concat(out, ignore_index=True))
    E["c1"] = (E["btc_ret_24h"] <= -0.03) & (E["dlo30"] <= 0)
    E["day"] = E["ts"] // D_
    periods = {"holdout_2024-04..11": ("2024-04-01", "2024-12-01"), "insample_2024-12..2026-09": ("2024-12-01", "2026-09-25")}
    for name, (a, b) in periods.items():
        S = E[(E["ts"] >= pd.Timestamp(a).timestamp()) & (E["ts"] < pd.Timestamp(b).timestamp())]
        for arm, g in (("C1", S[S["c1"]]), ("control", S[~S["c1"]])):
            if len(g) == 0:
                print(name, arm, "n=0")
                continue
            top = g.groupby("day").size().sort_values(ascending=False).head(5).index
            h = g[~g["day"].isin(top)]
            print(f"{name:28s} {arm:8s} n={len(g):5d} days={g['day'].nunique():4d} mean={g['net24'].mean():+.4f} "
                  f"median={g['net24'].median():+.4f} win={(g['net24'] > 0).mean():.2f} ci={ci_day(g['net24'].to_numpy(), g['day'].to_numpy())} "
                  f"ex-top5-days n={len(h)} mean={h['net24'].mean() if len(h) else float('nan'):+.4f}")
    H = E[(E["ts"] >= pd.Timestamp("2024-04-01").timestamp()) & (E["ts"] < pd.Timestamp("2024-12-01").timestamp()) & E["c1"]]
    print(H.assign(date=pd.to_datetime(H["ts"], unit="s")).groupby(pd.to_datetime(H["ts"], unit="s").dt.date)["net24"].agg(["size", "mean"]).round(4).to_string())


if __name__ == "__main__":
    main()
