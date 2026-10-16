"""B55: prereg v6 (research/prereg_v6_combos.md) - 54 combined books = F17 core + alt satellite (UP detector) x DOWN
detector x LOSS minimiser x satellite share. Same engine as B54. Output research/b55_combos.md, data/upbit_db/b55_results.parquet."""
from __future__ import annotations

import itertools
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import b51_prereg_v2 as B  # noqa: E402
import b54_sweep100 as S  # noqa: E402  (panel, engine, universe, breakout)

IDX, CF, C, V = S.IDX, S.CF, S.C, S.V
F17W = S.W_f17()
SMA = lambda x, n: x.rolling(n, min_periods=n).mean()  # noqa: E731


def sat_upvol():
    """S3: up & volatile entry, exit first close < SMA10, 60d cap, 10 slots."""
    lr = np.log(CF).diff()
    sig = ((CF / CF.shift(7) - 1 >= 0.15) & (lr.rolling(7, min_periods=7).std() >= 1.5 * lr.rolling(90, min_periods=60).std())
           & (CF > SMA(CF, 20)) & S.MEM).mul(S.BTCW >= 0.5, axis=0)
    Sg, Cn, s10 = sig.fillna(False).astype(bool).to_numpy(), CF.to_numpy(), SMA(CF, 10).to_numpy()
    T = len(IDX); W = np.zeros(Cn.shape); held = {}
    for i in range(T - 1):
        for j in [j for j, x in held.items() if x <= i]:
            del held[j]
        cand = sorted([j for j in np.flatnonzero(Sg[i]) if j not in held], key=lambda j: -np.nan_to_num(S.VR[i, j]))
        for j in cand[: max(10 - len(held), 0)]:
            x = min(i + 60, T - 1)
            for k in range(i + 1, min(i + 61, T - 1)):
                if Cn[k, j] < s10[k, j]:
                    x = k; break
            W[i:x, j] = 0.1; held[j] = x
    return pd.DataFrame(W, index=IDX, columns=C.columns)


AIDX = S.alt_index()                                    # alt index level known at close t


def down_n1():
    a = AIDX.to_numpy(); hi = AIDX.rolling(20, min_periods=20).max().to_numpy(); s20 = SMA(AIDX, 20).to_numpy()
    off = np.zeros(len(a), bool); on = False
    for t in range(1, len(a)):
        if not np.isfinite(a[t]):
            continue
        if (np.isfinite(a[t - 1]) and a[t] <= 0.90 * a[t - 1]) or (np.isfinite(hi[t]) and a[t] <= 0.85 * hi[t]):
            on = True
        elif on and np.isfinite(s20[t]) and a[t] > s20[t]:
            on = False
        off[t] = on
    return pd.Series(off, index=IDX)


def down_n2():
    above = (CF > SMA(CF, 50)) & S.U30
    share = above.sum(axis=1) / S.U30.sum(axis=1).replace(0, np.nan)
    return (share < 0.30).fillna(False)


def loss_l1():
    rv = np.log(AIDX).diff().rolling(20, min_periods=20).std()
    return (rv.rolling(365, min_periods=120).median() / rv).clip(upper=1.0).fillna(1.0)


def book(sat, s, off, scale, ddbrake):
    satw = sat.mul((~off).astype(float) * scale, axis=0)
    W = F17W * (1 - s) + satw * s
    if ddbrake:
        r = S.run(W); eq = (1 + r).cumprod(); dd = (eq / eq.cummax() - 1).shift(1)
        half = pd.Series(np.where(dd < -0.15, 0.5, 1.0), index=IDX)
        W = F17W * (1 - s) + satw.mul(half, axis=0) * s
    return W


def registry():
    sats = {"S1": lambda: S.breakout(20, 1.5, "trail20"), "S2": lambda: S.breakout(20, 1.5, own=True), "S3": sat_upvol}
    downs = {"N0": lambda: pd.Series(False, index=IDX), "N1": down_n1, "N2": down_n2}
    losses = {"L0": "none", "L1": "vol", "L2": "dd"}
    out = []
    for (sk, dk, lk, sh) in itertools.product(sats, downs, losses, (0.2, 0.4)):
        out.append((f"{sk}_{dk}_{lk}_s{int(sh * 100)}", sk, dk, lk, sh))
    return sats, downs, out


