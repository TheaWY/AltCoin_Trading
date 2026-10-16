"""Batch B4 (research/batch_B4.yaml): calendar / market-structure hypotheses over 2024-03..2026-09."""
from __future__ import annotations

import os

os.environ["B2_ERA"] = "all"

import math  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from concurrent.futures import ProcessPoolExecutor  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from b2_panel import load_funding  # noqa: E402
from b2_hypotheses import sim_code  # noqa: E402
from b3_run import boot_day  # noqa: E402

T0 = int(pd.Timestamp("2024-03-08").timestamp())
T1 = int(pd.Timestamp("2026-09-22").timestamp())
Y1 = int(pd.Timestamp("2025-03-01").timestamp())
Y2 = int(pd.Timestamp("2026-01-01").timestamp())
H_, D_ = 3600, 86400
LEDGER = ROOT / "research/trial_ledger.csv"
OUTD = ROOT / "data/reports/b4"


def panel() -> pd.DataFrame:
    cols = ["code", "ts", "dv24", "age_h", "fund24", "ret_1h", "c"]
    a = pd.read_parquet(ROOT / "data/cache/b2_hourly_2024.parquet", columns=cols)
    b = pd.read_parquet(ROOT / "data/cache/b2_hourly.parquet", columns=cols)
    P = pd.concat([a[a["ts"] < Y1], b[b["ts"] >= Y1]], ignore_index=True).sort_values(["code", "ts"])
    P["ret_8h"] = P.groupby("code")["c"].transform(lambda s: s / s.shift(8) - 1)
    return P[(P["ts"] >= T0) & (P["ts"] <= T1) & (P["age_h"] >= 72) & (P["dv24"] >= 2e6)]


def eight_hour_funding(codes) -> dict[str, set]:
    out = {}
    for c in codes:
        f = load_funding(c)
        ts = f["ts"].to_numpy()
        ok = ts[1:][(np.diff(ts) == 8 * H_) & (ts[1:] % (8 * H_) == 0)]
        out[c] = set(int(x) for x in ok)
    return out


def main() -> int:
    led = pd.read_csv(LEDGER)
    if led["hypothesis"].astype(str).str.startswith("B4_").any():
        print("B4 already run; refusing")
        return 1
    P = panel()
    fund_ts = eight_hour_funding(P["code"].unique())
    rows = []
    # funding-time hypotheses
    pre = P[(P["ts"] + H_) % (8 * H_) == 0]
    pre = pre[[int(t) + H_ in fund_ts.get(c, ()) for c, t in zip(pre["code"], pre["ts"])]]
    post = P[P["ts"] % (8 * H_) == 0]
    post = post[[int(t) in fund_ts.get(c, ()) for c, t in zip(post["code"], post["ts"])]]
    for hid, d, side in (("B4_C1_fundpos_pre_short", pre[pre["fund24"] >= 0.0005], "S"),
                         ("B4_C2_fundpos_post_long", post[post["fund24"] >= 0.0005], "L"),
                         ("B4_C3_fundneg_pre_long", pre[pre["fund24"] <= -0.0005], "L"),
                         ("B4_C4_fundneg_post_short", post[post["fund24"] <= -0.0005], "S")):
        rows.append(d[["code", "ts", "dv24"]].assign(hid=hid, side=side, hold=60))
    wk = P[(P["ts"] % D_ == 20 * H_) & ((((P["ts"] // D_) + 3) % 7) == 4) & (P["code"] != "BTCUSDT")]
    wk = wk.sort_values(["ts", "dv24"], ascending=[True, False]).groupby("ts").head(10)
    rows.append(wk[["code", "ts", "dv24"]].assign(hid="B4_C5_weekend_long", side="L", hold=52 * 60))
    dt = pd.to_datetime(P["ts"], unit="s")
    tom = P[(P["ts"] % D_ == 20 * H_) & ((dt + pd.Timedelta(hours=4)).dt.day == 1) & (P["code"] != "BTCUSDT")]
    tom = tom.sort_values(["ts", "dv24"], ascending=[True, False]).groupby("ts").head(10)
    rows.append(tom[["code", "ts", "dv24"]].assign(hid="B4_C7_turn_of_month_long", side="L", hold=28 * 60))
    ko = P[(P["ts"] % D_ == 0) & (P["dv24"] >= 2e7) & (P["code"] != "BTCUSDT")].dropna(subset=["ret_8h"])
    ko = ko.sort_values(["ts", "ret_8h"], ascending=[True, False]).groupby("ts").head(5)
    rows.append(ko[["code", "ts", "dv24"]].assign(hid="B4_C9_korea_open_fade", side="S", hold=240))
    rows.append(ko[["code", "ts", "dv24"]].assign(hid="B4_C10_korea_open_follow", side="L", hold=240))
    T = pd.concat(rows, ignore_index=True)
    print(T["hid"].value_counts().to_string(), flush=True)
    data_end = T1 + 8 * D_
    res = []
    with ProcessPoolExecutor(8) as ex:
        for r in ex.map(sim_code, [(c, g.to_dict("records"), data_end) for c, g in T.groupby("code")], chunksize=2):
            res += r
    X = pd.DataFrame(res)
    OUTD.mkdir(parents=True, exist_ok=True)
    X.to_parquet(OUTD / "trades.parquet", index=False)
    out = []
    for hid, g in X.groupby("hid"):
        lo, hi, p = boot_day(g["net"].to_numpy(), (g["ts"] // D_).to_numpy())
        y = [g[(g["ts"] >= a) & (g["ts"] < b)]["net"].mean() for a, b in ((T0, Y1), (Y1, Y2), (Y2, T1 + D_))]
        out.append(dict(id=hid, n=len(g), gross=g["gross"].mean(), mean=g["net"].mean(), hit=(g["net"] > 0).mean(),
                        ci_lo=lo, ci_hi=hi, p=p, y2024=y[0], y2025=y[1], y2026=y[2]))
    S = pd.DataFrame(out)
    ps = S["p"].to_numpy()
    o = np.argsort(ps)
    q = ps[o] * len(ps) / (np.arange(len(ps)) + 1)
    q = np.minimum.accumulate(q[::-1])[::-1]
    qq = np.empty_like(ps)
    qq[o] = np.minimum(q, 1)
    S["q"] = qq
    S["pass"] = (S["q"] < 0.05) & (S["mean"] > 0) & (S[["y2024", "y2025", "y2026"]] > 0).all(axis=1)
    S.to_csv(OUTD / "summary.csv", index=False)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    add = pd.DataFrame([dict(ts=now, run_id=f"B4-{int(time.time())}", hypothesis=r["id"], tier="confirmatory",
                             period="2024-03..2026-09", mean_daily_net=r["mean"], n_trades=r["n"], note="per-trade mean")
                        for r in out])
    pd.concat([led, add], ignore_index=True).to_csv(LEDGER, index=False)
    print(S.round(4).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
