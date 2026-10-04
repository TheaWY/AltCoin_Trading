"""B15_4 coordinated pumps, B15_2 pump shape clusters (DTW k-medoids + 1D-CNN on first 10 min). research/batch_B15_B16.yaml.
  .venv/bin/python -W ignore scripts/b15_more.py 4        # coordinated vs organic
  .venv/bin/python -W ignore scripts/b15_more.py 2a       # DTW k-medoids (tslearn/numba), no torch
  .venv/bin/python -W ignore scripts/b15_more.py 2b       # 1D-CNN classifier + tests (torch, separate process)
Out: data/reports/b15/b15_{4,2}.json, data/cache/b15/clusters.parquet

Implementation notes fixed before running (not visible holdout results):
 - B15_4 category source: Binance product sector tags (data/cache/cg_categories.json; CoinGecko free tier rate-limited, changed before any B15_4 run). Two pumps are 'related' if their coins share any
   category; a coin with no category data counts as unrelated. Primary = spec (>=2 unrelated other pumps within +-3 min).
   Secondary: (i) category-agnostic (>=2 other coins), (ii) primary excluding market bursts (>=10 pumps within +-3 min = market-wide move).
   Matching: each coordinated pump vs mean of organic pumps in the same size_bucket x liquidity tercile x period stratum;
   day-clustered CI on the matched difference in dd_6h (holdout). BH q=0.10 over the 3 variants.
 - B15_2: medoids fit on a random 2,500 discovery pumps (full pairwise DTW is O(n^2)); Sakoe-Chiba radius 6; channels = per-pump
   z-scored cumulative return and log-volume ratio over minutes 1..60; k in 3..6 by silhouette on that sample; every pump then
   assigned to its nearest medoid. Trade leg enters at the m0+10 close, exits m0+1440, cost = b15_models cost.
   CNN input = 4 channels x minutes 1..10; train on discovery before CAL0, early stop on the last 60 discovery days.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import kruskal

if sys.argv[1:] == ["2b"]:
    import torch  # noqa: F401  (load torch's OpenMP before lightgbm's; segfault otherwise on macOS)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b15_models as M  # noqa: E402

C, OUT = M.C, M.OUT
M0 = 60
RNG = np.random.default_rng(152)


def dump(name, obj):
    OUT.mkdir(parents=True, exist_ok=True)
    json.dump(obj, open(OUT / f"{name}.json", "w"), indent=1, default=float)
    print(json.dumps(obj, indent=1, default=float)[:6000])


# ------------------------------------------------------------------ B15_4
def exp4():
    P = M.pump_context()
    cats = json.load(open(C / "cg_categories.json"))
    catmap = {k: set(v or []) for k, v in cats.items()}
    P["period"] = np.where(P["hold"], "holdout", "discovery")
    ts, code = P["ts"].to_numpy(), P["code"].to_numpy()
    order = np.argsort(ts)
    ts_s, code_s = ts[order], code[order]
    n_unrel, n_any, n_all = np.zeros(len(P), int), np.zeros(len(P), int), np.zeros(len(P), int)
    for j, i in enumerate(order):
        lo, hi = np.searchsorted(ts_s, ts[i] - 180), np.searchsorted(ts_s, ts[i] + 180, side="right")
        others = {c for c in code_s[lo:hi] if c != code[i]}
        n_all[i] = hi - lo
        n_any[i] = len(others)
        mine = catmap.get(code[i], set())
        n_unrel[i] = sum(1 for c in others if not (mine & catmap.get(c, set())))
    P["n_unrel"], P["n_any"], P["n_window"] = n_unrel, n_any, n_all
    P["short_net"] = -P["ret24_from_entry"] - P["cost"]
    variants = {"primary_unrelated>=2": P["n_unrel"] >= 2,
                "any_coin>=2": P["n_any"] >= 2,
                "unrelated>=2_no_burst": (P["n_unrel"] >= 2) & (P["n_window"] < 10)}
    res = {"category_coverage": float(np.mean([c in catmap and len(catmap[c]) > 0 for c in P["code"]]))}
    pvals = {}
    for name, flag in variants.items():
        P["coord"] = flag.astype(int)
        out = {}
        for per, g in P.groupby("period"):
            base = g[g["coord"] == 0].groupby(["size_bucket", "liq"])[["dd_6h", "short_net", "ret24_from_entry"]].mean()
            cg = g[g["coord"] == 1].join(base, on=["size_bucket", "liq"], rsuffix="_org").dropna(subset=["dd_6h_org"])
            diff = (cg["dd_6h"] - cg["dd_6h_org"]).to_numpy()
            dshort = (cg["short_net"] - cg["short_net_org"]).to_numpy()
            ci = M.day_ci(diff, cg["day"].to_numpy())
            boot = []
            dd = pd.DataFrame({"x": diff, "d": cg["day"].to_numpy()}).groupby("d")["x"].agg(["sum", "count"])
            su, cn = dd["sum"].to_numpy(), dd["count"].to_numpy()
            for _ in range(4000):
                k = RNG.integers(0, len(su), len(su)); boot.append(su[k].sum() / cn[k].sum())
            p = 2 * min(np.mean(np.array(boot) <= 0), np.mean(np.array(boot) >= 0))
            out[per] = dict(n_coord=int(len(cg)), n_organic=int((g["coord"] == 0).sum()), share=float(g["coord"].mean()),
                            dd6h_coord=float(cg["dd_6h"].mean()), dd6h_matched_organic=float(cg["dd_6h_org"].mean()),
                            diff=float(diff.mean()), diff_ci=ci, p=float(p),
                            short_net_coord=float(cg["short_net"].mean()), short_net_coord_ci=M.day_ci(cg["short_net"].to_numpy(), cg["day"].to_numpy()),
                            short_minus_matched=float(dshort.mean()), short_minus_matched_ci=M.day_ci(dshort, cg["day"].to_numpy()))
            if per == "holdout":
                pvals[name] = p
                sl = g[g["coord"] == 1].copy()
                out["holdout_slices_short_net"] = M.slices(sl, "short_net")
        res[name] = out
    names = sorted(pvals, key=pvals.get)
    m = len(names)
    passed = {nm: bool(pvals[nm] <= 0.10 * (r + 1) / m) for r, nm in enumerate(names)}
    # step-up: all up to the largest passing rank pass
    last = max([r for r, nm in enumerate(names) if pvals[nm] <= 0.10 * (r + 1) / m], default=-1)
    res["bh_pass"] = {nm: r <= last for r, nm in enumerate(names)}
    dump("b15_4", res)


# ------------------------------------------------------------------ B15_2
def _series(A):
    x = np.nan_to_num(A[:, M0 + 1:M0 + 61, :2].astype(np.float32))
    mu, sd = x.mean(1, keepdims=True), x.std(1, keepdims=True) + 1e-6
    return (x - mu) / sd


def _kmedoids(D, k, restarts=8, iters=50):
    best = (np.inf, None)
    n = len(D)
    for _ in range(restarts):
        med = [RNG.integers(n)]
        for _ in range(k - 1):                       # k-medoids++ init
            d = D[:, med].min(1) ** 2
            med.append(RNG.choice(n, p=d / d.sum()))
        med = np.array(med)
        for _ in range(iters):
            lab = D[:, med].argmin(1)
            new = np.array([np.where(lab == c)[0][D[np.ix_(lab == c, lab == c)].sum(1).argmin()] if (lab == c).any() else med[c] for c in range(k)])
            if (new == med).all():
                break
            med = new
        cost = D[np.arange(n), med[D[:, med].argmin(1)]].sum()
        if cost < best[0]:
            best = (cost, med.copy())
    return best[1]


def exp2a():
    from sklearn.metrics import silhouette_score
    from tslearn.metrics import cdist_dtw
    P = M.pump_context()
    A = np.load(C / "b15/paths.npy", mmap_mode="r")
    X = _series(A)
    disc = np.where(~P["hold"].to_numpy())[0]
    samp = RNG.choice(disc, min(2500, len(disc)), replace=False)
    D = cdist_dtw(X[samp], global_constraint="sakoe_chiba", sakoe_chiba_radius=6, n_jobs=6)
    sil = {}
    meds = {}
    for k in range(3, 7):
        med = _kmedoids(D, k)
        lab = D[:, med].argmin(1)
        sil[k] = float(silhouette_score(D, lab, metric="precomputed"))
        meds[k] = med
    k = max(sil, key=sil.get)
    medoids = X[samp[meds[k]]]
    lab_all = np.concatenate([cdist_dtw(X[i:i + 2000], medoids, global_constraint="sakoe_chiba", sakoe_chiba_radius=6, n_jobs=6).argmin(1)
                              for i in range(0, len(X), 2000)])
    r = np.nan_to_num(A[:, :, 0].astype(np.float32))
    P["cluster"] = lab_all
    P["gross10"] = (1 + r[:, M0 + 1440]) / (1 + r[:, M0 + 10]) - 1
    P["ret10"] = r[:, M0 + 10]
    P[["pump_id", "cluster", "gross10", "ret10"]].to_parquet(C / "b15/clusters.parquet")
    json.dump({"k": k, "silhouette": sil, "medoid_pump_ids": P["pump_id"].to_numpy()[samp[meds[k]]].tolist(),
               "medoid_shapes": medoids.mean(0).tolist() if False else [m[:, 0].tolist() for m in medoids]},
              open(OUT / "b15_2a.json", "w"), default=float)
    print("k", k, sil, np.bincount(lab_all))


def exp2b():
    import torch
    import torch.nn as nn
    torch.manual_seed(152)
    P = M.pump_context().merge(pd.read_parquet(C / "b15/clusters.parquet"), on="pump_id")
    A = np.load(C / "b15/paths.npy", mmap_mode="r")
    k = int(P["cluster"].max()) + 1
    P["net_long10"] = P["gross10"] - P["cost"]
    P["net_short10"] = -P["gross10"] - P["cost"]
    x = np.nan_to_num(A[:, M0 + 1:M0 + 11, :].astype(np.float32))           # minutes 1..10, 4 channels
    x[:, :, 0] = np.clip(x[:, :, 0], -0.5, 1.5) * 10
    x[:, :, 2] = x[:, :, 2] - 0.5
    x[:, :, 3] = np.clip(x[:, :, 3], 0, 0.3) * 20
    X = torch.tensor(x.transpose(0, 2, 1))
    y = torch.tensor(P["cluster"].to_numpy(), dtype=torch.long)
    tr = np.where(P["ts"] < M.CAL0)[0]
    va = np.where((P["ts"] >= M.CAL0) & ~P["hold"])[0]
    ho = np.where(P["hold"])[0]
    net = nn.Sequential(nn.Conv1d(4, 32, 3, padding=1), nn.GELU(), nn.Conv1d(32, 32, 3, padding=1), nn.GELU(),
                        nn.AdaptiveAvgPool1d(1), nn.Flatten(), nn.Dropout(0.2), nn.Linear(32, k))
    w = torch.tensor(len(tr) / (k * np.bincount(y[tr].numpy(), minlength=k) + 1), dtype=torch.float32)
    opt, lossf = torch.optim.AdamW(net.parameters(), 1e-3, weight_decay=1e-3), nn.CrossEntropyLoss(weight=w)
    best, state, bad = 1e9, None, 0
    for ep in range(200):
        net.train()
        for b in np.array_split(RNG.permutation(tr), max(1, len(tr) // 256)):
            opt.zero_grad(); l = lossf(net(X[b]), y[b]); l.backward(); opt.step()
        net.eval()
        with torch.no_grad():
            vl = float(lossf(net(X[va]), y[va]))
        if vl < best - 1e-4:
            best, state, bad = vl, {kk: v.clone() for kk, v in net.state_dict().items()}, 0
        else:
            bad += 1
            if bad >= 15:
                break
    net.load_state_dict(state); net.eval()
    with torch.no_grad():
        P["pred"] = net(X).argmax(1).numpy()
    H, Dd = P.iloc[ho], P[~P["hold"]]
    res = {"k": k, "epochs": ep + 1}
    # (a) cluster outcome differences on holdout
    groups = [g["gross10"].dropna().to_numpy() for _, g in H.groupby("cluster")]
    res["a_kruskal_p"] = float(kruskal(*groups).pvalue)
    res["a_cluster_outcomes"] = {int(c): dict(n_disc=int((Dd["cluster"] == c).sum()), n_hold=int(len(g)),
                                              ret_first10=float(g["ret10"].mean()),
                                              disc_net_long10=float(Dd.loc[Dd["cluster"] == c, "net_long10"].mean()),
                                              hold_net_long10=float(g["net_long10"].mean()),
                                              hold_ci=M.day_ci(g["net_long10"].to_numpy(), g["day"].to_numpy()),
                                              hold_dd6h=float(g["dd_6h"].mean()), hold_peak_gain=float(g["peak_gain"].median()))
                                 for c, g in H.groupby("cluster")}
    # (b) classifier accuracy vs majority class, day-clustered CI of the accuracy gap
    maj = int(Dd["cluster"].mode()[0])
    gap = (H["pred"] == H["cluster"]).astype(float) - (H["cluster"] == maj).astype(float)
    res["b_acc"] = float((H["pred"] == H["cluster"]).mean()); res["b_majority_acc"] = float((H["cluster"] == maj).mean())
    res["b_gap_ci"] = M.day_ci(gap.to_numpy(), H["day"].to_numpy())
    # (c) trade: long predicted best-discovery cluster, short predicted worst
    dm = Dd.groupby("cluster")["net_long10"].mean()
    bestc, worstc = int(dm.idxmax()), int(dm.idxmin())
    L_ = H[H["pred"] == bestc]; S_ = H[H["pred"] == worstc]
    T = pd.concat([L_.assign(net=L_["net_long10"]), S_.assign(net=S_["net_short10"])])
    res["c_best_cluster"], res["c_worst_cluster"] = bestc, worstc
    res["c_long"] = dict(n=int(len(L_)), mean=float(L_["net_long10"].mean()), ci=M.day_ci(L_["net_long10"].to_numpy(), L_["day"].to_numpy()))
    res["c_short"] = dict(n=int(len(S_)), mean=float(S_["net_short10"].mean()), ci=M.day_ci(S_["net_short10"].to_numpy(), S_["day"].to_numpy()))
    res["c_combined"] = dict(n=int(len(T)), mean=float(T["net"].mean()), ci=M.day_ci(T["net"].to_numpy(), T["day"].to_numpy()))
    res["c_slices"] = M.slices(T, "net")
    res["pass"] = dict(a=res["a_kruskal_p"] < 0.05, b=res["b_gap_ci"][0] > 0, c=res["c_combined"]["ci"][0] > 0)
    dump("b15_2", res)


if __name__ == "__main__":
    {"4": exp4, "2a": exp2a, "2b": exp2b}[sys.argv[1]]()
