"""B2_G1: Gu-Kelly-Xiu style neural net (NN3: 32-16-8) on daily cross-sectional characteristics.

Daily 00 UTC, coins with 24h volume >= $20M. 14 characteristics ranked within the day (to [-1, 1]).
Target: within-day rank of the next 24h return. Train 2025-03..11, validate 2025-12 (early stopping on rank IC),
test 2026-01..09: long top 10 / short bottom 10, hold 24h, net of fees+slippage (close-to-close proxy).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from b2_hypotheses import FEE, slip  # noqa: E402
from b2_models import boot_day, DEV  # noqa: E402

F = ["ret_1h", "ret_4h", "ret_24h", "ret_7d", "ret_28d", "rv24", "rv_7d", "max_7d", "dv24", "taker_1h", "taker_24h",
     "vsurge", "dhi30", "fund24"]
TR_END = int(pd.Timestamp("2025-12-01").timestamp())
VA_END = int(pd.Timestamp("2026-01-01").timestamp())


def main() -> int:
    P = pd.read_parquet(ROOT / "data/cache/b2_hourly.parquet", columns=["code", "ts", "fwd_24h", "age_h"] + F)
    P = P[(P["ts"] % 86400 == 0) & (P["dv24"] >= 2e7) & (P["age_h"] >= 72) & P["fwd_24h"].notna()]
    g = P.groupby("ts")
    X = np.stack([(g[f].rank(pct=True).fillna(0.5) * 2 - 1).to_numpy(np.float32) for f in F], 1)
    y = (g["fwd_24h"].rank(pct=True) * 2 - 1).to_numpy(np.float32)
    ts = P["ts"].to_numpy()
    tr, va, te = ts < TR_END, (ts >= TR_END) & (ts < VA_END), ts >= VA_END

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
    for ep in range(100):
        net.train()
        perm = torch.randperm(len(Xt))
        for k in range(0, len(Xt), 512):
            i = perm[k:k + 512]
            opt.zero_grad()
            loss = ((net(Xt[i].to(DEV)).squeeze(-1) - yt[i].to(DEV)) ** 2).mean()
            loss.backward()
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
    with torch.no_grad():
        pte = net(torch.tensor(X[te]).to(DEV)).squeeze(-1).cpu().numpy()
    T = P[te].assign(p=pte)
    T["rk"] = T.groupby("ts")["p"].rank(ascending=False, method="first")
    T["rk2"] = T.groupby("ts")["p"].rank(ascending=True, method="first")
    L, S = T[T["rk"] <= 10], T[T["rk2"] <= 10]
    cost = lambda d: 2 * (FEE + slip(d["dv24"].to_numpy()))  # noqa: E731
    net_l = L["fwd_24h"].to_numpy() - cost(L)
    net_s = -S["fwd_24h"].to_numpy() - cost(S)
    daily = pd.concat([pd.Series(net_l, index=L["ts"]), pd.Series(net_s, index=S["ts"])]).groupby(level=0).mean()
    lo, hi = boot_day(daily.to_numpy(), daily.index.to_numpy())
    out = dict(id="B2_G1_xs_mlp", val_ric=best, test_ric=ric(pte, te), test_days=len(daily),
               mean_daily_net=daily.mean(), ci_lo=lo, ci_hi=hi, long_net=net_l.mean(), short_net=net_s.mean())
    print(out)
    pd.DataFrame([out]).to_csv(ROOT / "data/reports/b2/g1.csv", index=False)
    led = pd.read_csv(ROOT / "research/trial_ledger.csv")
    led = pd.concat([led, pd.DataFrame([dict(ts=time.strftime("%Y-%m-%dT%H:%M:%S"), run_id=f"B2G-{int(time.time())}",
                                             hypothesis="B2_G1_xs_mlp", tier="confirmatory", period="test 2026-01..09",
                                             mean_daily_net=out["mean_daily_net"], n_trades=len(net_l) + len(net_s),
                                             note=f"test_ric={out['test_ric']:.4f}")])], ignore_index=True)
    led.to_csv(ROOT / "research/trial_ledger.csv", index=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
