"""B17 F01c sequence models on the 22 precursor panels: 24-hour history per coin-hour -> P(pump within 6h).
  .venv/bin/python -W ignore scripts/b17_f01_seq.py gru|tcn|transformer     -> data/cache/b17/p_{model}.npy, thr_{model}.npy (same format as b17_f01b fit)
Same quarterly walk-forward folds and 2-day embargo. Training rows: all positives + 8x random negatives from the training period
(class-balanced loss); validation/test rows: every universe coin-hour. Early stopping on the last 30 days of each training period.
Torch only (no lightgbm import) because of the macOS OpenMP clash."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b7_lib as L  # noqa: E402
from b17_f01 import FEATS, VAL0, labels, panel  # noqa: E402

B = ROOT / "data/cache/b17"
RNG = np.random.default_rng(1703)
torch.manual_seed(1703)
W, HZ, H_ = 24, 6, 3600
QS = [0.999, 0.995, 0.98]
DEV = torch.device("mps" if torch.backends.mps.is_available() else "cpu")


class GRU(nn.Module):
    def __init__(self, f):
        super().__init__(); self.g = nn.GRU(f, 64, num_layers=2, batch_first=True, dropout=0.1); self.o = nn.Linear(64, 1)

    def forward(self, x):
        return self.o(self.g(x)[0][:, -1]).squeeze(-1)


class TCN(nn.Module):
    def __init__(self, f):
        super().__init__()
        layers, ch = [], f
        for d in (1, 2, 4, 8):
            layers += [nn.Conv1d(ch, 64, 3, padding=d, dilation=d), nn.GELU(), nn.Dropout(0.1)]; ch = 64
        self.net = nn.Sequential(*layers); self.o = nn.Linear(64, 1)

    def forward(self, x):
        return self.o(self.net(x.transpose(1, 2))[:, :, -1]).squeeze(-1)


class Trans(nn.Module):
    def __init__(self, f):
        super().__init__()
        self.inp = nn.Linear(f, 64); self.pos = nn.Parameter(torch.randn(1, W, 64) * 0.02)
        self.enc = nn.TransformerEncoder(nn.TransformerEncoderLayer(64, 4, 128, 0.1, batch_first=True), 2); self.o = nn.Linear(64, 1)

    def forward(self, x):
        return self.o(self.enc(self.inp(x) + self.pos)[:, -1]).squeeze(-1)


def windows(feat, t_idx, j_idx):
    """feat (T,N,F) -> (n, W, F) windows ending at t (inclusive)."""
    out = np.zeros((len(t_idx), W, feat.shape[-1]), np.float32)
    for k, (t, j) in enumerate(zip(t_idx, j_idx)):
        s = max(0, t - W + 1); out[k, W - (t - s + 1):] = feat[s:t + 1, j]
    return out


def main(model):
    ts, codes, X, F = panel()
    onset, Y = labels(ts, codes)
    y = Y[HZ]
    U = (X["dv24"] >= 5e6) & (X["age"] >= 720)
    T, N = U.shape
    feat = np.stack([np.nan_to_num(np.asarray(F[k], np.float32), nan=0.0, posinf=0, neginf=0) for k in FEATS + ["DEPTH1_to_volume", "IMB1_6h"]], -1)
    mu, sd = feat[U].mean(0), feat[U].std(0) + 1e-6
    feat = ((feat - mu) / sd).clip(-6, 6).astype(np.float32)
    P_all = np.full((T, N), np.nan, np.float32); thr = np.full((T, 3), np.nan, np.float32)
    q_edges = np.arange(int(ts[0]), int(ts[-1]) + 1, 91 * 86400)
    for q0, q1 in zip(q_edges[1:], list(q_edges[2:]) + [int(ts[-1]) + H_]):
        tr_end = q0 - 2 * 86400 - HZ * H_
        itr = np.flatnonzero((ts >= ts[0] + W * H_) & (ts < tr_end)); ite = np.flatnonzero((ts >= q0) & (ts < q1))
        if len(ite) == 0:
            continue
        cal = ts[itr] >= tr_end - 30 * 86400
        pos = np.argwhere(U[itr] & y[itr]); neg = np.argwhere(U[itr] & ~y[itr])
        if len(pos) < 50:
            continue
        neg = neg[RNG.choice(len(neg), min(len(neg), 8 * len(pos)), replace=False)]
        sel = np.concatenate([pos, neg]); is_cal = cal[sel[:, 0]]
        Xw = torch.tensor(windows(feat, itr[sel[:, 0]], sel[:, 1])); yw = torch.tensor(y[itr][sel[:, 0], sel[:, 1]].astype(np.float32))
        tr_i, ca_i = np.flatnonzero(~is_cal), np.flatnonzero(is_cal)
        net = {"gru": GRU, "tcn": TCN, "transformer": Trans}[model](feat.shape[-1]).to(DEV)
        opt = torch.optim.AdamW(net.parameters(), 1e-3, weight_decay=1e-4)
        lossf = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(8.0, device=DEV))
        best, state, bad = 1e9, None, 0
        for ep in range(40):
            net.train()
            for b in np.array_split(RNG.permutation(tr_i), max(1, len(tr_i) // 512)):
                opt.zero_grad(); l = lossf(net(Xw[b].to(DEV)), yw[b].to(DEV)); l.backward(); opt.step()
            net.eval()
            with torch.no_grad():
                vl = float(np.mean([float(lossf(net(Xw[b].to(DEV)), yw[b].to(DEV))) for b in np.array_split(ca_i, max(1, len(ca_i) // 2048))])) if len(ca_i) else 0
            if vl < best - 1e-4:
                best, state, bad = vl, {k: v.clone() for k, v in net.state_dict().items()}, 0
            else:
                bad += 1
                if bad >= 5:
                    break
        if state:
            net.load_state_dict(state)
        net.eval()
        with torch.no_grad():
            # thresholds on the natural (unbalanced) training distribution: random universe coin-hours
            allu = np.argwhere(U[itr]); allu = allu[RNG.choice(len(allu), min(300_000, len(allu)), replace=False)]
            p_nat = np.concatenate([torch.sigmoid(net(torch.tensor(windows(feat, itr[allu[b, 0]], allu[b, 1])).to(DEV))).cpu().numpy()
                                    for b in np.array_split(np.arange(len(allu)), max(1, len(allu) // 4096))])
            for qi, qq in enumerate(QS):
                thr[ite, qi] = np.quantile(p_nat, qq)
            cand = np.argwhere(U[ite])
            for b in np.array_split(np.arange(len(cand)), max(1, len(cand) // 4096)):
                xb = torch.tensor(windows(feat, ite[cand[b, 0]], cand[b, 1])).to(DEV)
                P_all[ite[cand[b, 0]], cand[b, 1]] = torch.sigmoid(net(xb)).cpu().numpy()
        print(model, str(np.datetime64(int(q0), "s"))[:10], "ep", ep, "cal", round(best, 4), flush=True)
    np.save(B / f"p_{model}.npy", P_all); np.save(B / f"thr_{model}.npy", thr)
    from sklearn.metrics import roc_auc_score
    v = (ts >= VAL0)[:, None] & U & np.isfinite(P_all)
    print(model, "validation AUC", roc_auc_score(y[v], P_all[v]))


if __name__ == "__main__":
    main(sys.argv[1])
