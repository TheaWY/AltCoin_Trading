"""B2 ML/DL layer: M1..M8 on pump / dump / breakout event sets (registered in research/batch_B2.yaml).

Split: train 2025-03..2025-11, validate 2025-12 (early stopping, thresholds), test 2026-01..2026-09.
Target: sign of the 4h return from the next-minute open. Trade on test: long if p >= validation 70th pct,
short if p <= 30th pct, hold 4h, net of fees+slippage. Stats: AUC (bootstrap CI), mean net per trade with a
day-clustered bootstrap CI.
Out: data/reports/b2/models.csv, models.md; ledger rows.
"""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

ROOT = Path(__file__).resolve().parents[1]
OUTD = ROOT / "data" / "reports" / "b2"
LEDGER = ROOT / "research" / "trial_ledger.csv"
TR_END = int(pd.Timestamp("2025-12-01").timestamp())
VA_END = int(pd.Timestamp("2026-01-01").timestamp())
DEV = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
torch.manual_seed(7)
np.random.seed(7)


def auc(y, p):
    from sklearn.metrics import roc_auc_score
    return roc_auc_score(y, p) if len(np.unique(y)) == 2 else np.nan


def boot_auc(y, p, n=1000):
    rng = np.random.default_rng(1)
    v = []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        if len(np.unique(y[i])) == 2:
            v.append(auc(y[i], p[i]))
    return float(np.quantile(v, 0.025)), float(np.quantile(v, 0.975))


def boot_day(x, day, n=2000):
    df = pd.DataFrame({"x": x, "d": day})
    groups = [g["x"].to_numpy() for _, g in df.groupby("d")]
    if len(groups) < 5:
        return np.nan, np.nan
    rng = np.random.default_rng(2)
    m = []
    for _ in range(n):
        pick = rng.integers(0, len(groups), len(groups))
        v = np.concatenate([groups[k] for k in pick])
        m.append(v.mean())
    return float(np.quantile(m, 0.025)), float(np.quantile(m, 0.975))


# ------------------------------------------------------------------ torch models

class MLP(nn.Module):
    def __init__(self, d):
        super().__init__()
        self.f = nn.Sequential(nn.Linear(d, 64), nn.ReLU(), nn.Dropout(0.3), nn.Linear(64, 32), nn.ReLU(),
                               nn.Dropout(0.3), nn.Linear(32, 1))

    def forward(self, x):
        return self.f(x).squeeze(-1)


class CNN1D(nn.Module):
    def __init__(self, c=5):
        super().__init__()
        self.f = nn.Sequential(nn.Conv1d(c, 32, 5, padding=2), nn.ReLU(), nn.MaxPool1d(2),
                               nn.Conv1d(32, 64, 5, padding=2), nn.ReLU(), nn.MaxPool1d(2),
                               nn.Conv1d(64, 64, 3, padding=1), nn.ReLU(), nn.AdaptiveAvgPool1d(1))
        self.h = nn.Sequential(nn.Dropout(0.3), nn.Linear(64, 1))

    def forward(self, x):
        return self.h(self.f(x).squeeze(-1)).squeeze(-1)


class GRU(nn.Module):
    def __init__(self, c=5):
        super().__init__()
        self.g = nn.GRU(c, 64, batch_first=True)
        self.h = nn.Sequential(nn.Dropout(0.3), nn.Linear(64, 1))

    def forward(self, x):
        o, _ = self.g(x.transpose(1, 2))
        return self.h(o[:, -1]).squeeze(-1)


class TF(nn.Module):
    def __init__(self, c=5, d=32, L=120):
        super().__init__()
        self.inp = nn.Linear(c, d)
        self.pos = nn.Parameter(torch.zeros(1, L, d))
        layer = nn.TransformerEncoderLayer(d, 2, 64, dropout=0.2, batch_first=True)
        self.enc = nn.TransformerEncoder(layer, 2)
        self.h = nn.Linear(d, 1)

    def forward(self, x):
        z = self.inp(x.transpose(1, 2)) + self.pos
        return self.h(self.enc(z).mean(1)).squeeze(-1)


class CNN2D(nn.Module):
    """Jiang, Kelly & Xiu (2023) style image CNN, scaled down."""
    def __init__(self):
        super().__init__()
        self.f = nn.Sequential(nn.Conv2d(1, 32, (5, 3), padding=(2, 1)), nn.BatchNorm2d(32), nn.LeakyReLU(),
                               nn.MaxPool2d((2, 1)),
                               nn.Conv2d(32, 64, (5, 3), padding=(2, 1)), nn.BatchNorm2d(64), nn.LeakyReLU(),
                               nn.MaxPool2d((2, 1)),
                               nn.Conv2d(64, 64, (5, 3), padding=(2, 1)), nn.BatchNorm2d(64), nn.LeakyReLU(),
                               nn.AdaptiveAvgPool2d((4, 4)))
        self.h = nn.Sequential(nn.Dropout(0.5), nn.Linear(64 * 16, 1))

    def forward(self, x):
        return self.h(self.f(x).flatten(1)).squeeze(-1)


def fit_torch(model, Xtr, ytr, Xva, yva, epochs=40, bs=256, lr=1e-3):
    model = model.to(DEV)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-3)
    lossf = nn.BCEWithLogitsLoss()
    Xt, yt = torch.tensor(Xtr, dtype=torch.float32), torch.tensor(ytr, dtype=torch.float32)
    Xv = torch.tensor(Xva, dtype=torch.float32).to(DEV)  # batched below (MPS memory)
    best, best_state, bad = -1, None, 0
    for ep in range(epochs):
        model.train()
        perm = torch.randperm(len(Xt))
        for k in range(0, len(Xt), bs):
            i = perm[k:k + bs]
            xb, yb = Xt[i].to(DEV), yt[i].to(DEV)
            opt.zero_grad()
            loss = lossf(model(xb), yb)
            loss.backward()
            opt.step()
        model.eval()
        with torch.no_grad():
            pv = np.concatenate([torch.sigmoid(model(Xv[k:k + 1024])).cpu().numpy() for k in range(0, len(Xv), 1024)])
        a = auc(yva, pv)
        if np.isfinite(a) and a > best:
            best, bad = a, 0
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
            if bad >= 6:
                break
    if best_state is not None:
        model.load_state_dict(best_state)
    return model


