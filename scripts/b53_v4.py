"""B53: run prereg v4 (research/prereg_v4.md, commit 3ffc886) exactly as written. G1 strict surge ride, G2 ETH/BTC rotation
inside F17. Upbit KRW daily, long only, paper research. Output research/b53_v4.md, data/upbit_db/b53_g1_trades.parquet."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import b51_prereg_v2 as B  # noqa: E402
import b52_ride_protect as P  # noqa: E402

K = 2


def g1(O, C, V, L):
    Cf = C.ffill()
    btc_full = B.trend_w(Cf["KRW-BTC"]) == 1.0
    own = pd.DataFrame({m: B.trend_w(Cf[m]) for m in C.columns}) >= 0.75
    reg = own.mul(btc_full, axis=0).astype(bool)
    Tr, port, elig, Cn, On, cost = P.run_R(O, C, V, regime_df=reg)
    Tr["day"] = Tr.entry.dt.floor("D")
    lo, hi = P.day_ci(Tr.net.to_numpy(), Tr.day.to_numpy())
    ctrl = P.control_R(Tr, elig, Cn, On, cost)
    p_ctrl = float(np.mean(ctrl >= Tr.net.mean()))
    L += ["## G1 strict surge ride", "", "| era | trades | mean net | win | median |", "|---|---|---|---|---|"]
    pos = 0
    for en, a, z in B.ERAS:
        x = Tr[(Tr.entry >= a) & (Tr.entry < z)]
        if len(x) < 10:
            L.append(f"| {en} | {len(x)} | {x.net.mean() if len(x) else float('nan'):+.2%} (too few) | - | - |"); continue
        pos += x.net.mean() > 0
        L.append(f"| {en} | {len(x)} | {x.net.mean():+.2%} | {(x.net > 0).mean():.0%} | {x.net.median():+.2%} |")
    ok = lo > 0 and p_ctrl < 0.05 / K and pos >= 4
    L += ["", f"{len(Tr)} trades, mean {Tr.net.mean():+.2%} CI [{lo:+.2%}, {hi:+.2%}], median {Tr.net.median():+.2%}, win {(Tr.net > 0).mean():.0%}. "
          f"Random control under same regime: mean {ctrl.mean():+.2%}, p = {p_ctrl:.3f}. Eras positive {pos}/5. **{'PASS' if ok else 'FAIL'}**", ""]
    Tr.to_parquet(B.ROOT / "data/upbit_db/b53_g1_trades.parquet", index=False)
    return ok, port


def g2(O, C, V, L):
    Cf = C.ffill()
    b, e = "KRW-BTC", "KRW-ETH"
    ratio = Cf[e] / Cf[b]
    eth_share = 0.25 + 0.5 * B.trend_w(ratio)           # known at close t
    shares = pd.DataFrame({b: 1 - eth_share, e: eth_share})
    tot, f17 = [], []
    for m in (b, e):
        o, v = O[m], V[m]
        R = o.shift(-1) / o - 1
        cost = B.FEE + pd.Series(B.slip(v.rolling(30, min_periods=10).median().fillna(0).to_numpy()), index=v.index)
        pw = P.p_weight(Cf[m])
        for store, sh in ((tot, shares[m]), (f17, pd.Series(0.5, index=C.index))):
            w = (pw * sh).shift(1)                          # portfolio weight in the coin, decided at previous close
            turn = (w - w.shift(1)).abs().fillna(w.abs())
            store.append((w * R - turn * cost.shift(1)).where(w.notna()))
    s = pd.concat(tot, axis=1).sum(axis=1, min_count=2); f = pd.concat(f17, axis=1).sum(axis=1, min_count=2)
    ok_idx = s.notna() & f.notna(); s, f = s[ok_idx], f[ok_idx]
    L += ["## G2 ETH/BTC rotation inside F17 (S = G2, B = F17)", ""]
    B.era_table("G2 vs F17", s, f, L)
    eras_sh = sum(B.sharpe(s[(s.index >= a) & (s.index < z)]) >= B.sharpe(f[(f.index >= a) & (f.index < z)]) for _, a, z in B.ERAS)
    obs, p = B.boot_p(s.to_numpy(), f.to_numpy(), lambda x: B.sharpe(x), 20)
    ok = obs > 0 and p < 0.05 / K and B.maxdd(s) >= B.maxdd(f) - 0.05 and eras_sh >= 4
    L += [f"Sharpe G2 {B.sharpe(s):.2f} vs F17 {B.sharpe(f):.2f} (diff {obs:+.2f}, p = {p:.4f}); CAGR {B.cagr(s):+.0%} vs {B.cagr(f):+.0%}; "
          f"maxDD {B.maxdd(s):.0%} vs {B.maxdd(f):.0%}; Sharpe >= F17 in {eras_sh}/5 eras. **{'PASS' if ok else 'FAIL'}**", ""]
    return ok, f


def main():
    t0 = time.time()
    O, C, V = B.panel()
    L = ["# B53: prereg v4 (G1 strict surge ride, G2 ETH/BTC rotation)", "",
         f"Prereg research/prereg_v4.md (commit 3ffc886). Run {time.strftime('%Y-%m-%d %H:%M')}. K={K}, alpha {0.05 / K}. "
         "G1 is informed by B52 (optimistic by construction).", ""]
    ok1, port = g1(O, C, V, L)
    ok2, f17 = g2(O, C, V, L)
    port = port.reindex(f17.index).fillna(0)
    L += ["Information: 80% F17 + 20% G1 book: "
          f"Sharpe {B.sharpe(0.8 * f17 + 0.2 * port):.2f}, CAGR {B.cagr(0.8 * f17 + 0.2 * port):+.0%}, maxDD {B.maxdd(0.8 * f17 + 0.2 * port):.0%} "
          f"(F17 alone {B.sharpe(f17):.2f} / {B.cagr(f17):+.0%} / {B.maxdd(f17):.0%}).", "",
          "## Verdicts", "", "| test | verdict |", "|---|---|", f"| G1 strict surge ride | **{'PASS' if ok1 else 'FAIL'}** |",
          f"| G2 ETH/BTC rotation | **{'PASS' if ok2 else 'FAIL'}** |", "", f"Runtime {time.time() - t0:.0f}s."]
    (B.ROOT / "research/b53_v4.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
