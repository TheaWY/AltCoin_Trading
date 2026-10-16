"""Batch B5 (research/batch_B5.yaml): Jiang-Kelly-Xiu style chart-image CNN on the cross-section of liquid perps.

  .venv/bin/python -W ignore scripts/b5_jkx.py build      # images + labels -> data/cache/b5_{I20,I60h}.npz
  .venv/bin/python -W ignore scripts/b5_jkx.py run        # train/validate/test once, report, ledger
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
H_, D_ = 3600, 86400
TR_END = int(pd.Timestamp("2025-07-01").timestamp())
VA_END = int(pd.Timestamp("2025-10-01").timestamp())
T0 = int(pd.Timestamp("2024-03-08").timestamp())
T1 = int(pd.Timestamp("2026-09-22").timestamp())
Y1 = int(pd.Timestamp("2025-03-01").timestamp())
FEE = 0.0005


def slip(dv):
    dv = np.asarray(dv, float)
    return np.select([dv > 1e8, dv > 2e7, dv > 5e6], [0.0002, 0.0005, 0.0010], 0.0020)


def render(o, h, l_, c, v, px_per_bar: int) -> np.ndarray:
    """64 x (bars*px) image: price area rows 0..50, volume rows 52..63 (JKX layout, scaled down)."""
    n = len(c)
    W = n * px_per_bar
    img = np.zeros((64, W), np.uint8)
    lo, hi = np.nanmin(l_), np.nanmax(h)
    if not (np.isfinite(lo) and np.isfinite(hi) and hi > lo):
        return img
    top = 51

    def y(x):
        return int(np.clip((hi - x) / (hi - lo) * (top - 1), 0, top - 1))
    vm = np.nanmax(v)
    for k in range(n):
        x0 = k * px_per_bar
        if px_per_bar == 3:
            img[y(o[k]), x0] = 255
            img[y(h[k]):y(l_[k]) + 1, x0 + 1] = 255
            img[y(c[k]), x0 + 2] = 255
            xv = x0 + 1
        else:
            img[y(h[k]):y(l_[k]) + 1, x0] = 255
            img[y(c[k]), x0] = 255
            xv = x0
        if vm > 0 and np.isfinite(v[k]):
            hh = int(v[k] / vm * 11)
            if hh > 0:
                img[64 - hh:, xv] = 255
    return img


def panel() -> pd.DataFrame:
    cols = ["code", "ts", "c", "hi", "lo", "qv", "dv24", "age_h"]
    a = pd.read_parquet(ROOT / "data/cache/b2_hourly_2024.parquet", columns=cols)
    b = pd.read_parquet(ROOT / "data/cache/b2_hourly.parquet", columns=cols)
    return pd.concat([a[a["ts"] < Y1], b[b["ts"] >= Y1]], ignore_index=True).sort_values(["code", "ts"])


def build() -> None:
    P = panel()
    out20, out60 = [], []
    for code, g in P.groupby("code", sort=False):
        g = g.set_index("ts")
        idx = np.arange(int(g.index[0]), int(g.index[-1]) + H_, H_)
        g = g.reindex(idx)
        c = g["c"].ffill().to_numpy(float)
        hi = g["hi"].fillna(g["c"]).ffill().to_numpy(float)
        lo = g["lo"].fillna(g["c"]).ffill().to_numpy(float)
        qv = g["qv"].fillna(0).to_numpy(float)
        dv = g["dv24"].to_numpy(float)
        age = g["age_h"].to_numpy(float)
        o = np.concatenate([[c[0]], c[:-1]])                       # hourly open ~ previous close
        days = np.flatnonzero((idx % D_ == 0) & (idx >= T0) & (idx <= T1))
        for i in days:
            if not (dv[i] >= 2e7 and age[i] >= 72 * 1.0) or i < 20 * 24 or i + 5 * 24 >= len(c):
                continue
            # I60h: last 60 hourly bars ending at i (bar i = hour ending at ts[i])
            s = slice(i - 59, i + 1)
            img60 = render(o[s], hi[s], lo[s], c[s], qv[s], 1)
            f24 = c[i + 24] / c[i] - 1
            # I20: 20 daily bars ending at i
            dd = [slice(i - 24 * (k + 1) + 1, i - 24 * k + 1) for k in range(19, -1, -1)]
            do = np.array([o[x][0] for x in dd])
            dh = np.array([hi[x].max() for x in dd])
            dl = np.array([lo[x].min() for x in dd])
            dc = np.array([c[x][-1] for x in dd])
            dvv = np.array([qv[x].sum() for x in dd])
            img20 = render(do, dh, dl, dc, dvv, 3)
            f5 = c[i + 120] / c[i] - 1
            out60.append((idx[i], code, dv[i], f24, img60))
            out20.append((idx[i], code, dv[i], f5, img20))
    for name, out in (("I60h", out60), ("I20", out20)):
        ts = np.array([x[0] for x in out])
        fwd = np.array([x[3] for x in out])
        df = pd.DataFrame({"ts": ts, "f": fwd})
        exc = (df["f"] - df.groupby("ts")["f"].transform("median")).to_numpy()
        np.savez_compressed(ROOT / f"data/cache/b5_{name}.npz", ts=ts, code=np.array([x[1] for x in out]),
                            dv=np.array([x[2] for x in out]), fwd=fwd, exc=exc,
                            img=np.stack([x[4] for x in out]).astype(np.uint8))
        print(name, len(out), flush=True)


def run() -> int:
    import torch
    sys.path.insert(0, str(ROOT / "scripts"))
    from b2_models import CNN2D, auc, fit_torch, predict  # noqa: E402
    from b3_run import boot_day  # noqa: E402
    led = pd.read_csv(ROOT / "research/trial_ledger.csv")
    if led["hypothesis"].astype(str).str.startswith("B5_").any():
        print("B5 already run; refusing")
        return 1
    rows = []
    for name, hid, hold_days, rebal in (("I20", "B5_JKX_I20_R5", 5, "monday"), ("I60h", "B5_JKX_I60h_R24", 1, "daily")):
        z = np.load(ROOT / f"data/cache/b5_{name}.npz", allow_pickle=True)
        ts, exc, fwd, dv = z["ts"], z["exc"], z["fwd"], z["dv"]
        y = (exc > 0).astype(np.float32)
        tr, va, te = ts < TR_END, (ts >= TR_END) & (ts < VA_END), ts >= VA_END
        x = z["img"].astype(np.float32)[:, None] / 255.0
        torch.manual_seed(7)
        np.random.seed(7)
        m = fit_torch(CNN2D(), x[tr], y[tr], x[va], y[va], epochs=20, bs=256)
        p = predict(m, x[te])
        d = pd.DataFrame({"ts": ts[te], "p": p, "fwd": fwd[te], "dv": dv[te], "y": y[te]})
        if rebal == "monday":
            d = d[((d["ts"] // D_) + 3) % 7 == 0]
        d["q"] = d.groupby("ts")["p"].rank(pct=True)
        L, S = d[d["q"] > 0.9], d[d["q"] <= 0.1]
        net = pd.concat([pd.DataFrame({"ts": L["ts"], "net": L["fwd"] - 2 * (FEE + slip(L["dv"]))}),
                         pd.DataFrame({"ts": S["ts"], "net": -S["fwd"] - 2 * (FEE + slip(S["dv"]))})])
        daily = net.groupby("ts")["net"].mean() / hold_days             # per-day return of the book
        lo, hi, pv = boot_day(daily.to_numpy(), (daily.index // D_).to_numpy())
        r = dict(id=hid, n_train=int(tr.sum()), n_test=int(te.sum()), auc_test=auc(y[te], p), rebalances=len(daily),
                 mean_daily=daily.mean(), ci_lo=lo, ci_hi=hi, p=pv, long_leg=(L["fwd"].mean()), short_leg=(-S["fwd"].mean()))
        r["pass"] = bool(r["mean_daily"] > 0 and lo > 0)
        rows.append(r)
        print(r, flush=True)
    R = pd.DataFrame(rows)
    (ROOT / "data/reports/b5").mkdir(parents=True, exist_ok=True)
    R.to_csv(ROOT / "data/reports/b5/summary.csv", index=False)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    add = pd.DataFrame([dict(ts=now, run_id=f"B5-{int(time.time())}", hypothesis=r["id"], tier="confirmatory",
                             period="test 2025-10..2026-09", mean_daily_net=r["mean_daily"], n_trades=r["rebalances"],
                             note=f"auc={r['auc_test']:.3f}") for r in rows])
    pd.concat([led, add], ignore_index=True).to_csv(ROOT / "research/trial_ledger.csv", index=False)
    print(R.round(5).to_string())
    return 0


if __name__ == "__main__":
    if sys.argv[1] == "build":
        build()
    else:
        sys.exit(run())
