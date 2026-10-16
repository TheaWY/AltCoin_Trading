"""Stress the frozen pump chart-CNN ensemble (data/models/pump_cnn): long vs short legs, cost multipliers,
1 trade per coin per 24h (no overlap), by month. On the 2026 test and the 2024 holdout. Descriptive checks."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from b2_models import CNN2D, boot_day, predict  # noqa: E402

MOD = ROOT / "data/models/pump_cnn"
VA_END = int(pd.Timestamp("2026-01-01").timestamp())


def load():
    meta = json.loads((MOD / "meta.json").read_text())
    ms = []
    for s in meta["seeds"]:
        m = CNN2D()
        m.load_state_dict(torch.load(MOD / f"seed{s}.pt", map_location="cpu"))
        ms.append(m.to("cpu"))
    return ms, meta


def score(ms, img):
    x = img.astype(np.float32)[:, None] / 255.0
    out = []
    for m in ms:
        m.eval()
        with torch.no_grad():
            out.append(np.concatenate([torch.sigmoid(m(torch.tensor(x[k:k + 1024]))).numpy() for k in range(0, len(x), 1024)]))
    return np.mean(out, 0)


def report(name, z, sel_mask, ms, meta, L):
    p = score(ms, z["img"][sel_mask])
    g, c, ts, code = z["gross"][sel_mask], z["cost"][sel_mask], z["ts"][sel_mask], z["code"][sel_mask]
    side = np.where(p >= meta["hi"], 1, np.where(p <= meta["lo"], -1, 0))
    d = pd.DataFrame({"ts": ts, "code": code, "side": side, "g": g, "c": c})
    d = d[d["side"] != 0]
    L.append(f"## {name} ({len(d)} trades)")
    for lab, dd in (("both", d), ("long only", d[d.side > 0]), ("short only", d[d.side < 0])):
        for mult in (1.0, 1.5, 2.0):
            net = dd["side"] * dd["g"] - mult * dd["c"]
            lo, hi = boot_day(net.to_numpy(), (dd["ts"] // 86400).to_numpy())
            L.append(f"- {lab}, cost x{mult}: mean {net.mean()*100:+.2f}% [{lo*100:+.2f}, {hi*100:+.2f}] n={len(dd)}")
    dd = d.sort_values("ts")
    keep, last = [], {}
    for r in dd.itertuples():
        if r.ts - last.get(r.code, -10 ** 12) >= 86400:
            keep.append(r.Index)
            last[r.code] = r.ts
    d1 = dd.loc[keep]
    net = d1["side"] * d1["g"] - d1["c"]
    lo, hi = boot_day(net.to_numpy(), (d1["ts"] // 86400).to_numpy())
    L.append(f"- one trade per coin per 24h: mean {net.mean()*100:+.2f}% [{lo*100:+.2f}, {hi*100:+.2f}] n={len(d1)}")
    m = (d["side"] * d["g"] - d["c"]).groupby(pd.to_datetime(d["ts"], unit="s").dt.to_period("M")).agg(["mean", "size"])
    L.append("- by month: " + ", ".join(f"{k}: {v['mean']*100:+.1f}% ({int(v['size'])})" for k, v in m.iterrows()))
    L.append("")


def main():
    ms, meta = load()
    L = ["# Stress tests of the frozen pump chart-CNN (6 seeds)", ""]
    z = np.load(ROOT / "data/cache/b2_ds_pump.npz", allow_pickle=True)
    report("2026 test (Jan-Sep)", z, z["ts"] >= VA_END, ms, meta, L)
    h = np.load(ROOT / "data/cache/b2_ds_pump_2024.npz", allow_pickle=True)
    report("2024 holdout (Mar 2024 - Feb 2025)", h, np.ones(len(h["ts"]), bool), ms, meta, L)
    (ROOT / "data/reports/b2/m7_stress.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
