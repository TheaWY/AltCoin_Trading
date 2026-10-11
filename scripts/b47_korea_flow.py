"""B47 / U6 (2026-10-04, Upbit queue): Upbit share / Korea-led vs Binance-led flows, LONG ONLY on Upbit.

Re-run of F7/F14 ideas in long form. Uses data/upbit_db h1 (Upbit KRW) + bn_h1 (Binance spot USDT, same base) and
KRW-USDT as the FX rate. All features known at the close of hour i; entry at OPEN of hour i+1 (B43 events/basket helpers).
Same pre-registered protocol and costs as B43: eras by entry time train 2024-01..2025-06 | val 2025-07..12 | TEST 2026;
per family the best-validation variant (n >= 30) is judged once on TEST, PASS = day-clustered 95% CI lower bound > 0.
Mirror = -gross - cost (shorts impossible on Upbit; information / avoid rule only).
Families
  K_share   daily cross-section on 24h Upbit value share = upbit_krw / (upbit_krw + binance_usd*usdkrw):
            top decile share surge (share / its 30d median), bottom decile surge, top decile level, bottom decile level
  K_lag     Binance-led move, Upbit lagging: Binance 1h return >= +x and Upbit 1h return - Binance 1h return <= -y
            (x in 3%,5%; y in 2%,4%) -> buy Upbit, hold 1/4/24h  (catch-up long)
  K_lead    Korea-led move: Upbit 1h return >= +5% and Binance 1h return <= +1% -> buy, hold 1/4/24h (expected avoid)
  K_prem    premium fell >= 5% over 24h (Upbit cheapened vs Binance) -> buy, hold 24/72h
Output research/b47_korea_flow.md, data/upbit_db/b47_trades.parquet. Paper research only.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from b43_upbit_hourly_suite import DB, ROOT, START, alive, basket, boot, era, events, panel  # noqa: E402


def binance(P):
    usdt = P["c"]["KRW-USDT"]
    BC, BV = {}, {}
    for f in (DB / "bn_h1").glob("*.parquet"):
        m = f"KRW-{f.stem}"
        if m not in P["c"].columns:
            continue
        b = pd.read_parquet(f).drop_duplicates("ts").set_index("ts").reindex(P["c"].index)
        BC[m] = b["c"].ffill(limit=2)
        BV[m] = b["qv"].fillna(0) * usdt
    return pd.DataFrame(BC).reindex(columns=P["c"].columns), pd.DataFrame(BV).reindex(columns=P["c"].columns), usdt


def fam_share(P, BV):
    uv = P["v"].rolling(24, min_periods=24).sum()
    bv = BV.rolling(24, min_periods=24).sum()
    share = uv / (uv + bv)
    share = share.where(bv > 0)
    surge = share / share.rolling(720, min_periods=240).median()
    ts = P["c"].index.to_numpy()
    ok = alive(P, 1e9).to_numpy()
    days = [i for i in range(1, len(ts)) if ts[i] % 86400 == 0 and ts[i] >= START]
    out = []
    for name, F in (("top decile share surge", surge), ("bottom decile share surge", -surge),
                    ("top decile share level (Korea-heavy)", share), ("bottom decile share level (Binance-heavy)", -share)):
        Fn = F.to_numpy(); rows = []
        for e in days:
            f = Fn[e - 1]; js = np.flatnonzero(ok[e - 1] & ~np.isnan(f))
            if len(js) < 20:
                continue
            k = max(len(js) // 10, 3)
            rows.append((e, list(js[np.argsort(-f[js])[:k]])))
        out.append(basket(P, rows, "K_share", f"{name}, hold 1d"))
    rows = [(e, list(np.flatnonzero(ok[e - 1] & ~np.isnan(share.to_numpy()[e - 1])))) for e in days]
    out.append(basket(P, rows, "K_share", "benchmark: all coins with a Binance pair, EW hold 1d"))
    return pd.concat(out, ignore_index=True)


def fam_lag(P, BC):
    ru = P["c"] / P["c"].shift(1) - 1
    rb = BC / BC.shift(1) - 1
    ok = alive(P, 2.7e9)
    out = []
    for x in (0.03, 0.05):
        for y in (0.02, 0.04):
            sig = (rb >= x) & (ru - rb <= -y) & ok
            for h in (1, 4, 24):
                out.append(events(P, sig, 0, h, fam="K_lag", var=f"Binance +{x:.0%}/1h, Upbit lags >= {y:.0%} -> buy, hold {h}h"))
    sig = (ru >= 0.05) & (rb <= 0.01) & ok
    for h in (1, 4, 24):
        out.append(events(P, sig, 0, h, fam="K_lead", var=f"Upbit +5%/1h, Binance <= +1% -> buy, hold {h}h"))
    return pd.concat(out, ignore_index=True)


def fam_prem(P, BC, usdt):
    prem = P["c"].div(BC.mul(usdt, axis=0)) - 1
    d24 = prem - prem.shift(24)
    ok = alive(P, 2.7e9)
    out = []
    for th in (-0.05, -0.08):
        for h in (24, 72):
            out.append(events(P, (d24 <= th) & ok, 0, h, fam="K_prem", var=f"premium fell >= {-th:.0%} in 24h -> buy, hold {h}h"))
    return pd.concat(out, ignore_index=True)


def main():
    t0 = time.time()
    P = panel()
    BC, BV, usdt = binance(P)
    print("panel", P["c"].shape, "binance pairs", int(BC.notna().any().sum()), flush=True)
    T = pd.concat([fam_share(P, BV), fam_lag(P, BC), fam_prem(P, BC, usdt)], ignore_index=True)
    T["net"] = T.gross - T.cost
    T["mirror"] = -T.gross - T.cost
    T["era"] = era(T.t.to_numpy())
    T["day"] = T.t // 86400
    T.to_parquet(DB / "b47_trades.parquet", index=False)
    L = ["# B47 / U6: Upbit share and Korea-led vs Binance-led flows (long only)", "",
         "Upbit KRW hourly + Binance spot hourly. Net after Upbit fees + slippage. Eras train 2024-01..2025-06, val 2025-07..12, "
         "TEST 2026. Best-validation variant per family judged once on TEST. Mirror = short side after costs (not executable).", ""]
    summary = []
    for fam, F in T.groupby("family", sort=False):
        L += [f"## {fam}", "", "| variant | train n / mean | val n / mean | test n / mean | test mirror |", "|---|---|---|---|---|"]
        best = None
        for var, g in F.groupby("variant", sort=False):
            cell = {er: (f"{(g.era == er).sum()} / {g[g.era == er].net.mean():+.2%}" if (g.era == er).any() else "0 / -")
                    for er in ("train", "val", "test")}
            te = g[g.era == "test"]
            L.append(f"| {var} | {cell['train']} | {cell['val']} | {cell['test']} | "
                     f"{te.mirror.mean():+.2%} |" if len(te) else f"| {var} | {cell['train']} | {cell['val']} | {cell['test']} | - |")
            va = g[g.era == "val"]
            if len(va) >= 30 and "benchmark" not in var and (best is None or va.net.mean() > best[1]):
                best = (var, va.net.mean())
        if best:
            g = F[(F.variant == best[0]) & (F.era == "test")]
            lo, hi = boot(g.net.to_numpy(), g.day.to_numpy()) if len(g) else (np.nan, np.nan)
            summary.append((fam, best[0], best[1], len(g), g.net.mean() if len(g) else np.nan, lo, hi,
                            "PASS" if len(g) >= 30 and lo > 0 else "FAIL"))
        L.append("")
    L += ["## Verdicts", "", "| family | chosen variant | val mean | test n | test mean | test 95% CI | verdict |",
          "|---|---|---|---|---|---|---|"]
    for f_, v, vm, n, tm, lo, hi, vd in summary:
        L.append(f"| {f_} | {v} | {vm:+.2%} | {n} | {tm:+.2%} | [{lo:+.2%}, {hi:+.2%}] | **{vd}** |")
    L += ["", f"Runtime {time.time() - t0:.0f}s."]
    (ROOT / "research/b47_korea_flow.md").write_text("\n".join(L) + "\n")
    print("\n".join(L[-10:]))


if __name__ == "__main__":
    main()
