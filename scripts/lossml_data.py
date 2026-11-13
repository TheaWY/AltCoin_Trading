"""B6 loss-prediction dataset (research/ml_design.md).

Rows: every coin at 00/08/16 UTC, 2024-03..2026-09, 24h volume >= $2M, >= 72h listed, x both directions.
Features are signed by trade direction where direction matters. Labels: loss24 (net after fees, slippage, funding
over a 24h hold < 0) and triple-barrier loss (stop -1 vol / target +1.5 vol / 24h).
Out: data/cache/lossml_tab.parquet, data/cache/lossml_seq.npy (+ index in the parquet)
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

ROOT = Path(__file__).resolve().parents[1]
H_, D_ = 3600, 86400
Y1 = int(pd.Timestamp("2025-03-01").timestamp())
FEE = 0.0005
SEQ = 48
COLS = ["code", "ts", "c", "hi", "lo", "qv", "ret_1h", "ret_4h", "ret_24h", "ret_7d", "ret_28d", "rv24", "rv_7d",
        "max_7d", "dv24", "taker_1h", "taker_24h", "vsurge", "dhi30", "dlo30", "range_1h", "upwick_1h", "pumps_30d",
        "age_h", "fund24", "btc_ret_1h", "btc_ret_24h", "breadth_pump", "hour", "weekday"]
SIGNED = ["ret_1h", "ret_4h", "ret_24h", "ret_7d", "ret_28d", "tk1", "tk24", "dhi30", "dlo30", "fund24",
          "btc_ret_1h", "btc_ret_24h", "xs_ret24"]
UNSIGNED = ["rv24", "rv_7d", "max_7d", "ldv", "vsurge", "range_1h", "upwick_1h", "pumps_30d", "lage",
            "breadth_pump", "hour_s", "hour_c", "weekday", "xs_rv", "xs_dv"]
FEATURES = [f"s_{c}" for c in SIGNED] + UNSIGNED + ["side"]


def slip(dv):
    dv = np.asarray(dv, float)
    return np.select([dv > 1e8, dv > 2e7, dv > 5e6], [0.0002, 0.0005, 0.0010], 0.0020)


def panel() -> pd.DataFrame:
    a = pd.read_parquet(ROOT / "data/cache/b2_hourly_2024.parquet", columns=COLS)
    b = pd.read_parquet(ROOT / "data/cache/b2_hourly.parquet", columns=COLS)
    return pd.concat([a[a["ts"] < Y1], b[b["ts"] >= Y1]], ignore_index=True).sort_values(["code", "ts"]).reset_index(drop=True)


def main() -> None:
    P = panel()
    rows, seqs = [], []
    for code, g in P.groupby("code", sort=False):
        g = g.reset_index(drop=True)
        ts = g["ts"].to_numpy()
        n = len(g)
        if n < 24 * 5:
            continue
        c, hi, lo = (g[k].to_numpy(float) for k in ("c", "hi", "lo"))
        contiguous_fwd = np.zeros(n, bool)
        contiguous_fwd[:-24] = (ts[24:] - ts[:-24]) == 24 * H_
        fwd = np.full(n, np.nan)
        fwd[:-24] = c[24:] / c[:-24] - 1
        fund_next = np.full(n, np.nan)
        f24 = g["fund24"].to_numpy(float)
        fund_next[:-24] = f24[24:] * 3                      # 8h-normalised trailing mean -> 24h sum
        hw = np.full((n, 24), np.nan)
        lw = np.full((n, 24), np.nan)
        hw[:-24] = sliding_window_view(hi[1:], 24)[: n - 24]
        lw[:-24] = sliding_window_view(lo[1:], 24)[: n - 24]
        pick = (ts % (8 * H_) == 0) & contiguous_fwd & (g["dv24"].to_numpy() >= 2e6) & (g["age_h"].to_numpy() >= 72)
        idx = np.flatnonzero(pick & (np.arange(n) >= SEQ))
        if len(idx) == 0:
            continue
        sig = np.clip(np.nan_to_num(g["rv24"].to_numpy(float), nan=0.05), 0.01, 1.0)
        # triple barrier for both sides (hourly bars; same-bar hit counts as stop)
        up_l = hw[idx] >= (c[idx] * (1 + 1.5 * sig[idx]))[:, None]
        dn_l = lw[idx] <= (c[idx] * (1 - 1.0 * sig[idx]))[:, None]
        up_s = lw[idx] <= (c[idx] * (1 - 1.5 * sig[idx]))[:, None]
        dn_s = hw[idx] >= (c[idx] * (1 + 1.0 * sig[idx]))[:, None]

        def first(m):
            return np.where(m.any(1), m.argmax(1), 99)
        tb_loss_l = (first(dn_l) <= first(up_l)) & (first(dn_l) < 99)
        tb_loss_s = (first(dn_s) <= first(up_s)) & (first(dn_s) < 99)
        none_l = (first(dn_l) == 99) & (first(up_l) == 99)
        none_s = (first(dn_s) == 99) & (first(up_s) == 99)
        r1 = g["ret_1h"].to_numpy(float)
        lq = np.log1p(g["qv"].to_numpy(float))
        tk = g["taker_1h"].to_numpy(float) - 0.5
        rg = g["range_1h"].to_numpy(float)
        for k, i in enumerate(idx):
            s = slice(i - SEQ + 1, i + 1)
            seqs.append(np.stack([r1[s], lq[s] - np.nanmean(lq[s]), tk[s], rg[s]]).astype(np.float16))
        base = g.iloc[idx].copy()
        base["fwd24"] = fwd[idx]
        base["fund_next"] = fund_next[idx]
        base["tb_loss_L"] = np.where(none_l, np.nan, tb_loss_l.astype(float))
        base["tb_loss_S"] = np.where(none_s, np.nan, tb_loss_s.astype(float))
        rows.append(base)
    B = pd.concat(rows, ignore_index=True)
    B["seq_idx"] = np.arange(len(B))
    np.save(ROOT / "data/cache/lossml_seq.npy", np.stack(seqs))
    # cross-sectional ranks at each timestamp
    gts = B.groupby("ts")
    B["xs_ret24"] = gts["ret_24h"].rank(pct=True) - 0.5
    B["xs_rv"] = gts["rv24"].rank(pct=True)
    B["xs_dv"] = gts["dv24"].rank(pct=True)
    B["tk1"] = B["taker_1h"] - 0.5
    B["tk24"] = B["taker_24h"] - 0.5
    B["ldv"] = np.log1p(B["dv24"])
    B["lage"] = np.log1p(B["age_h"])
    B["hour_s"] = np.sin(2 * np.pi * B["hour"] / 24)
    B["hour_c"] = np.cos(2 * np.pi * B["hour"] / 24)
    B["cost"] = 2 * (FEE + slip(B["dv24"]))
    fund = B["fund_next"].fillna(0)
    out = []
    for side in (1, -1):
        X = pd.DataFrame({"code": B["code"], "ts": B["ts"], "seq_idx": B["seq_idx"], "side": side})
        for c in SIGNED:
            X[f"s_{c}"] = (side * B[c]).astype(np.float32)
        for c in UNSIGNED:
            X[c] = B[c].astype(np.float32)
        X["net24"] = (side * B["fwd24"] - B["cost"] - side * fund).astype(np.float32)
        X["loss24"] = (X["net24"] < 0).astype(np.int8)
        X["tb_loss"] = B["tb_loss_L"] if side == 1 else B["tb_loss_S"]
        out.append(X)
    T = pd.concat(out, ignore_index=True)
    T.to_parquet(ROOT / "data/cache/lossml_tab.parquet", index=False)
    print(T.shape, T["loss24"].mean(), pd.to_datetime(T["ts"].min(), unit="s"), pd.to_datetime(T["ts"].max(), unit="s"))


if __name__ == "__main__":
    main()