def main():
    t0 = time.time()
    sats, downs, reg = registry()
    assert len(reg) == 54
    satW = {k: f() for k, f in sats.items()}; offs = {k: f() for k, f in downs.items()}; l1 = loss_l1()
    f17 = S.run(F17W)
    rows = []
    for rid, sk, dk, lk, sh in reg:
        W = book(satW[sk], sh, offs[dk], l1 if lk == "L1" else 1.0, lk == "L2")
        r = S.run(W)
        d_s, d_b = S.window(r, *S.DISC), S.window(f17, *S.DISC)
        h_s, h_b = S.window(r, *S.HOLD), S.window(f17, *S.HOLD)
        obs, p = B.boot_p(d_s.to_numpy(), d_b.to_numpy(), lambda x: B.sharpe(x), 20, draws=2000)
        hobs, hp = B.boot_p(h_s.to_numpy(), h_b.to_numpy(), lambda x: B.sharpe(x), 20, draws=2000)
        rows.append(dict(id=rid, sat=sk, down=dk, loss=lk, share=sh, d_sh=B.sharpe(d_s), d_bsh=B.sharpe(d_b), d_diff=obs, d_p=p,
                         d_dd=B.maxdd(d_s), d_bdd=B.maxdd(d_b), d_cagr=B.cagr(d_s), h_sh=B.sharpe(h_s), h_bsh=B.sharpe(h_b),
                         h_diff=hobs, h_p=hp, h_dd=B.maxdd(h_s), h_bdd=B.maxdd(h_b), h_cagr=B.cagr(h_s), h_bcagr=B.cagr(h_b)))
        print(time.strftime("%T"), rid, f"disc {obs:+.2f} p={p:.3f} | hold {hobs:+.2f}", flush=True)
    D = pd.DataFrame(rows)
    D["bhy"] = S.bhy(D.d_p.to_numpy())
    D["survivor"] = D.bhy & (D.d_diff > 0) & (D.d_dd >= D.d_bdd - 0.05)
    ns = int(D.survivor.sum())
    D["PASS"] = D.survivor & (D.h_diff > 0) & (D.h_p < 0.05 / max(ns, 1)) & (D.h_dd >= D.h_bdd - 0.05)
    D.to_parquet(B.ROOT / "data/upbit_db/b55_results.parquet", index=False)
    L = ["# B55: combined systems (prereg v6): F17 core + UP satellite x DOWN detector x LOSS minimiser", "",
         f"Run {time.strftime('%Y-%m-%d %H:%M')}, {time.time() - t0:.0f}s. Benchmark F17: discovery Sharpe {D.d_bsh.iloc[0]:.2f}, "
         f"holdout {D.h_bsh.iloc[0]:.2f} (CAGR {D.h_bcagr.iloc[0]:+.0%}). BHY q<=0.10 across 54 -> {ns} survivors.", "",
         "| id | disc Sh | diff | p | BHY | disc maxDD vs F17 | hold Sh | hold diff | hold p | hold CAGR | hold maxDD vs F17 | verdict |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in D.sort_values("d_p").itertuples():
        v = "PASS" if r.PASS else ("survivor FAIL" if r.survivor else "-")
        L.append(f"| {r.id} | {r.d_sh:.2f} | {r.d_diff:+.2f} | {r.d_p:.3f} | {'Y' if r.bhy else ''} | {r.d_dd:.0%} vs {r.d_bdd:.0%} | "
                 f"{r.h_sh:.2f} | {r.h_diff:+.2f} | {r.h_p:.3f} | {r.h_cagr:+.0%} | {r.h_dd:.0%} vs {r.h_bdd:.0%} | {v} |")
    L += ["", "## Component averages (discovery diff / holdout diff)", ""]
    for col in ("sat", "down", "loss", "share"):
        g = D.groupby(col)[["d_diff", "h_diff"]].mean()
        L.append(f"- {col}: " + "; ".join(f"{k}: {a:+.2f} / {b:+.2f}" for k, (a, b) in g.iterrows()))
    (B.ROOT / "research/b55_combos.md").write_text("\n".join(L) + "\n")
    print("\n".join(L[:4]))


if __name__ == "__main__":
    main()
