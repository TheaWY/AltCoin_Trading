"""B6 evaluation (registered tests B6_1..B6_4, research/ml_design.md). Pure numpy/sklearn.
Out: data/reports/b6/b6_results.json + printed tables."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/reports/b6"
MODELS = ["lgb", "cat", "mlp", "gru"]
D_ = 86400
RNG = np.random.default_rng(11)


def day_boot_auc(y, p, day, reps=300):
    u, inv = np.unique(day, return_inverse=True)
    out = []
    for _ in range(reps):
        w = np.bincount(RNG.integers(0, len(u), len(u)), minlength=len(u))[inv]
        m = w > 0
        out.append(roc_auc_score(y[m], p[m], sample_weight=w[m]))
    return np.percentile(out, [2.5, 97.5]).round(4).tolist()


def day_boot_mean(x, day, reps=2000):
    u, inv = np.unique(day, return_inverse=True)
    s, c = np.bincount(inv, x), np.bincount(inv)
    out = []
    for _ in range(reps):
        b = RNG.integers(0, len(u), len(u))
        out.append(s[b].sum() / c[b].sum())
    return np.percentile(out, [2.5, 97.5]).round(5).tolist()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    res = {}
    T = pd.read_parquet(ROOT / "data/cache/lossml_tab.parquet", columns=["ts", "code", "side", "net24", "loss24", "tb_loss"])
    for m in MODELS:
        pr = pd.read_parquet(ROOT / f"data/cache/lossml_pred_{m}.parquet")
        T.loc[pr["row"].to_numpy(), m] = pr["p"].to_numpy()
        T.loc[pr["row"].to_numpy(), "fold"] = pr["fold"].to_numpy()
    O = T.dropna(subset=MODELS).copy()
    O["blend"] = O[MODELS].mean(1)
    y, day = O["loss24"].to_numpy(), O["ts"].to_numpy() // D_
    # ---- B6_1
    b1 = {}
    for m in MODELS + ["blend"]:
        b1[m] = dict(auc=round(roc_auc_score(y, O[m]), 4))
    b1["blend"]["ci"] = day_boot_auc(y, O["blend"].to_numpy(), day)
    b1["per_fold_blend"] = {int(k): round(roc_auc_score(g["loss24"], g["blend"]), 4) for k, g in O.groupby("fold")}
    b1["per_side_blend"] = {int(k): round(roc_auc_score(g["loss24"], g["blend"]), 4) for k, g in O.groupby("side")}
    # within-time AUC: only compares coins at the same timestamp and side (strips out market-timing)
    wa, wn = [], []
    for _, g in O.groupby(["ts", "side"]):
        if len(g) >= 20 and 0 < g["loss24"].mean() < 1:
            wa.append(roc_auc_score(g["loss24"], g["blend"]))
            wn.append(len(g))
    b1["within_time_auc_blend"] = round(float(np.average(wa, weights=wn)), 4)
    tb = O.dropna(subset=["tb_loss"])
    b1["tb_label_blend_auc"] = round(roc_auc_score(tb["tb_loss"], tb["blend"]), 4)
    b1["pass"] = bool(b1["blend"]["auc"] > 0.55 and b1["blend"]["ci"][0] > 0.53)
    res["B6_1"] = b1
    print("B6_1", json.dumps(b1))
    # ---- B6_2 calibration + what the deciles are worth
    O["dec"] = pd.qcut(O["blend"], 10, labels=False, duplicates="drop")
    cal = O.groupby("dec").agg(n=("blend", "size"), p_mean=("blend", "mean"), loss_rate=("loss24", "mean"),
                               net24_mean=("net24", "mean")).round(4)
    res["B6_2"] = cal.reset_index().to_dict("records")
    print(cal.to_string())
    lo = O[O["dec"] == 0]
    res["B6_2_lowest_decile_net_ci"] = day_boot_mean(lo["net24"].to_numpy(), lo["ts"].to_numpy() // D_)
    res["B6_2_lowest_decile_by_side"] = lo.groupby("side")["net24"].agg(["size", "mean"]).round(5).reset_index().to_dict("records")
    res["B6_2_lowest_decile_by_year"] = lo.groupby(pd.to_datetime(lo["ts"], unit="s").dt.year)["net24"].mean().round(5).to_dict()
    print("lowest-decile net24", lo["net24"].mean().round(5), res["B6_2_lowest_decile_net_ci"],
          res["B6_2_lowest_decile_by_side"], res["B6_2_lowest_decile_by_year"])
    # ---- B6_3 meta-label filter on the primaries
    E = pd.read_parquet(ROOT / "data/cache/lossml_ev.parquet")
    for m in MODELS:
        E[m] = np.load(ROOT / f"data/cache/lossml_ev_p_{m}.npy")
    E = E.dropna(subset=MODELS + ["net24"])
    E["blend"] = E[MODELS].mean(1)
    b3, passes = {}, 0
    for name, g in E.groupby("primary"):
        med = g["blend"].median()
        net, keep, d = g["net24"].to_numpy(), (g["blend"] <= med).to_numpy(), g["ts"].to_numpy() // D_
        imp = net[keep].mean() - net.mean()
        # bootstrap the improvement by day
        u, inv = np.unique(d, return_inverse=True)
        sa, ca = np.bincount(inv, net), np.bincount(inv)
        sk, ck = np.bincount(inv, net * keep), np.bincount(inv, keep.astype(float))
        bs = []
        for _ in range(2000):
            b = RNG.integers(0, len(u), len(u))
            bs.append(sk[b].sum() / max(ck[b].sum(), 1) - sa[b].sum() / ca[b].sum())
        ci = np.percentile(bs, [2.5, 97.5]).round(5).tolist()
        ok = bool(imp > 0 and ci[0] > 0)
        passes += ok
        b3[name] = dict(n=len(g), auc=round(roc_auc_score(net < 0, g["blend"]), 4), mean_all=round(net.mean(), 5),
                        mean_kept=round(net[keep].mean(), 5), mean_skipped=round(net[~keep].mean(), 5),
                        improvement=round(imp, 5), ci=ci, pass_=ok)
        print("B6_3", name, b3[name])
    b3["pass"] = passes >= 3
    res["B6_3"] = b3
    # ---- B6_4 real paper trades
    R = pd.read_parquet(ROOT / "data/cache/lossml_pt.parquet")
    for m in MODELS:
        R[m] = np.load(ROOT / f"data/cache/lossml_pt_p_{m}.npy")
    R["blend"] = R[MODELS].mean(1)
    b4 = {}
    for nm, g in (("all", R), ("directional", R[R["strategy"] != "pairs_statarb"]), ("signal_xs", R[R["strategy"] == "signal_xs"])):
        yl, p = g["loss"].to_numpy(), g["blend"].to_numpy()
        boots = []
        for _ in range(2000):
            i = RNG.integers(0, len(g), len(g))
            if 0 < yl[i].mean() < 1:
                boots.append(roc_auc_score(yl[i], p[i]))
        skip = p > 0.6
        b4[nm] = dict(n=len(g), loss_rate=round(yl.mean(), 3), auc=round(roc_auc_score(yl, p), 4),
                      ci=np.percentile(boots, [2.5, 97.5]).round(3).tolist(), n_skipped=int(skip.sum()),
                      pnl_all=round(g["pnl"].astype(float).sum(), 1), pnl_skip06=round(g["pnl"].astype(float)[~skip].sum(), 1),
                      pnl_of_skipped=round(g["pnl"].astype(float)[skip].sum(), 1),
                      loss_rate_skipped=round(yl[skip].mean(), 3) if skip.any() else None,
                      loss_rate_kept=round(yl[~skip].mean(), 3))
        print("B6_4", nm, b4[nm])
    b4["by_strategy"] = R.groupby("strategy").agg(n=("loss", "size"), loss_rate=("loss", "mean"), p_mean=("blend", "mean"),
                                                  pnl=("pnl", "sum")).round(3).reset_index().to_dict("records")
    b4["p_range"] = [round(R["blend"].min(), 3), round(R["blend"].max(), 3)]
    res["B6_4"] = b4
    print(pd.DataFrame(b4["by_strategy"]).to_string())
    R[["src", "id", "strategy", "code", "side", "opened_at", "pnl", "loss", "blend"] + MODELS].to_csv(OUT / "paper_trades_scored.csv", index=False)
    json.dump(res, open(OUT / "b6_results.json", "w"), indent=1, default=float)


if __name__ == "__main__":
    main()
