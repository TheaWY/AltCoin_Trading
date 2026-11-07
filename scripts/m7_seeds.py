"""Robustness of the chart-image CNN (B2_M7_pump): retrain with 6 seeds, same data/split/rule.
Reports test-2026 and holdout-2024 net per trade for each seed (checks that the B2/B3 result is not one lucky init)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from b2_models import CNN2D, auc, boot_day, fit_torch, predict  # noqa: E402

TR_END = int(pd.Timestamp("2025-12-01").timestamp())
VA_END = int(pd.Timestamp("2026-01-01").timestamp())


def trade(p, pva, gross, cost, ts):
    lo, hi = np.quantile(pva, 0.3), np.quantile(pva, 0.7)
    side = np.where(p >= hi, 1, np.where(p <= lo, -1, 0))
    s = side != 0
    net = side[s] * gross[s] - cost[s]
    a, b = boot_day(net, ts[s] // 86400)
    return net.mean(), a, b, int(s.sum())


def main():
    z = np.load(ROOT / "data/cache/b2_ds_pump.npz", allow_pickle=True)
    h = np.load(ROOT / "data/cache/b2_ds_pump_2024.npz", allow_pickle=True)
    ts, g, c = z["ts"], z["gross"], z["cost"]
    y = (g > 0).astype(np.float32)
    tr, va, te = ts < TR_END, (ts >= TR_END) & (ts < VA_END), ts >= VA_END
    im = z["img"].astype(np.float32)[:, None] / 255.0
    im24 = h["img"].astype(np.float32)[:, None] / 255.0
    rows = []
    for seed in (1, 2, 3, 4, 5, 6):
        torch.manual_seed(seed)
        np.random.seed(seed)
        m = fit_torch(CNN2D(), im[tr], y[tr], im[va], y[va], epochs=25, bs=128)
        p, p24 = predict(m, im), predict(m, im24)
        t26 = trade(p[te], p[va], g[te], c[te], ts[te])
        t24 = trade(p24, p[va], h["gross"], h["cost"], h["ts"])
        rows.append(dict(seed=seed, auc26=auc(y[te], p[te]), net26=t26[0], lo26=t26[1], hi26=t26[2], n26=t26[3],
                         auc24=auc((h["gross"] > 0).astype(int), p24), net24=t24[0], lo24=t24[1], hi24=t24[2], n24=t24[3]))
        print(rows[-1], flush=True)
    R = pd.DataFrame(rows)
    R.to_csv(ROOT / "data/reports/b2/m7_seeds.csv", index=False)
    print(R.round(4).to_string())


if __name__ == "__main__":
    main()
