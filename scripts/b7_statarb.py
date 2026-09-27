"""B7_C deep residual stat-arb (Guijarro-Ordonez/Pelger/Zanotti adapted): CNN + Transformer over 72h residual paths
-> cross-sectional weights (demeaned, unit gross), trained to maximise net Sharpe with turnover cost, rebalance every 4h.
Train: discovery minus last 90 days; validation (early stopping): last 90 days of discovery; test: holdout. 3 seeds.
  OMP_NUM_THREADS=4 .venv/bin/python -W ignore scripts/b7_statarb.py
Out: data/reports/b7/b7_C.json"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
DIR = ROOT / "data/cache/b7"
OUT = ROOT / "data/reports/b7"
DEV = "mps" if torch.backends.mps.is_available() else "cpu"
D_ = 86400
DISC1 = 1756684800            # 2025-09-01
VAL0 = DISC1 - 90 * D_
HOLD1 = 1790208000            # 2026-09-24


class Net(nn.Module):
    def __init__(self, d=24):
        super().__init__()
        self.conv = nn.Sequential(nn.Conv1d(3, d, 5, padding=2), nn.GELU(), nn.Conv1d(d, d, 5, padding=2), nn.GELU())
        self.pos = nn.Parameter(torch.zeros(1, 72, d))
        self.tf = nn.TransformerEncoder(nn.TransformerEncoderLayer(d, 4, 64, 0.1, batch_first=True), 1)
        self.head = nn.Sequential(nn.Linear(d, 32), nn.GELU(), nn.Linear(32, 1))

    def forward(self, x):                      # x: (n, 3, 72)
        h = self.conv(x).transpose(1, 2) + self.pos
        h = self.tf(h)
        return self.head(h[:, -1]).squeeze(-1)


def weights(raw):
    w = raw - raw.mean()
    return w / (w.abs().sum() + 1e-9)


class Data:
    def __init__(self):
        z = np.load(DIR / "statarb.npz")
        self.x, self.offs, self.t, self.coin = z["x"], z["offs"], z["t"], z["coin"]
        self.g, self.fund, self.cost, self.ts = z["g"], z["fund"], z["cost"], z["ts"]
        self.tt = self.ts[self.t]

    def date(self, k):
        a, b = self.offs[k], self.offs[k + 1]
        return (torch.from_numpy(self.x[a:b].astype(np.float32)), self.coin[a:b],
                torch.from_numpy(self.g[a:b]), torch.from_numpy(self.fund[a:b]), torch.from_numpy(self.cost[a:b]))


def run_seq(net, D, ks, train=False, opt=None, chunk=42, lam=1.0):
    """Walk dates in order; turnover is measured against the previous date's weights (mapped by coin id)."""
    rets, turns, grosses = [], [], []
    prev = {}
    for c0 in range(0, len(ks), chunk):
        seq = ks[c0:c0 + chunk]
        out = []
        for k in seq:
            x, coin, g, fund, cost = D.date(k)
            raw = net(x.to(DEV))
            w = weights(raw)
            wp = torch.tensor([prev.get(int(c), 0.0) for c in coin], device=DEV)
            gone = sum(abs(v) for c, v in prev.items() if c not in set(coin.tolist()))
            dw = (w - wp).abs()
            net_r = (w * (g.to(DEV) - fund.to(DEV))).sum() - (dw * cost.to(DEV)).sum() - gone * 0.0015
            out.append(net_r)
            grosses.append(float((w * g.to(DEV)).sum()))
            turns.append(float(dw.sum() + gone))
            prev = {int(c): float(v) for c, v in zip(coin, w.detach().cpu())}
        R = torch.stack(out)
        if train:
            loss = -(R.mean() / (R.std() + 1e-6)) * lam
            opt.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(net.parameters(), 1.0)
            opt.step()
        rets += [float(r) for r in R.detach().cpu()]
    return np.array(rets), np.array(turns), np.array(grosses)


def summary(r, tt, turns, gross):
    day = {}
    for v, t in zip(r, tt):
        day[t // D_] = day.get(t // D_, 0.0) + v
    d = np.array(list(day.values()))
    rng = np.random.default_rng(5)
    bs = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(4000)]
    return dict(days=len(d), net_day=float(d.mean()), ci=np.percentile(bs, [2.5, 97.5]).tolist(),
                sharpe=float(d.mean() / (d.std() + 1e-12) * np.sqrt(365)), turnover_per_rebalance=float(np.mean(turns)),
                gross_per_rebalance=float(np.mean(gross)))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    D = Data()
    ktr = np.flatnonzero(D.tt < VAL0)
    kva = np.flatnonzero((D.tt >= VAL0) & (D.tt < DISC1))
    kte = np.flatnonzero((D.tt >= DISC1) & (D.tt < HOLD1))
    res = {}
    test_runs = []
    for seed in (1, 2, 3):
        torch.manual_seed(seed)
        np.random.seed(seed)
        net = Net().to(DEV)
        opt = torch.optim.AdamW(net.parameters(), lr=3e-4, weight_decay=1e-3)
        best, state, bad = -9, None, 0
        for ep in range(25):
            net.train()
            # random contiguous starting offset each epoch, sequences kept in time order inside chunks
            off = np.random.randint(0, 42)
            run_seq(net, D, ktr[off:], train=True, opt=opt)
            net.eval()
            with torch.no_grad():
                r, tu, gr = run_seq(net, D, kva)
            sv = summary(r, D.tt[kva], tu, gr)["sharpe"]
            print(f"seed {seed} ep {ep} val net sharpe {sv:+.2f}", flush=True)
            if sv > best:
                best, bad, state = sv, 0, {k: v.detach().clone() for k, v in net.state_dict().items()}
            else:
                bad += 1
                if bad >= 5:
                    break
        net.load_state_dict(state)
        torch.save(state, ROOT / f"data/models/b7_statarb_seed{seed}.pt")
        net.eval()
        with torch.no_grad():
            r, tu, gr = run_seq(net, D, kte)
        test_runs.append(r)
        res[f"seed{seed}"] = dict(val_sharpe=best, test=summary(r, D.tt[kte], tu, gr))
        print("seed", seed, res[f"seed{seed}"], flush=True)
    res["ensemble_mean_of_seed_returns"] = summary(np.mean(test_runs, 0), D.tt[kte], [0], [0])
    res["pass"] = bool(res["ensemble_mean_of_seed_returns"]["sharpe"] > 1.0 and res["ensemble_mean_of_seed_returns"]["ci"][0] > 0)
    print(json.dumps(res, indent=1, default=float))
    json.dump(res, open(OUT / "b7_C.json", "w"), indent=1, default=float)


if __name__ == "__main__":
    main()
