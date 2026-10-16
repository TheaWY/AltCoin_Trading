"""M-series: FAST direction from the Korean tick data (2026-10-02). Pilot on the days collected so far (since 2026-09-28);
re-run daily by the engine as the sample grows. Everything is pre-registered with a sign from the literature.

M1  order-flow imbalance (Cont-Kukanov-Stoikov 2014): L1 OFI over a 10s / 60s bin -> next-bin mid return of the same
    coin on Upbit. Sign +. Pooled (coin-standardised) NW slope, Pesaran-Timmermann, AUC, and the net edge after the
    half-spread (the only cost of a passive fill) and after a 5 bp taker fee.
M2  cross-venue lead-lag at 1 minute: Korean taker imbalance (Upbit + Bithumb trades) -> next-minute Binance perp return
    (sign +), controlling for Binance's own taker imbalance; and the reverse, Binance 1m return -> next-minute Upbit mid
    return (sign +, Korea lags). Cross-correlation of 1m returns at lags -5..+5 says who leads whom.
M3  hit-rate stability: per-day and per-KST-hour hit rates of M1/M2 so a single day cannot carry the result.

Split: all days except the last = in-sample; the last full day = holdout (grows daily; the engine re-runs this file).
Outputs research/autoresearch/mseries_<date>.md and research/autoresearch/mseries.jsonl (one row per test per run).
"""
from __future__ import annotations

import glob
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import aphx as A  # noqa: E402
import kr_ticks_io as K  # noqa: E402

TICKS = ROOT / "data/cache/kr_ticks"
OUT = ROOT / "research/autoresearch"
KST = 9 * 3600


def load(exchange, kind, cols):
    fs = sorted(glob.glob(str(TICKS / exchange / kind / "*.parquet")))
    parts = []
    for f in fs:
        try:
            d = K.read(f)[cols]
        except Exception:  # noqa: BLE001
            continue
        parts.append(d)
    d = pd.concat(parts, ignore_index=True)
    d["code"] = d["code"].astype(str)
    return d.sort_values(["code", "ts_us"], kind="stable").reset_index(drop=True)


