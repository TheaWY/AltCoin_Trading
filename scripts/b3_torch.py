"""B3 torch stage.
default : retrain M4 (1D-CNN) and M7 (chart-image CNN) exactly as in B2 on the 2025 pump train split
          (early stopping on Dec-2025), predict Dec-2025 validation and the 2024 holdout pump events.
--g1    : retrain the B2_G1 NN3 cross-sectional model the same way and apply it frozen to 2024 daily cross-sections.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from b2_models import CNN1D, CNN2D, DEV, fit_torch, predict  # noqa: E402

TR_END = int(pd.Timestamp("2025-12-01").timestamp())
VA_END = int(pd.Timestamp("2026-01-01").timestamp())
FEE = 0.0005


def slip(dv):
    dv = np.asarray(dv, float)
    return np.select([dv > 1e8, dv > 2e7, dv > 5e6], [0.0002, 0.0005, 0.0010], 0.0020)


def seqprep(seq):
    s = np.clip(np.nan_to_num(seq), -1, 5).copy()
    s[:, :3] = np.clip(s[:, :3] * 20, -5, 5)
    return s


def pump() -> None:
    z = np.load(ROOT / "data/cache/b2_ds_pump.npz", allow_pickle=True)
    h = np.load(ROOT / "data/cache/b2_ds_pump_2024.npz", allow_pickle=True)
    ts = z["ts"]
    y = (z["gross"] > 0).astype(np.float32)
    tr, va = ts < TR_END, (ts >= TR_END) & (ts < VA_END)
    s, s24 = seqprep(z["seq"]), seqprep(h["seq"])
    im, im24 = z["img"].astype(np.float32)[:, None] / 255.0, h["img"].astype(np.float32)[:, None] / 255.0
    torch.manual_seed(7)
    m4 = fit_torch(CNN1D(), s[tr], y[tr], s[va], y[va])
    torch.manual_seed(7)
    m7 = fit_torch(CNN2D(), im[tr], y[tr], im[va], y[va], epochs=25, bs=128)
    np.savez(ROOT / "data/cache/b3_torch_preds.npz", M4_va=predict(m4, s[va]), M4_24=predict(m4, s24),
             M7_va=predict(m7, im[va]), M7_24=predict(m7, im24))


F = ["ret_1h", "ret_4h", "ret_24h", "ret_7d", "ret_28d", "rv24", "rv_7d", "max_7d", "dv24", "taker_1h", "taker_24h",
     "vsurge", "dhi30", "fund24"]


def xs(path):
    P = pd.read_parquet(path, columns=["code", "ts", "fwd_24h", "age_h"] + F)
    P = P[(P["ts"] % 86400 == 0) & (P["dv24"] >= 2e7) & (P["age_h"] >= 72) & P["fwd_24h"].notna()]
    g = P.groupby("ts")
    X = np.stack([(g[f].rank(pct=True).fillna(0.5) * 2 - 1).to_numpy(np.float32) for f in F], 1)
    y = (g["fwd_24h"].rank(pct=True) * 2 - 1).to_numpy(np.float32)
    return P, X, y


def g1() -> None:
    P, X, y = xs(ROOT / "data/cache/b2_hourly.parquet")
    ts = P["ts"].to_numpy()
    tr, va = ts < TR_END, (ts >= TR_END) & (ts < VA_END)

    def ric(pred, mask):
        d = pd.DataFrame({"t": ts[mask], "p": pred, "y": y[mask]})
        return d.groupby("t").apply(lambda z: z["p"].corr(z["y"], method="spearman")).mean()

    torch.manual_seed(7)
    net = nn.Sequential(nn.Linear(len(F), 32), nn.ReLU(), nn.Linear(32, 16), nn.ReLU(), nn.Linear(16, 8), nn.ReLU(),
                        nn.Linear(8, 1)).to(DEV)
    opt = torch.optim.Adam(net.parameters(), lr=1e-3, weight_decay=1e-4)
    Xt, yt = torch.tensor(X[tr]), torch.tensor(y[tr])
    Xv = torch.tensor(X[va]).to(DEV)
    best, state, bad = -9, None, 0
    for _ in range(100):
        net.train()
        perm = torch.randperm(len(Xt))
        for k in range(0, len(Xt), 512):
            i = perm[k:k + 512]
            opt.zero_grad()
            ((net(Xt[i].to(DEV)).squeeze(-1) - yt[i].to(DEV)) ** 2).mean().backward()
            opt.step()
        net.eval()
        with torch.no_grad():
            r = ric(net(Xv).squeeze(-1).cpu().numpy(), va)
        if r > best:
            best, bad, state = r, 0, {k: v.clone() for k, v in net.state_dict().items()}
        else:
            bad += 1
            if bad >= 10:
                break
    net.load_state_dict(state)
    Q, X24, _ = xs(ROOT / "data/cache/b2_hourly_2024.parquet")
    with torch.no_grad():
        p = net(torch.tensor(X24).to(DEV)).squeeze(-1).cpu().numpy()
    Q = Q.assign(p=p)
    Q["rk"] = Q.groupby("ts")["p"].rank(ascending=False, method="first")
    Q["rk2"] = Q.groupby("ts")["p"].rank(ascending=True, method="first")
    L, S = Q[Q["rk"] <= 10], Q[Q["rk2"] <= 10]
    rows = pd.concat([pd.DataFrame({"ts": L["ts"], "net": L["fwd_24h"] - 2 * (FEE + slip(L["dv24"]))}),
                      pd.DataFrame({"ts": S["ts"], "net": -S["fwd_24h"] - 2 * (FEE + slip(S["dv24"]))})])
    rows.to_parquet(ROOT / "data/cache/b3_g1_2024.parquet", index=False)
    print("G1 val ric", best, flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--g1":
        g1()
    else:
        pump()
