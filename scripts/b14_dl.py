"""B14_4 sequence DL: GRU and TCN on 48h x 12 sequences + tensor snapshot; 3 seeds; targets (a) pump6 (b) sign(y_res24).
Blend = mean of calibrated probabilities with the LightGBM baseline (tree probabilities are produced by b14_dl_tree.py first).
  OMP_NUM_THREADS=4 .venv/bin/python -W ignore scripts/b14_dl.py
Torch process: no lightgbm import."""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import average_precision_score, roc_auc_score

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import b14_lib as B  # noqa: E402
from b14_lib import boot_ci, quintile_ls, slice_rules, slice_table, topk_long, wf_folds  # noqa: E402

DEV = "mps" if torch.backends.mps.is_available() else "cpu"


class GRUNet(nn.Module):
    def __init__(self, d):
        super().__init__()
        self.g = nn.GRU(12, 64, batch_first=True)
        self.t = nn.Sequential(nn.Linear(d, 64), nn.SiLU())
        self.h = nn.Sequential(nn.Dropout(0.3), nn.Linear(128, 64), nn.SiLU(), nn.Linear(64, 1))

    def forward(self, s, x):
        _, h = self.g(s)
        return self.h(torch.cat([h[-1], self.t(x)], 1)).squeeze(-1)


class TCN(nn.Module):
    def __init__(self, d):
        super().__init__()
        layers, ch = [], 12
        for k, dil in enumerate((1, 2, 4, 8)):
            layers += [nn.Conv1d(ch, 32, 3, padding=2 * dil, dilation=dil), nn.GELU()]
            ch = 32
        self.c = nn.Sequential(*layers)
        self.t = nn.Sequential(nn.Linear(d, 64), nn.SiLU())
        self.h = nn.Sequential(nn.Dropout(0.3), nn.Linear(96, 64), nn.SiLU(), nn.Linear(64, 1))

    def forward(self, s, x):
        z = self.c(s.transpose(1, 2))[:, :, :48][:, :, -1]        # causal crop, last step
        return self.h(torch.cat([z, self.t(x)], 1)).squeeze(-1)


def batches(idx, S, X, bs):
    for k in range(0, len(idx), bs):
        i = idx[k:k + bs]
        yield i, torch.from_numpy(S[i].astype(np.float32)).to(DEV), torch.from_numpy(X[i]).to(DEV)


def predict(net, idx, S, X):
    net.eval()
    out = []
    with torch.no_grad():
        for _, s, x in batches(idx, S, X, 8192):
            out.append(torch.sigmoid(net(s, x)).cpu().numpy())
    return np.concatenate(out) if out else np.array([])


def fit(arch, tr, va, y, S, X, seed, pos_weight):
    torch.manual_seed(seed)
    net = arch(X.shape[1]).to(DEV)
    opt = torch.optim.AdamW(net.parameters(), lr=1e-3, weight_decay=1e-3)
    lf = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(pos_weight, device=DEV))
    best, state, bad = -1.0, None, 0
    for ep in range(8):
        net.train()
        for i, s, x in batches(np.random.default_rng(seed + ep).permutation(tr), S, X, 2048):
            opt.zero_grad()
            loss = lf(net(s, x), torch.from_numpy(y[i]).to(DEV))
            loss.backward()
            opt.step()
        a = roc_auc_score(y[va], predict(net, va, S, X))
        if a > best:
            best, bad, state = a, 0, {k: v.detach().clone() for k, v in net.state_dict().items()}
        else:
            bad += 1
            if bad >= 2:
                break
    net.load_state_dict(state)
    return net


