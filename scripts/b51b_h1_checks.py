"""B51b (EXPLORATORY, cannot change the B51 verdict): look-ahead and robustness checks on H1.
- lag check: extra 1-day delay should barely change results; same-day (cheating) weight should look much better
- each lookback alone (is the ensemble carried by one L?), BTC only, ETH only, 2x / 4x costs, trade count, time in market.
Output research/b51b_h1_checks.md. Paper research only."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import b51_prereg_v2 as B  # noqa: E402


def run(O, C, V, coins=("KRW-BTC", "KRW-ETH"), looks=B.LOOKS, lag=1, costx=1.0):
    S, R_ = [], []
    for m in coins:
        o, c, v = O[m], C[m].ffill(), V[m]
        R = o.shift(-1) / o - 1
        w = sum((c > c.rolling(L, min_periods=L).mean()).astype(float) for L in looks) / len(looks)
        w = w.where(c.rolling(200, min_periods=200).count() >= 200)
        cost = costx * (B.FEE + pd.Series(B.slip(v.rolling(30, min_periods=10).median().fillna(0).to_numpy()), index=v.index))
        wl = w.shift(lag)
        turn = (wl - wl.shift(1)).abs().fillna(wl.abs())
        S.append((wl * R - turn * cost.shift(1)).where(wl.notna())); R_.append(R)
        S[-1].attrs = {"trades": int((turn > 0).sum()), "inmkt": float(wl.mean())}
    s = pd.concat(S, axis=1).mean(axis=1, skipna=False); b = pd.concat(R_, axis=1).mean(axis=1, skipna=False)
    ok = s.notna() & b.notna()
    return s[ok], b[ok], sum(x.attrs["trades"] for x in S), np.mean([x.attrs["inmkt"] for x in S])


def main():
    O, C, V = B.panel()
    L = ["# B51b: H1 checks (EXPLORATORY)", "", "| variant | Sharpe S | Sharpe B | CAGR S | maxDD S | trades | time in mkt |", "|---|---|---|---|---|---|---|"]
    cases = [("prereg H1 (lag 1)", {}), ("lag 2 (one extra day)", {"lag": 2}), ("lag 0 (CHEAT: same-day close)", {"lag": 0}),
             ("costs x2", {"costx": 2}), ("costs x4", {"costx": 4}), ("BTC only", {"coins": ("KRW-BTC",)}),
             ("ETH only", {"coins": ("KRW-ETH",)})] + [(f"single SMA{L}", {"looks": (L,)}) for L in B.LOOKS]
    for n, kw in cases:
        s, b, tr, im = run(O, C, V, **kw)
        L.append(f"| {n} | {B.sharpe(s):.2f} | {B.sharpe(b):.2f} | {B.cagr(s):+.0%} | {B.maxdd(s):.0%} | {tr} | {im:.0%} |")
    (B.ROOT / "research/b51b_h1_checks.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
