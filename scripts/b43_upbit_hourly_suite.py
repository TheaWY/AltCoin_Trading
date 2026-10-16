"""B43 (2026-10-04, 유리's Upbit test queue): Upbit-native LONG-ONLY hourly test suite on data/upbit_db (h1 + bn_h1).

Every family is long only (Korea: no shorting). Costs: 2 x 0.05% Upbit fee + 2 x slippage by 24h KRW value (USD tiers as
F2), turnover-weighted for daily baskets. Entry = OPEN of the hour after the signal hour closes (no look-ahead).
Pre-registered protocol (same as B42): eras by entry time  train 2024-01..2025-06 | val 2025-07..2025-12 | TEST 2026-01..now.
Per family the variant with the best VALIDATION mean (n >= 30) is chosen and judged once on TEST with a day-clustered
bootstrap 95% CI. PASS = TEST CI lower bound > 0. All variants are printed for all eras (nothing hidden).
"Mirror" column (유리: a big minus means a big plus on the other side): -gross - cost, i.e. what the opposite (short) side
would have earned. Shorts cannot be executed on Upbit, so mirrors are reported as information and as AVOID filters,
and families are paired with a long-only mirror where one exists (pump fade -> buy after the fade; crash -> rebound).
Families: F_crash (rebound after -10%/1h), F_dump24 (-25%/24h), F_fade (buy k hours after a +10% pump),
F_xs (daily cross-section: reversal, momentum, volume surge, low vol, lottery MAX), F_kimchi (Upbit vs Binance premium),
F_season (hour-of-day / weekday basket timing), F_btclead (Binance BTC hourly jump -> Upbit alt basket),
F_breadth (many pumps -> basket next day).
Output research/b43_upbit_hourly_suite.md, data/upbit_db/b43_trades.parquet. Paper research only.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/upbit_db"
FEE, USDKRW = 0.0005, 1370.0
START = int(pd.Timestamp("2024-01-01", tz="UTC").timestamp())
TRAIN_END = int(pd.Timestamp("2025-07-01", tz="UTC").timestamp())
VAL_END = int(pd.Timestamp("2026-01-01", tz="UTC").timestamp())


def era(t):
    return np.where(t < TRAIN_END, "train", np.where(t < VAL_END, "val", "test"))


def slip(v24_krw):
    dv = np.asarray(v24_krw, float) / USDKRW
    return np.where(dv > 1e8, 0.0002, np.where(dv > 2e7, 0.0005, np.where(dv > 5e6, 0.0010, 0.0020)))


def panel():
    O, Hh, Ll, C, V = {}, {}, {}, {}, {}
    for f in sorted((DB / "h1").glob("*.parquet")):
        g = pd.read_parquet(f)
        g = g[g.ts >= START - 40 * 86400].set_index("ts").sort_index()
        if len(g) < 200:
            continue
        idx = range(int(g.index[0]), int(g.index[-1]) + 3600, 3600)
        g = g.reindex(idx)
        g["c"] = g["c"].ffill()
        for k in ("o", "h", "l"):
            g[k] = g[k].fillna(g["c"])
        g["v"] = g["v"].fillna(0)
        m = f.stem
        O[m], Hh[m], Ll[m], C[m], V[m] = g.o, g.h, g.l, g.c, g.v
    P = {k: pd.DataFrame(d).sort_index() for k, d in (("o", O), ("h", Hh), ("l", Ll), ("c", C), ("v", V))}
    return P


def events(P, sig, delay, hold, cond=None, fam="", var=""):
    """sig: bool DataFrame (candle start index) known at that candle's close. Long entry at open of candle i+1+delay,
    exit at open of candle i+1+delay+hold. One open trade per coin at a time. cond(i, m) -> bool checked at entry."""
    O, V = P["o"].to_numpy(), P["v"].to_numpy()
    v24 = P["v"].rolling(24, min_periods=24).sum().to_numpy()
    ts = P["o"].index.to_numpy()
    cols = list(P["o"].columns)
    S = sig.reindex_like(P["o"]).fillna(False).to_numpy()
    out = []
    n = len(ts)
    for j in range(S.shape[1]):
        busy = -1
        for i in np.flatnonzero(S[:, j]):
            e = i + 1 + delay
            x = e + hold
            if x >= n or e <= busy or ts[i] < START:
                continue
            if np.isnan(O[e, j]) or np.isnan(O[x, j]) or O[e, j] <= 0:
                continue
            if cond is not None and not cond(i, e, j):
                continue
            g = O[x, j] / O[e, j] - 1
            c = 2 * (FEE + float(slip(v24[i, j] if not np.isnan(v24[i, j]) else 0)))
            out.append((fam, var, cols[j], int(ts[e]), g, c))
            busy = x
    return pd.DataFrame(out, columns=["family", "variant", "market", "t", "gross", "cost"])


def alive(P, min_v24=1e9):
    v24 = P["v"].rolling(24, min_periods=24).sum()
    age = P["c"].notna().cumsum()
    return (v24 >= min_v24) & (age >= 72)


def fam_events(P):
    C = P["c"]
    r1 = C / C.shift(1) - 1
    r24 = C / C.shift(24) - 1
    ok = alive(P, 2.7e9)
    Cn = C.to_numpy()
    On = P["o"].to_numpy()
    out = []
    crash = (r1 <= -0.10) & ok
    for d in (0, 1, 3):
        for h in (1, 4, 24, 72):
            out.append(events(P, crash, d, h, fam="F_crash", var=f"-10%/1h delay{d}h hold{h}h"))
    dump = (r24 <= -0.25) & ok
    for d in (0, 3):
        for h in (24, 72):
            out.append(events(P, dump, d, h, fam="F_dump24", var=f"-25%/24h delay{d}h hold{h}h"))
    pump = (r1 >= 0.10) & ok
    for h in (1, 4, 24):
        out.append(events(P, pump, 0, h, fam="F_pump", var=f"+10%/1h buy now hold{h}h"))
    fell = lambda i, e, j: On[e, j] / Cn[i, j] - 1 <= -0.10          # noqa: E731  bought >= 10% below the pump close
    for d in (2, 6, 12, 24):
        for h in (24, 72):
            out.append(events(P, pump, d, h, fam="F_fade", var=f"+10% pump, buy after {d}h hold{h}h"))
            out.append(events(P, pump, d, h, cond=fell, fam="F_fade", var=f"+10% pump, buy after {d}h if -10% from pump close, hold{h}h"))
    return pd.concat(out, ignore_index=True)


def basket(P, rows, fam, var, hold=24):
    """rows: list of (entry index e, list of column positions). Equal-weight basket from open e to open e+hold."""
    O = P["o"].to_numpy(); ts = P["o"].index.to_numpy()
    v24 = P["v"].rolling(24, min_periods=24).sum().to_numpy()
    out, prev = [], set()
    for e, js in rows:
        x = e + hold
        if x >= len(ts) or not len(js):
            continue
        js = [j for j in js if O[e, j] > 0 and not np.isnan(O[x, j])]
        if not js:
            continue
        g = float(np.mean(O[x, js] / O[e, js] - 1))
        new = set(js)
        churn = (len(new - prev) + len(prev - new)) / max(len(new), 1) if hold >= 24 else 2.0
        c = churn * float(np.mean(FEE + slip(np.nan_to_num(v24[e - 1, js]))))
        out.append((fam, var, "EW", int(ts[e]), g, c))
        prev = new if hold >= 24 else set()
    return pd.DataFrame(out, columns=["family", "variant", "market", "t", "gross", "cost"])


def fam_xs(P):
    C, V, H = P["c"], P["v"], P["h"]
    ts = C.index.to_numpy()
    ok = alive(P, 1e9).to_numpy()
    feats = {
        "rev1d (lowest 1d return)": (-(C / C.shift(24) - 1)),
        "mom1d (highest 1d return)": (C / C.shift(24) - 1),
        "mom7d (highest 7d return)": (C / C.shift(168) - 1),
        "mom28d (highest 28d return)": (C / C.shift(672) - 1),
        "vsurge (24h value / 30d avg)": V.rolling(24).sum() / (V.rolling(720, min_periods=240).mean() * 24 + 1),
        "lowvol (lowest 7d vol)": -(np.log(C).diff().rolling(168, min_periods=100).std()),
        "lowMAX (no lottery coins)": -((C / C.shift(1) - 1).rolling(168, min_periods=100).max()),
        "highMAX (lottery coins)": (C / C.shift(1) - 1).rolling(168, min_periods=100).max(),
    }
    days = [i for i in range(1, len(ts)) if ts[i] % 86400 == 0 and ts[i] >= START]
    out = []
    for name, F in feats.items():
        Fn = F.to_numpy()
        rows = []
        for e in days:
            f, a = Fn[e - 1], ok[e - 1]
            js = np.flatnonzero(a & ~np.isnan(f))
            if len(js) < 30:
                continue
            k = max(len(js) // 10, 3)
            rows.append((e, list(js[np.argsort(-f[js])[:k]])))
        out.append(basket(P, rows, "F_xs", f"top decile {name}, hold 1d"))
    rows = [(e, list(np.flatnonzero(ok[e - 1]))) for e in days]
    out.append(basket(P, rows, "F_xs", "benchmark: all liquid coins equal weight, hold 1d"))
    return pd.concat(out, ignore_index=True)


def fam_kimchi(P):
    usdt = P["c"].get("KRW-USDT")
    if usdt is None:
        return pd.DataFrame()
    prem = {}
    for f in (DB / "bn_h1").glob("*.parquet"):
        m = f"KRW-{f.stem}"
        if m not in P["c"].columns:
            continue
        b = pd.read_parquet(f).set_index("ts")["c"].reindex(P["c"].index)
        prem[m] = P["c"][m] / (b * usdt) - 1
    if not prem:
        return pd.DataFrame()
    Pr = pd.DataFrame(prem).reindex(columns=P["c"].columns)
    ts = P["c"].index.to_numpy(); ok = alive(P, 1e9).to_numpy(); Pn = Pr.to_numpy()
    days = [i for i in range(1, len(ts)) if ts[i] % 86400 == 0 and ts[i] >= START]
    out = []
    for name, sgn in (("lowest premium (discount vs Binance)", -1), ("highest premium", 1)):
        rows = []
        for e in days:
            f = Pn[e - 1]; js = np.flatnonzero(ok[e - 1] & ~np.isnan(f))
            if len(js) < 20:
                continue
            k = max(len(js) // 10, 3)
            rows.append((e, list(js[np.argsort(-sgn * f[js])[:k]])))
        out.append(basket(P, rows, "F_kimchi", f"top decile {name}, hold 1d"))
    ok2 = alive(P, 2.7e9)
    for th in (-0.03, -0.05):
        out.append(events(P, (Pr <= th) & ok2, 0, 24, fam="F_kimchi", var=f"premium <= {th:.0%} -> buy, hold 24h"))
    out.append(events(P, (Pr >= 0.10) & ok2, 0, 24, fam="F_kimchi", var="premium >= +10% -> buy, hold 24h"))
    return pd.concat(out, ignore_index=True)


def fam_timing(P):
    """season / BTC-lead / breadth: equal-weight basket of liquid alts (BTC, ETH, USDT excluded)."""
    ts = P["o"].index.to_numpy()
    ok = alive(P, 1e9)
    ex = [c for c in ("KRW-BTC", "KRW-ETH", "KRW-USDT", "KRW-USDC") if c in ok.columns]
    ok[ex] = False
    okn = ok.to_numpy()
    out = []
    kst_h = ((ts // 3600) + 9) % 24
    for h0, h1, nm in ((9, 13, "09-13 KST"), (13, 18, "13-18 KST"), (18, 24, "18-24 KST"), (0, 9, "00-09 KST")):
        rows = [(i, list(np.flatnonzero(okn[i - 1]))) for i in range(1, len(ts)) if kst_h[i] == h0 % 24 and ts[i] >= START]
        out.append(basket(P, rows, "F_season", f"hold alt basket {nm}", hold=(h1 - h0)))
    wd = pd.to_datetime(ts, unit="s").weekday
    for d, nm in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]):
        rows = [(i, list(np.flatnonzero(okn[i - 1]))) for i in range(1, len(ts)) if ts[i] % 86400 == 0 and wd[i] == d and ts[i] >= START]
        out.append(basket(P, rows, "F_season", f"hold alt basket {nm} (UTC day)", hold=24))
    f = DB / "bn_h1" / "BTC.parquet"
    if f.exists():
        b = pd.read_parquet(f).set_index("ts")["c"].reindex(P["o"].index)
        rb = (b / b.shift(1) - 1).to_numpy()
        for th, nm in ((0.015, "BTC +1.5%/1h on Binance"), (-0.015, "BTC -1.5%/1h on Binance")):
            hit = np.flatnonzero((rb >= th) if th > 0 else (rb <= th))
            for hold in (1, 4, 24):
                rows = [(i + 1, list(np.flatnonzero(okn[i]))) for i in hit if ts[i] >= START]
                out.append(basket(P, rows, "F_btclead", f"{nm} -> buy Upbit alt basket, hold {hold}h", hold=hold))
    C = P["c"]
    pumps = ((C / C.shift(1) - 1 >= 0.10) & alive(P, 2.7e9)).sum(axis=1).rolling(24, min_periods=1).sum().to_numpy()
    days = [i for i in range(1, len(ts)) if ts[i] % 86400 == 0 and ts[i] >= START]
    tr = [pumps[i - 1] for i in days if ts[i] < TRAIN_END]
    hi, lo = np.quantile(tr, 0.8), np.quantile(tr, 0.2)
    for nm, test in (("many pumps in last 24h (top 20% days)", lambda x: x >= hi), ("few pumps in last 24h (bottom 20%)", lambda x: x <= lo)):
        rows = [(i, list(np.flatnonzero(okn[i - 1]))) for i in days if test(pumps[i - 1])]
        out.append(basket(P, rows, "F_breadth", f"{nm} -> alt basket hold 1d", hold=24))
    return pd.concat(out, ignore_index=True)


def boot(x, day, n=2000, seed=4):
    rng = np.random.default_rng(seed); u = np.unique(day)
    if len(u) < 5:
        return np.nan, np.nan
    g = {k: x[day == k] for k in u}
    m = [np.concatenate([g[k] for k in rng.choice(u, len(u))]).mean() for _ in range(n)]
    return tuple(np.percentile(m, [2.5, 97.5]))


def main():
    t0 = time.time()
    P = panel()
    print("panel", P["c"].shape, flush=True)
    T = pd.concat([fam_events(P), fam_xs(P), fam_kimchi(P), fam_timing(P)], ignore_index=True)
    T["net"] = T.gross - T.cost
    T["mirror"] = -T.gross - T.cost
    T["era"] = era(T.t.to_numpy())
    T["day"] = T.t // 86400
    T.to_parquet(DB / "b43_trades.parquet", index=False)
    L = ["# B43 Upbit-native long-only hourly suite (2026-10-04)", "",
         f"{P['c'].shape[1]} Upbit KRW markets, hourly, {pd.Timestamp(START, unit='s'):%Y-%m-%d}..now. Long only; net after Upbit fees + slippage. "
         "Eras: train 2024-01..2025-06, val 2025-07..12, TEST 2026. Per family the best-validation variant is judged once on TEST. "
         "Mirror = what the opposite (short) side would have made after costs: not executable on Upbit, shown as information / avoid filter.", ""]
    summary, mirrors = [], []
    for fam, F in T.groupby("family", sort=False):
        L += [f"## {fam}", "", "| variant | train n / mean | val n / mean | test n / mean | test mirror |", "|---|---|---|---|---|"]
        best = None
        for var, g in F.groupby("variant", sort=False):
            cell = {}
            for er in ("train", "val", "test"):
                x = g[g.era == er]
                cell[er] = f"{len(x)} / {x.net.mean():+.2%}" if len(x) else "0 / -"
            te = g[g.era == "test"]
            L.append(f"| {var} | {cell['train']} | {cell['val']} | {cell['test']} | {te.mirror.mean():+.2%} |" if len(te) else
                     f"| {var} | {cell['train']} | {cell['val']} | {cell['test']} | - |")
            va = g[g.era == "val"]
            if len(va) >= 30 and (best is None or va.net.mean() > best[1]):
                best = (var, va.net.mean())
            tr = g[g.era == "train"]
            if len(te) >= 30 and len(va) >= 30 and tr.net.mean() < -0.01 and va.net.mean() < -0.01 and te.net.mean() < -0.01:
                mirrors.append((fam, var, tr.mirror.mean(), va.mirror.mean(), te.mirror.mean(), len(te)))
        if best:
            g = F[(F.variant == best[0]) & (F.era == "test")]
            lo, hi = boot(g.net.to_numpy(), g.day.to_numpy()) if len(g) else (np.nan, np.nan)
            verdict = "PASS" if len(g) >= 30 and lo > 0 else "FAIL"
            summary.append((fam, best[0], best[1], len(g), g.net.mean() if len(g) else np.nan, lo, hi, verdict))
        L.append("")
    L += ["## Verdicts (variant chosen on validation, judged on 2026 TEST)", "",
          "| family | chosen variant | val mean | test n | test mean | test 95% CI | verdict |", "|---|---|---|---|---|---|---|"]
    for f_, v, vm, n, tm, lo, hi, vd in summary:
        L.append(f"| {f_} | {v} | {vm:+.2%} | {n} | {tm:+.2%} | [{lo:+.2%}, {hi:+.2%}] | **{vd}** |")
    L += ["", "## Strongly negative in ALL three eras (mean < -1% each): the minus is real, so its mirror is real too", "",
          "| family | variant | mirror train | mirror val | mirror test | test n |", "|---|---|---|---|---|---|"]
    for m in mirrors:
        L.append(f"| {m[0]} | {m[1]} | {m[2]:+.2%} | {m[3]:+.2%} | {m[4]:+.2%} | {m[5]} |")
    L += ["", "These are (a) AVOID rules for any long book, and (b) shorts that cannot be placed on Upbit; a long-only use needs a",
          "different instrument (e.g. buying after the fade, F_fade) rather than inverting the trade.", "", f"Runtime {time.time() - t0:.0f}s."]
    (ROOT / "research/b43_upbit_hourly_suite.md").write_text("\n".join(L) + "\n")
    print("\n".join(L[-30:]))


if __name__ == "__main__":
    main()