def main():
    T, G = B.load_tab()
    cols = B.feature_sets(G)["d_full"]
    S = np.load(B.B14 / "seq.npy", mmap_mode="r")
    S = np.asarray(S)[T["seq_idx"].to_numpy()]
    Xr = T[cols].to_numpy(np.float32)
    tree = pd.read_parquet(B.B14 / "pred_dl_tree.parquet")           # from b14_dl_tree.py: ts, j, p_tree_pump, p_tree_sign
    res = {}
    for target in ("pump6", "sign"):
        y = (T["y_pump6"].to_numpy() if target == "pump6" else (T["y_res24"] > 0).to_numpy()).astype(np.float32)
        parts = []
        for q0, tr, va, te in wf_folds(T):
            tr, va, te = map(np.flatnonzero, (tr, va, te))
            mu, sd = np.nanmean(Xr[tr], 0), np.nanstd(Xr[tr], 0) + 1e-6
            X = np.clip(np.nan_to_num((Xr - mu) / sd), -6, 6).astype(np.float32)
            pw = float((1 - y[tr].mean()) / max(y[tr].mean(), 1e-4)) if target == "pump6" else 1.0
            ted = T.iloc[te][["ts", "j"]].copy()
            for arch, nm in ((GRUNet, "gru"), (TCN, "tcn")):
                ps = []
                for seed in (1, 2, 3):
                    net = fit(arch, tr, va, y, S, X, seed, pw)
                    iso = IsotonicRegression(out_of_bounds="clip").fit(predict(net, va, S, X), y[va])
                    ps.append(iso.predict(predict(net, te, S, X)))
                ted[nm] = np.mean(ps, 0)
            parts.append(ted)
            print(target, q0, "gru auc", round(roc_auc_score(y[te], ted["gru"]), 4), "tcn auc", round(roc_auc_score(y[te], ted["tcn"]), 4), flush=True)
        P = pd.concat(parts).merge(tree, on=["ts", "j"])
        H = T.merge(P, on=["ts", "j"])
        yt = (H["y_pump6"] if target == "pump6" else (H["y_res24"] > 0)).to_numpy().astype(int)
        H["blend"] = H[["gru", "tcn", f"p_tree_{target}"]].mean(1)
        H["p"] = H["blend"]
        r = {}
        for nm in ("gru", "tcn", f"p_tree_{target}", "blend"):
            r[nm] = dict(auc=float(roc_auc_score(yt, H[nm])), pr_auc=float(average_precision_score(yt, H[nm])))
        # day-block CI of blend minus tree on the primary metric
        days = H["ts"] // B.D_
        ud, grp = days.unique(), {d: g for d, g in H.groupby(days)}
        diffs = []
        for _ in range(200):
            s = pd.concat([grp[d] for d in B.RNG.choice(ud, len(ud))])
            yy = (s["y_pump6"] if target == "pump6" else (s["y_res24"] > 0)).to_numpy().astype(int)
            if yy.sum() == 0:
                continue
            f = average_precision_score if target == "pump6" else roc_auc_score
            diffs.append(f(yy, s["blend"]) - f(yy, s[f"p_tree_{target}"]))
        r["blend_minus_tree"] = dict(mean=float(np.mean(diffs)), ci=boot_ci(np.array(diffs)))
        if target == "sign":
            r["ls"] = slice_table(H, quintile_ls)
            r["top10"] = slice_table(H, topk_long)
            r["rules"] = slice_rules(r["ls"])
            r["pass"] = bool(r["blend_minus_tree"]["ci"][0] > 0 and r["ls"]["pooled"]["ci"][0] > 0 and r["rules"]["all_ok"])
        else:
            top = H["blend"] >= H["blend"].quantile(0.995)
            r["precision_top_0p5pct"] = float(yt[top].mean())
            sel = H.sort_values("blend", ascending=False).groupby("ts").head(5)
            sel["pnl"] = B.net_returns(sel, 1)
            dd = sel.groupby("day")["pnl"].mean()
            r["top5_long_24h"] = dict(mean=float(dd.mean()), ci=boot_ci(dd.to_numpy()))
            r["pass"] = bool(r["blend_minus_tree"]["ci"][0] > 0 and r["top5_long_24h"]["ci"][0] > 0)
        res[target] = r
        print(target, json.dumps({k: v for k, v in r.items() if k not in ("ls", "top10")}, default=float), flush=True)
        H[["ts", "j", "gru", "tcn", "blend"]].to_parquet(B.B14 / f"pred_dl_{target}.parquet", index=False)
    json.dump(res, open(B.OUT / "b14_4.json", "w"), indent=1, default=float)


if __name__ == "__main__":
    main()
