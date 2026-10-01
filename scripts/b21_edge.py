"""B21_EDGE (registered 2026-10-01). Edge-case stress tests on everything that has survived so far.

Survivors under test
  Predictive (IC on fwd24 BTC-residual return, 8h rows):  vshare_up (sign -1), oi_to_volume (+1), B19 H033 (CatBoost xs72),
                                                           B19 H013 (CatBoost xs24)
  P&L books (hold-band quintile L/S, b18_disc2 style):     H033 @72h, vshare_up_raw @168h, H013 @24h
  Event trades:                                           F2 frozen pump CNN, 2026 test set (b18_f2fix.frame)

Period: 2026 validation slice (already read by earlier batches, so this is a robustness check, not new evidence).

Tests (kill rule in brackets)
  E01 1-bar delay: signal at t, enter at t+1h                              [P&L mean <= 0 or IC loses > 50%]
  E02 2x fee + slippage                                                    [P&L mean <= 0]
  E03 drop the 5 best periods/days                                         [mean <= 0]
  E04 weekend vs weekday                                                   [sign flips]
  E05/E13 row hour 00/08/16 UTC (funding settlements = Asia/EU/US opens)  [one hour carries everything / sign flips]
  E06 coins < 30d since listing: already excluded by universe (age >= 720h); reported as a check
  E07 delisted coins settle at last traded price instead of 0 return       [sign flips]
  E09 regimes: BTC 30d up/down, market vol high/low                         [sign flips]
  E10 placebo: signal shuffled across coins within each row, 200 perms     [real not above 95th pct]
  E11 capacity: square-root impact sigma_d * sqrt(trade / dv24) at $10k/$100k/$1M gross   [net <= 0 at $10k]
  E14 liquid (top 30% dv24 in row) vs rest                                 [edge only in illiquid tail]
Output data/reports/b21/edge.{json,md}
"""
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

OUT = ROOT / "data/reports/b21"
VAL0 = DE.VAL0
RNG = np.random.default_rng(21)
H_ = 3600


def ci(x):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    return L.boot_ci(x) if len(x) > 10 else [None, None]


# ---------------------------------------------------------------- predictive part
def rank_rows(A, m):
    R = pd.DataFrame(np.where(m, A, np.nan)).rank(axis=1).to_numpy()
    return R


def row_ic(F, Y, m):
    """Spearman IC per row (rows already restricted)."""
    a, b = rank_rows(F, m), rank_rows(Y, m)
    a = a - np.nanmean(a, 1, keepdims=True); b = b - np.nanmean(b, 1, keepdims=True)
    a, b = np.nan_to_num(a), np.nan_to_num(b)
    num = (a * b).sum(1); den = np.sqrt((a * a).sum(1) * (b * b).sum(1)) + 1e-12
    ic = num / den
    ic[m.sum(1) < 30] = np.nan
    return ic