def ofi_bins(book, bin_s):
    """Cont et al. L1 order-flow imbalance per (code, bin): e = 1{b>=b-}bq - 1{b<=b-}bq- - 1{a<=a-}aq + 1{a>=a-}aq-"""
    g = book.groupby("code", sort=False)
    b, a, bq, aq = book["b1"].to_numpy(), book["a1"].to_numpy(), book["bq1"].to_numpy(), book["aq1"].to_numpy()
    bp, ap, bqp, aqp = (g[c].shift(1).to_numpy() for c in ("b1", "a1", "bq1", "aq1"))
    e = (b >= bp) * bq - (b <= bp) * bqp - (a <= ap) * aq + (a >= ap) * aqp
    book = book.assign(e=np.where(np.isfinite(e), e, 0.0), mid=(b + a) / 2, bin=(book["ts_us"] // (bin_s * 10 ** 6)).astype("int64"),
                       depth=(bq + aq) / 2)
    agg = book.groupby(["code", "bin"], sort=True).agg(ofi=("e", "sum"), mid=("mid", "last"), depth=("depth", "mean"), n=("e", "size"),
                                                       spread=("a1", "last"))
    agg["spread"] = (agg["spread"] - book.groupby(["code", "bin"])["b1"].last()) / agg["mid"]
    agg = agg.reset_index()
    agg["ofi_n"] = agg["ofi"] / agg.groupby("code")["depth"].transform(lambda s: s.rolling(360, min_periods=30).mean().shift(1))
    # next-bin return: only when the next bin is the adjacent bin (no gaps across file boundaries / stale books)
    agg["mid_next"] = agg.groupby("code")["mid"].shift(-1); agg["bin_next"] = agg.groupby("code")["bin"].shift(-1)
    agg["r_next"] = np.where(agg["bin_next"] == agg["bin"] + 1, np.log(agg["mid_next"] / agg["mid"]), np.nan)
    agg["ts"] = agg["bin"] * bin_s
    return agg


def direction_stats(y, x, lag=5, block=60):
    ok = np.isfinite(y) & np.isfinite(x)
    y, x = y[ok], x[ok]
    if len(y) < 200:
        return {"n": int(len(y))}
    _, t, _ = A.nw_ols(y, x, lag=lag)
    hit, _, p = A.pesaran_timmermann(y, x)
    from sklearn.metrics import roc_auc_score
    auc = float(roc_auc_score((y > 0).astype(int), x)) if (y > 0).any() and (y <= 0).any() else np.nan
    rng = np.random.default_rng(0); n = len(y); aucs = []
    for _ in range(200):
        st = rng.integers(0, n, int(np.ceil(n / block))); idx = (st[:, None] + np.arange(block)[None, :]).ravel()[:n] % n
        yy, xx = y[idx], x[idx]
        if (yy > 0).any() and (yy <= 0).any():
            aucs.append(roc_auc_score((yy > 0).astype(int), xx))
    return {"n": int(n), "slope_t": float(t[1]), "pt_hit": float(hit), "pt_p": float(p), "auc": auc,
            "auc_lo": float(np.percentile(aucs, 2.5)) if aucs else np.nan, "edge_bp": float(np.mean(np.sign(x) * y) * 1e4)}


def m1(book, bin_s, hold_day):
    agg = ofi_bins(book, bin_s)
    agg["day"] = (agg["ts"] + KST) // 86400
    agg = agg[np.isfinite(agg["ofi_n"]) & np.isfinite(agg["r_next"]) & (agg["n"] >= 2)]
    ins_stats = agg[agg["day"] < hold_day].groupby("code")["ofi_n"].agg(["mean", "std"])                 # in-sample scale only
    x = ((agg["ofi_n"] - agg["code"].map(ins_stats["mean"])) / (agg["code"].map(ins_stats["std"]) + 1e-12)).to_numpy()
    y = agg["r_next"].to_numpy(); day = agg["day"].to_numpy()
    out = {"test": f"M1 OFI {bin_s}s -> next {bin_s}s Upbit mid", "sign": +1, "bin_s": bin_s, "coins": int(agg["code"].nunique())}
    for tag, sel in (("insample", day < hold_day), ("holdout", day == hold_day)):
        s = direction_stats(y[sel], x[sel], lag=max(3, 60 // bin_s), block=max(10, 600 // bin_s))
        s["half_spread_bp"] = float(np.nanmedian(agg["spread"].to_numpy()[sel]) / 2 * 1e4)
        s["edge_net_passive_bp"] = s.get("edge_bp", np.nan) - s["half_spread_bp"]; s["edge_net_taker_bp"] = s.get("edge_bp", np.nan) - s["half_spread_bp"] - 5
        out.update({f"{tag}_{k}": v for k, v in s.items()})
    # strong-signal subsample: |z| > 2 (pre-registered: OFI effect is concentrated in large imbalances)
    big = np.abs(x) > 2
    for tag, sel in (("insample", (day < hold_day) & big), ("holdout", (day == hold_day) & big)):
        s = direction_stats(y[sel], x[sel], lag=max(3, 60 // bin_s), block=max(10, 600 // bin_s))
        out.update({f"{tag}_big_{k}": v for k, v in s.items() if k in ("n", "pt_hit", "pt_p", "edge_bp", "auc")})
    # per-day hit rate (stability)
    nz = y != 0                                                                   # PT convention: zero moves are not scored
    out["hit_by_day"] = {int(d): float(((np.sign(x) * y) > 0)[nz & (day == d)].mean()) for d in np.unique(day)}
    hr = (agg["ts"].to_numpy() + KST) % 86400 // 3600
    out["hit_by_kst_hour"] = {int(h): float(((np.sign(x) * y) > 0)[nz & (hr == h)].mean()) for h in range(24) if (nz & (hr == h)).sum() > 500}
    return out


def minute_korea_flow(trades):
    t = trades.assign(minute=(trades["ts_us"] // 60_000_000).astype("int64"), v=trades["price"] * trades["qty"])
    t["sv"] = np.where(t["side"] == 1, t["v"], -t["v"])
    g = t.groupby(["code", "minute"]).agg(sv=("sv", "sum"), v=("v", "sum"), n=("v", "size")).reset_index()
    g["imb"] = g["sv"] / g["v"]
    return g


def m2(up_book, trades_kr, hold_day):
    """1-minute cross-venue tests against Binance perp 1m closes from the DB."""
    from src.data.storage import get_storage
    from oi_drop_short_paper import q
    st = get_storage()
    kr = minute_korea_flow(trades_kr)
    kr["sym"] = kr["code"].str.replace("KRW-", "", regex=False) + "/USDT"
    t0, t1 = int(kr["minute"].min() * 60), int(kr["minute"].max() * 60 + 60)
    syms = sorted(kr["sym"].unique())
    px = q(st, "SELECT symbol, ts, close, quote_volume, taker_buy_quote FROM prices_1m WHERE ts >= %s AND ts <= %s AND symbol = ANY(%s)", (t0, t1, syms))
    px = px.sort_values(["symbol", "ts"]); px["minute"] = px["ts"] // 60
    px["r_bn"] = np.log(px.groupby("symbol")["close"].shift(-1) / px["close"])                    # Binance return over the NEXT minute
    px["r_bn_lag"] = np.log(px["close"] / px.groupby("symbol")["close"].shift(1))                 # Binance return over THIS minute
    px["imb_bn"] = px["taker_buy_quote"] / px["quote_volume"].replace(0, np.nan) - 0.5
    # Upbit mid at minute end
    ub = up_book.assign(minute=(up_book["ts_us"] // 60_000_000).astype("int64"), mid=(up_book["b1"] + up_book["a1"]) / 2)
    um = ub.groupby(["code", "minute"])["mid"].last().reset_index(); um["sym"] = um["code"].str.replace("KRW-", "", regex=False) + "/USDT"
    um = um.sort_values(["sym", "minute"]); um["r_up"] = np.log(um.groupby("sym")["mid"].shift(-1) / um["mid"]); um["r_up_lag"] = np.log(um["mid"] / um.groupby("sym")["mid"].shift(1))
    d = px.merge(kr[["sym", "minute", "imb", "v"]], left_on=["symbol", "minute"], right_on=["sym", "minute"], how="left").merge(
        um[["sym", "minute", "r_up", "r_up_lag"]], on=["sym", "minute"], how="left")
    d["day"] = (d["ts"] + KST) // 86400
    out = []
    specs = [("M2a Korea taker imbalance (1m) -> next-1m Binance perp return", "imb", "r_bn", +1),
             ("M2b Binance taker imbalance (1m) -> next-1m Binance perp return (control)", "imb_bn", "r_bn", +1),
             ("M2c Binance 1m return -> next-1m Upbit mid return (Korea lags)", "r_bn_lag", "r_up", +1),
             ("M2d Upbit 1m mid return -> next-1m Binance return (Korea leads?)", "r_up_lag", "r_bn", +1),
             ("M2e Binance 1m return -> next-1m Binance return (own autocorrelation)", "r_bn_lag", "r_bn", +1)]
    for name, xc, yc, sign in specs:
        day = d["day"].to_numpy(); st_ = d[day < hold_day].groupby("symbol")[xc].agg(["mean", "std"])
        x = ((d[xc] - d["symbol"].map(st_["mean"])) / (d["symbol"].map(st_["std"]) + 1e-12)).to_numpy() * sign; y = d[yc].to_numpy()
        o = {"test": name, "sign": sign, "coins": int(d["symbol"].nunique())}
        for tag, sel in (("insample", day < hold_day), ("holdout", day == hold_day)):
            o.update({f"{tag}_{k}": v for k, v in direction_stats(y[sel], x[sel], lag=5, block=60).items()})
        ok = np.isfinite(x) & np.isfinite(y) & (y != 0)
        o["hit_by_day"] = {int(dd): float(((np.sign(x) * y) > 0)[ok & (day == dd)].mean()) for dd in np.unique(day[ok])}
        out.append(o)
    # who leads: cross-correlation of 1m returns, pooled across coins, lags -5..+5 (positive lag = Binance leads Upbit)
    xc = {}
    for lag in range(-5, 6):
        a = d.groupby("symbol")["r_bn_lag"].shift(lag).to_numpy(); b = d["r_up_lag"].to_numpy()
        ok = np.isfinite(a) & np.isfinite(b); xc[lag] = float(np.corrcoef(a[ok], b[ok])[0, 1]) if ok.sum() > 1000 else np.nan
    out.append({"test": "M2x cross-correlation corr(r_binance[t-lag], r_upbit[t]) by lag (minutes)", "xcorr": xc})
    return out


def main():
    t0 = time.time()
    book = load("upbit", "book", ["ts_us", "code", "b1", "bq1", "a1", "aq1"])
    tr_up = load("upbit", "trades", ["ts_us", "code", "price", "qty", "side"])
    try:
        tr_bt = load("bithumb", "trades", ["ts_us", "code", "price", "qty", "side"])
        trades = pd.concat([tr_up, tr_bt], ignore_index=True)
    except Exception:  # noqa: BLE001
        trades = tr_up
    days = np.unique((book["ts_us"] // 10 ** 6 + KST) // 86400)
    hold_day = int(days[-1]) if len(days) > 1 else int(days[0])
    rows = [m1(book, 10, hold_day), m1(book, 60, hold_day)] + m2(book, trades, hold_day)
    stamp = time.strftime("%F %T"); date = time.strftime("%F")
    with open(OUT / "mseries.jsonl", "a") as f:
        for r in rows:
            f.write(json.dumps({"run": stamp, **r}, default=float) + "\n")
    L = [f"# M-series (fast direction from Korean ticks) - {stamp} KST", "",
         f"Data: Upbit book {len(book):,} rows, trades {len(trades):,} rows, days {[int(x) for x in days]} (KST); holdout day = {hold_day}. "
         f"Pilot sample: {len(days)} days. The engine re-runs this daily as ticks accumulate.", "",
         "| test | sample | n | slope t | PT hit | PT p | AUC [lo] | edge bp | net passive bp | net taker bp |", "|---|---|---|---|---|---|---|---|---|---|"]
    g = lambda v, s="{:.3f}": "–" if v is None or (isinstance(v, float) and not np.isfinite(v)) else s.format(v)  # noqa: E731
    for r in rows:
        if "xcorr" in r:
            continue
        for tag in ("insample", "holdout"):
            L.append(f"| {r['test']} | {tag} | {r.get(f'{tag}_n')} | {g(r.get(f'{tag}_slope_t'), '{:+.2f}')} | {g(r.get(f'{tag}_pt_hit'))} | {g(r.get(f'{tag}_pt_p'))} | "
                     f"{g(r.get(f'{tag}_auc'))} [{g(r.get(f'{tag}_auc_lo'))}] | {g(r.get(f'{tag}_edge_bp'), '{:+.2f}')} | {g(r.get(f'{tag}_edge_net_passive_bp'), '{:+.2f}')} | {g(r.get(f'{tag}_edge_net_taker_bp'), '{:+.2f}')} |")
        if f"holdout_big_pt_hit" in r:
            L.append(f"| {r['test']} (|z|>2) | holdout | {r.get('holdout_big_n')} | | {g(r.get('holdout_big_pt_hit'))} | {g(r.get('holdout_big_pt_p'))} | {g(r.get('holdout_big_auc'))} | {g(r.get('holdout_big_edge_bp'), '{:+.2f}')} | | |")
    for r in rows:
        if "hit_by_day" in r:
            L += ["", f"- {r['test']}: hit by day {{{', '.join(f'{k}: {v:.3f}' for k, v in r['hit_by_day'].items())}}}"]
        if "hit_by_kst_hour" in r:
            L += [f"  hit by KST hour {{{', '.join(f'{k}: {v:.3f}' for k, v in r['hit_by_kst_hour'].items())}}}"]
        if "xcorr" in r:
            L += ["", f"- {r['test']}: {{{', '.join(f'{k}: {v:+.3f}' for k, v in r['xcorr'].items())}}}"]
    L += ["", "Reading: PT hit is the share of bins where sign(signal) matched the sign of the next return; edge bp = mean(sign(signal) x next return); "
          "net passive subtracts the median half-spread (a resting order), net taker also a 5 bp Upbit fee. A fast direction edge is real only if "
          "the holdout hit rate is > 0.5 with PT p < 0.05 AND net taker bp > 0 (or net passive > 0 with a credible fill), on more than one day.", ""]
    (OUT / f"mseries_{date}.md").write_text("\n".join(L))
    print("\n".join(L)); print(f"({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
