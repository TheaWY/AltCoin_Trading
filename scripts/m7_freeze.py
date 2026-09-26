"""Freeze the forward-test object for the pump chart-CNN: 6 seeds of B2_M7 trained on 2025-03..11 with Dec-2025
early stopping (exactly the objects tested in m7_seeds.py). Saves state dicts and the Dec-2025 thresholds of the
seed-averaged probability. Out: data/models/pump_cnn/"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from b2_models import CNN2D, fit_torch, predict  # noqa: E402

OUT = ROOT / "data/models/pump_cnn"
TR_END = int(pd.Timestamp("2025-12-01").timestamp())
VA_END = int(pd.Timestamp("2026-01-01").timestamp())


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    z = np.load(ROOT / "data/cache/b2_ds_pump.npz", allow_pickle=True)
    ts = z["ts"]
    y = (z["gross"] > 0).astype(np.float32)
    tr, va = ts < TR_END, (ts >= TR_END) & (ts < VA_END)
    im = z["img"].astype(np.float32)[:, None] / 255.0
    pv = []
    for seed in (1, 2, 3, 4, 5, 6):
        torch.manual_seed(seed)
        np.random.seed(seed)
        m = fit_torch(CNN2D(), im[tr], y[tr], im[va], y[va], epochs=25, bs=128)
        torch.save({k: v.cpu() for k, v in m.state_dict().items()}, OUT / f"seed{seed}.pt")
        pv.append(predict(m, im[va]))
        print("seed", seed, flush=True)
    p = np.mean(pv, 0)
    meta = dict(seeds=[1, 2, 3, 4, 5, 6], lo=float(np.quantile(p, 0.3)), hi=float(np.quantile(p, 0.7)),
                trained="2025-03..2025-11", validated="2025-12", source="B2_M7_pump / m7_seeds.py",
                rule="+10% hour (close vs close 60m earlier), 24h volume >= $2M, listed >= 72h; long if mean p >= hi, short if <= lo; hold 4h from next-minute open")
    (OUT / "meta.json").write_text(json.dumps(meta, indent=1))
    print(meta)


if __name__ == "__main__":
    main()