def daily_mean(v, tsr):
    s = pd.Series(v, index=tsr // 86400).dropna()
    return s.groupby(level=0).mean()


def predictive(name, F, ctx):
    ts, X, rows = ctx["ts"], ctx["X"], ctx["rows"]
    Y = X["fwd24_res"][rows]
    base_m = X["U"][rows] & np.isfinite(Y)
    Fr = F[rows]
    m = base_m & np.isfinite(Fr)
    tsr = ts[rows]
    ic = row_ic(Fr, Y, m)
    out = {}

    def put(tag, sel):
        d = daily_mean(np.where(sel, ic, np.nan), tsr)
        out[tag] = {"ic": float(d.mean()) if len(d) else None, "ci": ci(d.to_numpy()), "days": int(len(d))}
    allsel = np.ones(len(rows), bool)
    put("all", allsel)
    dow = pd.to_datetime(tsr, unit="s").dayofweek.to_numpy()
    put("E04_weekday", dow < 5); put("E04_weekend", dow >= 5)
    hr = (tsr % 86400) // 3600
    for h in (0, 8, 16):
        put(f"E05_hour{h:02d}", hr == h)
    put("E09_btc_up", ctx["btc30"] > 0); put("E09_btc_down", ctx["btc30"] <= 0)
    put("E09_vol_high", ctx["mvol"] > ctx["mvol_med"]); put("E09_vol_low", ctx["mvol"] <= ctx["mvol_med"])
    # E01 delay: signal at t, position (and return window) starts at t+1h
    Y1 = X["fwd24_res"][rows + 1]
    icl = row_ic(Fr, Y1, X["U"][rows] & np.isfinite(Y1) & np.isfinite(Fr))
    d = daily_mean(icl, tsr); out["E01_delay1h"] = {"ic": float(d.mean()), "ci": ci(d.to_numpy())}
    # E14 liquidity split
    dv = X["dv24"][rows]
    q70 = np.nanquantile(np.where(m, dv, np.nan), 0.7, axis=1, keepdims=True)
    for tag, mm in (("E14_liquid", m & (dv >= q70)), ("E14_illiquid", m & (dv < q70))):
        d = daily_mean(row_ic(Fr, Y, mm), tsr); out[tag] = {"ic": float(d.mean()), "ci": ci(d.to_numpy())}
    # E06 check
    out["E06_min_age_h_in_universe"] = float(np.nanmin(np.where(m, X["age"][rows], np.nan)))
    # E10 placebo (shuffle within row)
    real = out["all"]["ic"]
    perms = []
    for _ in range(200):
        P = Fr.copy()
        for i in range(P.shape[0]):
            idx = np.flatnonzero(m[i])
            P[i, idx] = P[i, RNG.permutation(idx)]
        perms.append(np.nanmean(daily_mean(row_ic(P, Y, m), tsr)))
    perms = np.array(perms)
    out["E10_placebo"] = {"p95_abs": float(np.percentile(np.abs(perms), 95)), "real_abs": abs(real),
                          "pass": bool(abs(real) > np.percentile(np.abs(perms), 95))}
    a = out["all"]["ic"]
    flips = [k for k in out if k.startswith(("E04", "E05", "E09", "E14")) and out[k]["ic"] is not None and np.sign(out[k]["ic"]) != np.sign(a)]
    out["verdict"] = {"sign_flips": flips, "delay_keeps": bool(np.sign(out["E01_delay1h"]["ic"]) == np.sign(a) and abs(out["E01_delay1h"]["ic"]) >= 0.5 * abs(a)),
                      "placebo_beaten": out["E10_placebo"]["pass"]}
    out["verdict"]["survives"] = bool(not flips and out["verdict"]["delay_keeps"] and out["verdict"]["placebo_beaten"])
    print(name, json.dumps(out["verdict"]), round(a, 4), flush=True)
    return out


# ---------------------------------------------------------------- P&L part
def book(F, rows, H, ctx, delay=0, cost_mult=1.0, ffill=False, gross_usd=None):
    X = ctx["X"]
    c = ctx["cf"] if ffill else X["c"]
    N = c.shape[1]
    w_prev, out = np.zeros(N), []
    for i in rows:
        if i + H + delay >= c.shape[0]:
            break
        m = X["U"][i] & np.isfinite(F[i])
        w = np.zeros(N)
        if m.sum() >= 30:
            f = np.where(m, F[i], np.nan)
            q20, q30, q70, q80 = np.nanquantile(f, [0.2, 0.3, 0.7, 0.8])
            hl, hs = (w_prev > 0) & m & (f >= q70), (w_prev < 0) & m & (f <= q30)
            Lg, Sg = (m & (f >= q80)) | hl, (m & (f <= q20)) | hs
            if Lg.sum() and Sg.sum():
                w[Lg], w[Sg] = 0.5 / Lg.sum(), -0.5 / Sg.sum()
        e = i + delay
        g = np.nan_to_num(c[e + H] / X["c"][e] - 1)
        fund = np.nan_to_num(np.nansum(X["f8"][e + 1:e + H + 1], 0) / 8.0)
        dw = np.abs(w - w_prev)
        dv = np.nan_to_num(X["dv24"][i], nan=1e6)
        rate = cost_mult * (L.FEE + L.slip(dv))
        if gross_usd:
            rate = rate + ctx["sigd"][i] * np.sqrt(dw * gross_usd / np.maximum(dv, 1e5))
        net = (w * g).sum() - (w * fund).sum() - (dw * rate).sum()
        out.append((ctx["ts"][i], net))
        w_prev = w
    return pd.DataFrame(out, columns=["ts", "net"])


def pnl(name, F, H, ctx):
    ts = ctx["ts"]
    rows = np.flatnonzero((ts % (8 * H_) == 0) & (ts >= VAL0 - 30 * 86400))[::max(1, H // 8)]
    res = {}

    def summ(b):
        v = b[b.ts >= VAL0].net.to_numpy()
        return {"mean": float(v.mean()), "ci": ci(v), "n": int(len(v))}
    base = book(F, rows, H, ctx)
    res["base"] = summ(base)
    res["E01_delay1h"] = summ(book(F, rows, H, ctx, delay=1))
    res["E02_cost2x"] = summ(book(F, rows, H, ctx, cost_mult=2.0))
    v = base[base.ts >= VAL0].net.sort_values()
    res["E03_drop_top5"] = {"mean": float(v.iloc[:-5].mean())}
    res["E07_delist_lastprice"] = summ(book(F, rows, H, ctx, ffill=True))
    for g in (1e4, 1e5, 1e6):
        res[f"E11_${int(g):,}"] = summ(book(F, rows, H, ctx, gross_usd=g))
    vb = base[base.ts >= VAL0]
    ridx = np.searchsorted(ts, vb.ts.to_numpy())
    up = ctx["btc30_full"][ridx] > 0
    res["E09_btc_up"] = {"mean": float(vb.net[up].mean())}; res["E09_btc_down"] = {"mean": float(vb.net[~up].mean())}
    perms = []
    for _ in range(100):
        P = F.copy()
        for i in rows:
            idx = np.flatnonzero(np.isfinite(P[i]))
            P[i, idx] = P[i, RNG.permutation(idx)]
        b = book(P, rows, H, ctx)
        perms.append(b[b.ts >= VAL0].net.mean())
    res["E10_placebo"] = {"p95": float(np.percentile(perms, 95)), "pass": bool(res["base"]["mean"] > np.percentile(perms, 95))}
    keys = ["base", "E01_delay1h", "E02_cost2x", "E03_drop_top5", "E07_delist_lastprice", "E11_$10,000"]
    res["verdict"] = {"positive_in": [k for k in keys if res[k]["mean"] > 0],
                      "survives": bool(all(res[k]["mean"] > 0 for k in keys) and res["base"]["ci"][0] is not None and res["base"]["ci"][0] > 0
                                       and res["E10_placebo"]["pass"])}
    print(name, H, json.dumps(res["verdict"]), round(res["base"]["mean"], 5), flush=True)
    return res


# ---------------------------------------------------------------- F2 event trades
def f2_part():
    import b18_f2fix as FX
    from m7_stress import load
    ms, meta = load()
    z = np.load(ROOT / "data/cache/b2_ds_pump.npz", allow_pickle=True)
    d = FX.add_context(FX.frame(z, z["ts"] >= FX.VA_END, ms, meta))
    t = d[d.side != 0].copy()
    net = lambda df, cm=1.0: df.side * df.g - cm * df.c  # noqa: E731
    day = lambda s, df: s.groupby(df.ts // 86400).sum()  # noqa: E731
    r = {}
    n0 = net(t)
    r["base"] = {"mean": float(n0.mean()), "ci": ci(day(n0, t).to_numpy()), "n": int(len(t))}
    n2 = net(t, 2.0)
    r["E02_cost2x"] = {"mean": float(n2.mean()), "ci": ci(day(n2, t).to_numpy())}
    dd = day(n0, t).sort_values()
    r["E03_drop_top5_days"] = {"mean_day": float(dd.iloc[:-5].mean()), "base_mean_day": float(dd.mean())}
    dow = pd.to_datetime(t.ts, unit="s").dt.dayofweek
    r["E04_weekday"] = {"mean": float(n0[dow < 5].mean())}; r["E04_weekend"] = {"mean": float(n0[dow >= 5].mean())}
    hr = (t.ts % 86400) // 3600
    for lab, sel in (("asia_00_08", hr < 8), ("eu_08_16", (hr >= 8) & (hr < 16)), ("us_16_24", hr >= 16)):
        r[f"E13_{lab}"] = {"mean": float(n0[sel].mean()), "n": int(sel.sum())}
    r["E09_btc_up"] = {"mean": float(n0[t.btc24 > 0].mean())}; r["E09_btc_down"] = {"mean": float(n0[t.btc24 <= 0].mean())}
    hv = t.vol7 > t.vol7.median()
    r["E09_highvol_coin"] = {"mean": float(n0[hv].mean())}; r["E09_lowvol_coin"] = {"mean": float(n0[~hv].mean())}
    r["long_only"] = {"mean": float(n0[t.side > 0].mean()), "n": int((t.side > 0).sum())}
    r["short_only"] = {"mean": float(n0[t.side < 0].mean()), "n": int((t.side < 0).sum())}
    perms = [float((RNG.permutation(t.side.to_numpy()) * t.g - t.c).mean()) for _ in range(1000)]
    r["E10_placebo"] = {"p95": float(np.percentile(perms, 95)), "pass": bool(r["base"]["mean"] > np.percentile(perms, 95))}
    r["E01_delay"] = "not testable on this dataset (entry fixed at trigger close); covered by live F2 fills"
    r["verdict"] = {"survives": bool(r["base"]["ci"][0] is not None and r["base"]["ci"][0] > 0 and r["E02_cost2x"]["mean"] > 0
                                     and r["E03_drop_top5_days"]["mean_day"] > 0 and r["E10_placebo"]["pass"])}
    print("F2", json.dumps(r["verdict"]), round(r["base"]["mean"], 5), flush=True)
    return r


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ts, codes, X = L.data()
    bi = codes.index("BTCUSDT")
    lc = X["lc"]
    btc30 = lc[:, bi] - L.lag(lc, 720)[:, bi]
    mret = np.nanmean(np.where(X["U"], np.abs(X["r1"]), np.nan), 1)
    mvol = pd.Series(mret).rolling(168, min_periods=24).mean().to_numpy()
    rows = np.flatnonzero((ts % (8 * H_) == 0) & (ts >= VAL0)); rows = rows[rows < len(ts) - 2]
    cf = pd.DataFrame(X["c"]).ffill().to_numpy()
    sigd = np.nan_to_num(L.SD(X["r1"], 168) * np.sqrt(24), nan=0.05)
    ctx = {"ts": ts, "X": X, "rows": rows, "btc30": btc30[rows], "btc30_full": btc30, "mvol": mvol[rows],
           "mvol_med": float(np.nanmedian(mvol[rows])), "cf": cf, "sigd": sigd}

    sig = {}
    want = {"vshare_up": -1, "oi_to_volume": +1}
    for n, _, f in DE.variables():
        if n in want:
            sig[n] = want[n] * f
        if len(sig) == len(want):
            break
    R8 = np.flatnonzero((ts % (8 * H_) == 0) & (ts >= int(pd.Timestamp("2024-03-15").timestamp())))   # = b19_ml.rows8()
    for hid in ("H033", "H013"):
        P = np.load(ROOT / f"data/cache/b19/{hid}.npy").astype(np.float32)
        s = json.load(open(ROOT / f"data/reports/b19/{hid}.json"))["sign"]
        full = np.full(X["c"].shape, np.nan, np.float32); full[R8] = s * P
        full = pd.DataFrame(full).ffill(limit=7).to_numpy()   # carry the 8h prediction to the next hours (known at its row)
        sig[hid] = full

    R = {"predictive": {}, "pnl": {}}
    for n, F in sig.items():
        R["predictive"][n] = predictive(n, F, ctx)
    for n, F, H in (("H033", sig["H033"], 72), ("vshare_up", sig["vshare_up"], 168), ("H013", sig["H013"], 24)):
        R["pnl"][f"{n}@{H}h"] = pnl(n, F, H, ctx)
    R["f2"] = f2_part()
    json.dump(R, open(OUT / "edge.json", "w"), indent=1, default=float)

    f = lambda x: "–" if x is None else f"{x:+.4f}"  # noqa: E731
    Ls = ["# B21_EDGE results (2026 slice)", "", "## Predictive signals (daily mean rank IC vs 24h residual return)", "",
          "| signal | all | delay 1h | weekday | weekend | 00h | 08h | 16h | BTC up | BTC down | vol hi | vol lo | liquid | illiquid | placebo beaten | survives |",
          "|---|" + "---|" * 15]
    for n, o in R["predictive"].items():
        g = lambda k: f(o[k]["ic"])  # noqa: E731
        Ls.append(f"| {n} | {g('all')} | {g('E01_delay1h')} | {g('E04_weekday')} | {g('E04_weekend')} | {g('E05_hour00')} | {g('E05_hour08')} | {g('E05_hour16')} | "
                  f"{g('E09_btc_up')} | {g('E09_btc_down')} | {g('E09_vol_high')} | {g('E09_vol_low')} | {g('E14_liquid')} | {g('E14_illiquid')} | "
                  f"{o['E10_placebo']['pass']} | {'YES' if o['verdict']['survives'] else 'no'} |")
    Ls += ["", "## P&L books (mean net per period)", "", "| book | base [CI] | delay 1h | 2x cost | drop top 5 | delist@last | $10k | $100k | $1M | BTC up | BTC down | placebo beaten | survives |", "|---|" + "---|" * 12]
    for n, o in R["pnl"].items():
        b = o["base"]
        Ls.append(f"| {n} | {f(b['mean'])} [{f(b['ci'][0])}, {f(b['ci'][1])}] | {f(o['E01_delay1h']['mean'])} | {f(o['E02_cost2x']['mean'])} | {f(o['E03_drop_top5']['mean'])} | "
                  f"{f(o['E07_delist_lastprice']['mean'])} | {f(o['E11_$10,000']['mean'])} | {f(o['E11_$100,000']['mean'])} | {f(o['E11_$1,000,000']['mean'])} | "
                  f"{f(o['E09_btc_up']['mean'])} | {f(o['E09_btc_down']['mean'])} | {o['E10_placebo']['pass']} | {'YES' if o['verdict']['survives'] else 'no'} |")
    o = R["f2"]
    Ls += ["", "## F2 pump CNN, 2026 trades (mean net per trade)", "", "| test | value |", "|---|---|"]
    for k, v in o.items():
        Ls.append(f"| {k} | {json.dumps(v, default=float)} |")
    (OUT / "edge.md").write_text("\n".join(Ls) + "\n")
    print("\n".join(Ls))


if __name__ == "__main__":
    main()
