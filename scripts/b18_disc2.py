"""B18_DISC2 (registered 2026-09-30): make the pass-1 novel predictors tradable by trading slowly.
Pass 1 found validated novel ICs for the Korea volume share (vshare_up / vshare_bt / vshare_korea, sign -) and
oi_to_volume (sign +), but daily quintile L/S lost the edge to turnover. Here:
  signal  = the RESIDUAL after the 10 known factors (what is actually novel), or the raw variable, sign fixed from pass-1 discovery
  book    = dollar-neutral quintile L/S (gross 1), rebalanced every H hours, H in {72, 168};
            hold band: a held name stays until it leaves the outer 30% (cuts turnover)
  combo   = mean cross-sectional rank of -vshare_korea_res and +oi_to_volume_res
  P&L     = price return over the holding period - funding paid - |dw| x (fee + slippage by dv24)
DISCOVERY 2024-04..2025-12, VALIDATION 2026. Pass: validation mean period net with a period-bootstrap 95% CI above 0
AND the same sign in discovery. Output data/reports/b18/disc2.{json,md}."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b7_lib as L  # noqa: E402
import disc_engine as DE  # noqa: E402

OUT = ROOT / "data/reports/b18"
VAL0, DISC0 = DE.VAL0, 1711929600
SIGS = {"vshare_up": -1, "vshare_bt": -1, "vshare_korea": -1, "oi_to_volume": +1}


def rows_every(H):
    ts, _, _ = L.data()
    r = np.flatnonzero((ts % (8 * 3600) == 0) & (ts >= DISC0))
    return r[::max(1, H // 8)]


def book(F, rows, H):
    ts, codes, X = L.data()
    c, N = X["c"], X["c"].shape[1]
    w_prev, out = np.zeros(N), []
    for i in rows:
        if i + H >= len(ts):
            break
        m = X["U"][i] & np.isfinite(F[i])
        w = np.zeros(N)
        if m.sum() >= 30:
            f = np.where(m, F[i], np.nan)
            q20, q30, q70, q80 = np.nanquantile(f, [0.2, 0.3, 0.7, 0.8])
            held_l, held_s = (w_prev > 0) & m & (f >= q70), (w_prev < 0) & m & (f <= q30)
            Lg, Sg = (m & (f >= q80)) | held_l, (m & (f <= q20)) | held_s
            if Lg.sum() and Sg.sum():
                w[Lg], w[Sg] = 0.5 / Lg.sum(), -0.5 / Sg.sum()
        g = np.nan_to_num(c[i + H] / c[i] - 1)
        fund = np.nan_to_num(np.nansum(X["f8"][i + 1:i + H + 1], 0) / 8.0)       # f8 is the 8h rate, carried on hourly rows
        dw = np.abs(w - w_prev)
        net = (w * g).sum() - (w * fund).sum() - (dw * (L.FEE + L.slip(np.nan_to_num(X["dv24"][i])))).sum()
        out.append((ts[i], net, (w * g).sum(), dw.sum()))
        w_prev = w
    return pd.DataFrame(out, columns=["ts", "net", "gross", "turnover"])


def ci(x, reps=4000):
    x = np.asarray(x); rng = np.random.default_rng(7)
    b = [x[rng.integers(0, len(x), len(x))].mean() for _ in range(reps)]
    return [float(v) for v in np.percentile(b, [2.5, 97.5])]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ts, _, X = L.data()
    K = dict(L.known_factors()); K.update(DE.extra_known())
    raw = {n: f for n, _, f in DE.variables() if n in SIGS}
    allrows = np.flatnonzero((ts % (8 * 3600) == 0) & (ts >= DISC0))
    sig = {}
    for n, s in SIGS.items():
        sig[f"{n}_raw"] = s * raw[n]
        sig[f"{n}_res"] = s * L.residualise(raw[n], allrows, K)
    rk = lambda A: pd.DataFrame(A).rank(axis=1, pct=True).to_numpy()
    sig["combo_res"] = np.nanmean(np.stack([rk(sig["vshare_korea_res"]), rk(sig["oi_to_volume_res"])]), 0)
    res = {}
    for name, F in sig.items():
        for H in (72, 168):
            b = book(F, rows_every(H), H)
            d, v = b[b.ts < VAL0], b[b.ts >= VAL0]
            r = {"H": H, "disc_mean": float(d.net.mean()), "disc_ci": ci(d.net), "val_mean": float(v.net.mean()), "val_ci": ci(v.net),
                 "val_n": int(len(v)), "val_gross": float(v.gross.mean()), "turnover": float(b.turnover.mean()),
                 "val_ann": float(v.net.mean() * 8760 / H)}
            r["pass"] = bool(r["val_ci"][0] > 0 and r["disc_mean"] > 0)
            res[f"{name}|{H}h"] = r
            print(name, H, json.dumps({k: (round(x, 5) if isinstance(x, float) else x) for k, x in r.items() if k != "val_ci"}), flush=True)
    json.dump(res, open(OUT / "disc2.json", "w"), indent=1)
    Ls = ["# B18_DISC2: slow L/S on the pass-1 novel predictors", "", "| signal | H | disc mean/period | val mean/period | val 95% CI | val annualised | turnover | pass |", "|---|---|---|---|---|---|---|---|"]
    for k, r in res.items():
        Ls.append(f"| {k.split('|')[0]} | {r['H']}h | {r['disc_mean']*100:+.2f}% | {r['val_mean']*100:+.2f}% | [{r['val_ci'][0]*100:+.2f}, {r['val_ci'][1]*100:+.2f}] | {r['val_ann']*100:+.1f}% | {r['turnover']:.2f} | {'PASS' if r['pass'] else '-'} |")
    (OUT / "disc2.md").write_text("\n".join(Ls) + "\n")
    print("\n".join(Ls))


if __name__ == "__main__":
    main()
