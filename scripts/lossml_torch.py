"""B6 neural models per walk-forward fold: MLP on tabular features, GRU on 48h sequence + tabular.
  OMP_NUM_THREADS=4 .venv/bin/python -W ignore scripts/lossml_torch.py mlp|gru
No lightgbm import in this process (macOS OpenMP clash)."""
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lossml_data import FEATURES  # noqa: E402
from lossml_folds import folds  # noqa: E402

MOD = ROOT / "data/models/lossml"
DEV = "mps" if torch.backends.mps.is_available() else "cpu"
torch.manual_seed(7)


class MLP(nn.Module):
    def __init__(self, d):
        super().__init__()
        self.f = nn.Sequential(nn.Linear(d, 256), nn.BatchNorm1d(256), nn.SiLU(), nn.Dropout(0.3),
                               nn.Linear(256, 128), nn.BatchNorm1d(128), nn.SiLU(), nn.Dropout(0.3), nn.Linear(128, 1))

    def forward(self, x, s=None):
        return self.f(x).squeeze(-1)


class GRUNet(nn.Module):
    def __init__(self, d):
        super().__init__()
        self.g = nn.GRU(4, 48, num_layers=1, batch_first=True)
        self.t = nn.Sequential(nn.Linear(d, 64), nn.SiLU())
        self.h = nn.Sequential(nn.Dropout(0.3), nn.Linear(48 + 64, 64), nn.SiLU(), nn.Linear(64, 1))

    def forward(self, x, s):
        _, h = self.g(s)
        return self.h(torch.cat([h[-1], self.t(x)], 1)).squeeze(-1)


def batches(idx, X, S, sign, bs):
    for k in range(0, len(idx), bs):
        i = idx[k:k + bs]
        x = torch.from_numpy(X[i]).to(DEV)
        s = None
        if S is not None:
            s = S[i].astype(np.float32)
            s[:, :, 0] *= sign[i, None]
            s[:, :, 2] *= sign[i, None]
            s = torch.from_numpy(np.nan_to_num(s)).to(DEV)
        yield i, x, s


def pred(model, idx, X, S, sign):
    model.eval()
    out = []
    with torch.no_grad():
        for _, x, s in batches(idx, X, S, sign, 8192):
            out.append(torch.sigmoid(model(x, s)).cpu().numpy())
    return np.concatenate(out)


def main(which: str) -> None:
    T = pd.read_parquet(ROOT / "data/cache/lossml_tab.parquet", columns=["ts", "seq_idx"] + FEATURES + ["loss24"])
    ts, y = T["ts"].to_numpy(), T["loss24"].to_numpy().astype(np.float32)
    Xraw = T[FEATURES].to_numpy(np.float32)
    sign = T["side"].to_numpy(np.float32)
    S = None
    if which == "gru":
        seq = np.load(ROOT / "data/cache/lossml_seq.npy", mmap_mode="r")
        seq = np.transpose(np.asarray(seq), (0, 2, 1))           # (n, 48, 4)
        S = seq[T["seq_idx"].to_numpy()]                          # float16, rows aligned
        sc = np.nanstd(S[::50].astype(np.float32), axis=(0, 1)) + 1e-6
        S = (S / sc.astype(np.float16)).astype(np.float16)
    preds = []
    for f in folds():
        tr = np.flatnonzero(ts < f["train1"])
        va = np.flatnonzero((ts >= f["val0"]) & (ts < f["val1"]))
        te = np.flatnonzero((ts >= f["test0"]) & (ts < f["test1"]))
        mu, sd = np.nanmean(Xraw[tr], 0), np.nanstd(Xraw[tr], 0) + 1e-6
        X = np.clip(np.nan_to_num((Xraw - mu) / sd), -6, 6).astype(np.float32)
        model = (MLP if which == "mlp" else GRUNet)(X.shape[1]).to(DEV)
        opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-3)
        lf = nn.BCEWithLogitsLoss()
        best, state, bad = -1.0, None, 0
        for ep in range(10):
            model.train()
            perm = np.random.default_rng(ep).permutation(tr)
            for i, x, s in batches(perm, X, S, sign, 2048):
                opt.zero_grad()
                loss = lf(model(x, s), torch.from_numpy(y[i]).to(DEV))
                loss.backward()
                opt.step()
            a = roc_auc_score(y[va], pred(model, va, X, S, sign))
            if a > best:
                best, bad, state = a, 0, {k: v.detach().clone() for k, v in model.state_dict().items()}
            else:
                bad += 1
                if bad >= 3:
                    break
        model.load_state_dict(state)
        pv, pt = pred(model, va, X, S, sign), pred(model, te, X, S, sign)
        iso = IsotonicRegression(out_of_bounds="clip").fit(pv, y[va])
        torch.save(model.state_dict(), MOD / f"{which}_f{f['k']}.pt")
        pickle.dump(dict(iso=iso, mu=mu, sd=sd), open(MOD / f"{which}_iso_f{f['k']}.pkl", "wb"))
        preds.append(pd.DataFrame({"row": te, "fold": f["k"], "p": iso.predict(pt), "p_raw": pt}))
        print(which, "fold", f["k"], "epochs", ep + 1, "val auc", round(best, 4), "test auc", round(roc_auc_score(y[te], pt), 4),
              flush=True)
    pd.concat(preds).to_parquet(ROOT / f"data/cache/lossml_pred_{which}.parquet", index=False)


if __name__ == "__main__":
    main(sys.argv[1])