def predict(model, X, bs=1024):
    model.eval()
    out = []
    with torch.no_grad():
        for k in range(0, len(X), bs):
            out.append(torch.sigmoid(model(torch.tensor(X[k:k + bs], dtype=torch.float32).to(DEV))).cpu().numpy())
    return np.concatenate(out)


# ------------------------------------------------------------------ run

def run_set(name: str) -> list[dict]:
    print("loading", name, flush=True)
    z = np.load(ROOT / f"data/cache/b2_ds_{name}.npz", allow_pickle=True)
    tab, seq, img = z["tab"], z["seq"], z["img"].astype(np.float32)[:, None] / 255.0
    gross, cost, ts = z["gross"], z["cost"], z["ts"]
    y = (gross > 0).astype(np.float32)
    tr, va, te = ts < TR_END, (ts >= TR_END) & (ts < VA_END), ts >= VA_END
    # tabular: winsorise with train quantiles, standardise
    tabf = np.nan_to_num(tab, nan=0.0)
    lo, hi = np.nanquantile(tabf[tr], 0.01, axis=0), np.nanquantile(tabf[tr], 0.99, axis=0)
    tabf = np.clip(tabf, lo, hi)
    mu, sd = tabf[tr].mean(0), tabf[tr].std(0) + 1e-9
    tabf = (tabf - mu) / sd
    seqf = np.clip(np.nan_to_num(seq), -1, 5).copy()
    seqf[:, :3] = np.clip(seqf[:, :3] * 20, -5, 5)       # scale relative prices
    preds = {}
    import subprocess
    tp = ROOT / f"data/cache/b2_tree_{name}.npz"
    subprocess.run([sys.executable, "-W", "ignore", str(Path(__file__).with_name("b2_tree.py")), name], check=True)
    tz = np.load(tp)
    preds["M1"], preds["M2"] = tz["M1"], tz["M2"]
    print("M1 M2 loaded", name, flush=True)
    if False:
        from sklearn.linear_model import LogisticRegression
        import lightgbm as lgb
    pass
    preds["M3"] = predict(fit_torch(MLP(tabf.shape[1]), tabf[tr], y[tr], tabf[va], y[va]), tabf)
    print("M3 done", name, flush=True)
    preds["M4"] = predict(fit_torch(CNN1D(), seqf[tr], y[tr], seqf[va], y[va]), seqf)
    print("M4 done", name, flush=True)
    preds["M5"] = predict(fit_torch(GRU(), seqf[tr], y[tr], seqf[va], y[va], epochs=25), seqf)
    print("M5 done", name, flush=True)
    preds["M6"] = predict(fit_torch(TF(), seqf[tr], y[tr], seqf[va], y[va], epochs=25), seqf)
    print("M6 done", name, flush=True)
    preds["M7"] = predict(fit_torch(CNN2D(), img[tr], y[tr], img[va], y[va], epochs=25, bs=128), img)
    print("M7 done", name, flush=True)
    preds["M8"] = (preds["M2"] + preds["M4"] + preds["M7"]) / 3
    rows = []
    day = ts // 86400
    base_long = gross[te] - cost[te]
    for k, p in preds.items():
        qlo, qhi = np.quantile(p[va], 0.3), np.quantile(p[va], 0.7)
        side = np.where(p >= qhi, 1, np.where(p <= qlo, -1, 0))
        sel = te & (side != 0)
        net = side[sel] * gross[sel] - cost[sel]
        a = auc(y[te], p[te])
        alo, ahi = boot_auc(y[te], p[te])
        lo_, hi_ = boot_day(net, day[sel])
        rows.append(dict(id=f"B2_{k}_{name}", set=name, n_train=int(tr.sum()), n_test=int(te.sum()),
                         auc_val=auc(y[va], p[va]), auc_test=a, auc_lo=alo, auc_hi=ahi, trades=int(sel.sum()),
                         long_share=float((side[sel] > 0).mean()) if sel.any() else np.nan,
                         mean_net=float(net.mean()) if len(net) else np.nan, ci_lo=lo_, ci_hi=hi_,
                         hit=float((net > 0).mean()) if len(net) else np.nan,
                         base_long_net=float(base_long.mean()), base_short_net=float((-gross[te] - cost[te]).mean())))
        print(rows[-1], flush=True)
    return rows


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--tree":
        tree_stage(sys.argv[2])
        return 0
    rows = []
    for name in ("pump", "dump", "brk"):
        rows += run_set(name)
    R = pd.DataFrame(rows)
    R["pass"] = (R["ci_lo"] > 0) & (R["auc_lo"] > 0.5)
    OUTD.mkdir(parents=True, exist_ok=True)
    R.to_csv(OUTD / "models.csv", index=False)
    led = pd.read_csv(LEDGER)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    add = pd.DataFrame([dict(ts=now, run_id=f"B2M-{int(time.time())}", hypothesis=r["id"], tier="confirmatory",
                             period="test 2026-01..09", mean_daily_net=r["mean_net"], n_trades=r["trades"],
                             note=f"auc_test={r['auc_test']:.3f}") for r in rows])
    pd.concat([led, add], ignore_index=True).to_csv(LEDGER, index=False)
    print(R.round(4).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
