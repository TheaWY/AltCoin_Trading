"""What does the chart-image CNN (M7) look at? Train seed 2 as in B2, score 2026 test pumps, compare the
top-30% (model says long) vs bottom-30% (model says short) groups: average chart image, simple shape stats,
and realised 4h outcome. Descriptive only."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from b2_dataset import TAB  # noqa: E402
from b2_models import CNN2D, fit_torch, predict  # noqa: E402

TR_END = int(pd.Timestamp("2025-12-01").timestamp())
VA_END = int(pd.Timestamp("2026-01-01").timestamp())


def main():
    z = np.load(ROOT / "data/cache/b2_ds_pump.npz", allow_pickle=True)
    ts, g = z["ts"], z["gross"]
    y = (g > 0).astype(np.float32)
    tr, va, te = ts < TR_END, (ts >= TR_END) & (ts < VA_END), ts >= VA_END
    im = z["img"].astype(np.float32)[:, None] / 255.0
    torch.manual_seed(2)
    np.random.seed(2)
    m = fit_torch(CNN2D(), im[tr], y[tr], im[va], y[va], epochs=25, bs=128)
    p = predict(m, im)
    lo, hi = np.quantile(p[va], 0.3), np.quantile(p[va], 0.7)
    L, S = te & (p >= hi), te & (p <= lo)
    seq, tab = z["seq"], z["tab"]
    c, h, l_, v = seq[:, 0], seq[:, 1], seq[:, 2], seq[:, 3]

    def stats(mask):
        c60, h60, l60, v60 = c[mask, -60:], h[mask, -60:], l_[mask, -60:], v[mask, -60:]
        rng = h60.max(1) - l60.min(1)
        pos = (0 - l60.min(1)) / np.where(rng > 0, rng, np.nan)          # close position in the 60m range
        dd = 0 / 1 - h60.max(1)                                            # close vs 60m high (relative)
        peak_min = 60 - np.argmax(h60, axis=1)                             # minutes since the 60m high
        vol_last15 = v60[:, -15:].mean(1) - v60[:, :45].mean(1)
        return dict(n=int(mask.sum()), close_pos_in_range=np.nanmedian(pos), below_60m_high=np.nanmedian(dd),
                    minutes_since_high=np.median(peak_min), late_volume=np.nanmedian(vol_last15),
                    ret_1h=np.nanmedian(tab[mask, TAB.index("ret_1h")]), upwick=np.nanmedian(tab[mask, TAB.index("upwick_1h")]),
                    ret_24h=np.nanmedian(tab[mask, TAB.index("ret_24h")]), mean_4h=g[mask].mean(), median_4h=np.median(g[mask]),
                    up_share=(g[mask] > 0).mean())
    R = pd.DataFrame([dict(group="model says LONG", **stats(L)), dict(group="model says SHORT", **stats(S))])
    print(R.round(4).T.to_string())
    R.to_csv(ROOT / "data/reports/b2/m7_explain.csv", index=False)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 2, figsize=(9, 4.2))
        for a, mk, t in ((ax[0], L, "model says LONG (continue)"), (ax[1], S, "model says SHORT (fade)")):
            a.imshow(z["img"][mk].mean(0), cmap="gray_r", aspect="auto")
            a.set_title(f"{t}\nmean of {int(mk.sum())} test pumps")
            a.set_xlabel("last 60 minutes before the +10% hour closes")
            a.set_yticks([])
        fig.tight_layout()
        fig.savefig(ROOT / "data/reports/b2/m7_mean_images.png", dpi=140)
        print("png saved")
    except Exception as e:  # noqa: BLE001
        print("no plot", e)


if __name__ == "__main__":
    main()
