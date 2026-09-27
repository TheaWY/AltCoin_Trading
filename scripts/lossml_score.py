"""Score B6 event / paper-trade rows with the fold model whose test window contains each row (strictly pre-trained).
  .venv/bin/python -W ignore scripts/lossml_score.py tree     (lgb + cat, no torch in process)
  OMP_NUM_THREADS=4 .venv/bin/python -W ignore scripts/lossml_score.py torch   (mlp + gru)
Out: data/cache/lossml_{ev,pt}_p_{model}.npy"""
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lossml_data import FEATURES  # noqa: E402
from lossml_folds import folds  # noqa: E402

MOD = ROOT / "data/models/lossml"
FO = folds()


def fold_of(ts):
    k = np.zeros(len(ts), int)
    for f in FO:
        k[ts >= f["test0"]] = f["k"]
    return k


def main(which):
    sets = {n: (pd.read_parquet(ROOT / f"data/cache/lossml_{n}.parquet"), np.load(ROOT / f"data/cache/lossml_{n}_seq.npy"))
            for n in ("ev", "pt")}
    if which == "tree":
        import lightgbm as lgb
        from catboost import CatBoostClassifier
        for n, (R, _) in sets.items():
            X, k = R[FEATURES].to_numpy(np.float32), fold_of(R["ts"].to_numpy())
            for m in ("lgb", "cat"):
                p = np.full(len(R), np.nan)
                for f in np.unique(k):
                    iso = pickle.load(open(MOD / f"{m}_iso_f{f}.pkl", "rb"))
                    if m == "lgb":
                        raw = lgb.Booster(model_file=str(MOD / f"lgb_f{f}.txt")).predict(X[k == f])
                    else:
                        cb = CatBoostClassifier()
                        cb.load_model(str(MOD / f"cat_f{f}.cbm"))
                        raw = cb.predict_proba(X[k == f])[:, 1]
                    p[k == f] = iso.predict(raw)
                np.save(ROOT / f"data/cache/lossml_{n}_p_{m}.npy", p)
                print(n, m, np.nanmean(p).round(4), flush=True)
        return
    import torch
    from lossml_torch import DEV, MLP, GRUNet
    T = pd.read_parquet(ROOT / "data/cache/lossml_tab.parquet", columns=["seq_idx"])
    seq = np.transpose(np.load(ROOT / "data/cache/lossml_seq.npy"), (0, 2, 1))
    S_all = seq[T["seq_idx"].to_numpy()]
    sc = (np.nanstd(S_all[::50].astype(np.float32), axis=(0, 1)) + 1e-6).astype(np.float16)   # same as training
    del S_all, seq, T
    for n, (R, S0) in sets.items():
        Xraw, k = R[FEATURES].to_numpy(np.float32), fold_of(R["ts"].to_numpy())
        sign = R["side"].to_numpy(np.float32)
        S = (np.transpose(S0, (0, 2, 1)) / sc).astype(np.float32)
        S[:, :, 0] *= sign[:, None]
        S[:, :, 2] *= sign[:, None]
        S = np.nan_to_num(S)
        for m in ("mlp", "gru"):
            p = np.full(len(R), np.nan)
            for f in np.unique(k):
                st = pickle.load(open(MOD / f"{m}_iso_f{f}.pkl", "rb"))
                X = np.clip(np.nan_to_num((Xraw[k == f] - st["mu"]) / st["sd"]), -6, 6).astype(np.float32)
                net = (MLP if m == "mlp" else GRUNet)(X.shape[1]).to(DEV)
                net.load_state_dict(torch.load(MOD / f"{m}_f{f}.pt", map_location=DEV))
                net.eval()
                out = []
                with torch.no_grad():
                    for a in range(0, len(X), 8192):
                        x = torch.from_numpy(X[a:a + 8192]).to(DEV)
                        s = torch.from_numpy(S[k == f][a:a + 8192]).to(DEV)
                        out.append(torch.sigmoid(net(x, s)).cpu().numpy())
                p[k == f] = st["iso"].predict(np.concatenate(out))
            np.save(ROOT / f"data/cache/lossml_{n}_p_{m}.npy", p)
            print(n, m, np.nanmean(p).round(4), flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
