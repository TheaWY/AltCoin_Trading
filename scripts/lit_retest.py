"""Literature-driven retest of every candidate predictor.

Follows the test design in the literature review (reports/Crypto return
predictors literature.md):

  universe   point-in-time, delisted coins included, trailing 24h dollar
             volume >= $1M and >= 14 days listed
  targets    excess return vs the cross-sectional median (1h / 4h / 24h),
             same with a 1 hour entry delay, |excess 24h| (magnitude),
             tails up10 / dn10 / win10 within 24h, up10 within 1h and 4h
  features   deep_panel.parquet + new literature variables (excess vs BTC,
             21d momentum, CTREND-style composite, Amihud, MAX, semivariance,
             30d-standardised taker flow, same-hour volume z, OI x price
             quadrant, funding interval-adjusted, Binance bookDepth,
             Coinalyze cross-exchange OI, Tardis premium / liquidations)
  method     hourly Spearman IC with Newey-West t, per liquidity tercile,
             orthogonalised on past returns / taker flow / vol / size,
             6 time folds sign check, last 35 days held out,
             Benjamini-Hochberg FDR, BTC regime split,
             quintile long-short and long-only vs BTC net of fees, slippage
             and funding, purged walk-forward Ridge vs LightGBM, tail models
             scored by precision@10 and lift, event studies.

Run:  .venv/bin/python -W ignore scripts/lit_retest.py
Out:  data/reports/lit/latest.md + latest.json
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PANEL = ROOT / "data" / "cache" / "deep_panel.parquet"
LITP = ROOT / "data" / "cache" / "lit_panel.parquet"
BOOK = ROOT / "data" / "cache" / "bookdepth"
FUND = ROOT / "data" / "cache" / "funding"
TARD = ROOT / "data" / "tardis"
OUT = ROOT / "data" / "reports" / "lit"
H_ = 3600
HOLDOUT_DAYS = 35
FEE = 0.0005            # taker fee per side
SLIP = (0.0020, 0.0008, 0.0003)   # per side, liquidity tercile low/mid/high

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("lit")

CONTROLS = ["ret_60m", "ret_1440m", "ret_10080m", "taker_60m", "taker_1440m", "rv_24h", "dv24_log"]
DROP = {"symbol", "ts", "code", "terc", "univ", "hour", "weekday", "age_censored", "btc_fwd_24h", "fund_h"}
LABEL_PREFIX = ("mfe_", "mae_", "ret_1h", "ret_4h", "ret_24h", "up10_", "win10_", "dn10_", "ex_ret_", "d1_ex_",
                "abs_ex_")

FAMILY = {
    "price": ("ret_", "ex_", "exb_", "mom_", "ctrend", "dhi_", "dlo_", "rsi_", "xs_ret", "rk_ret", "max1m", "min1m",
              "max5m", "bursts"),
    "vol": ("rv_", "skew_", "kurt_", "max_7d", "min_7d", "semi_", "rk_rv"),
    "liq": ("vsurge", "nsurge", "tsize", "dv24", "amihud", "volz", "turnover", "rk_dv", "rk_vsurge"),
    "flow": ("taker", "cvd", "tkz_", "rk_taker"),
    "deriv": ("oi_", "ls_", "smart", "fut_taker", "funding", "fund_", "rk_oi", "rk_funding", "oiq", "cz_"),
    "book": ("bk_",),
    "tardis": ("td_",),
    "size": ("mcap", "age_", "rk_mcap"),
    "korea": ("kr_", "kimchi"),
    "market": ("btc_", "mkt_", "breadth", "fng"),
    "event": ("ev_",),
}


def family(c: str) -> str:
    for f, pre in FAMILY.items():
        if c.startswith(pre):
            return f
    return "other"


# ------------------------------------------------------------------ panel

def _grp_roll(s: pd.Series, sym: pd.Series, w: int, fn: str, minp: int) -> pd.Series:
    r = s.groupby(sym, sort=False).rolling(w, min_periods=minp)
    return getattr(r, fn)().reset_index(level=0, drop=True).sort_index()


def funding_hours() -> dict[str, float]:
    out = {}
    for f in FUND.glob("*.parquet"):
        try:
            ts = pd.read_parquet(f, columns=["ts"])["ts"]
            d = ts.diff().dropna()
            out[f.stem] = float(d.tail(60).median() / H_) if len(d) else 8.0
        except Exception:  # noqa: BLE001
            out[f.stem] = 8.0
    return out


def book_features(codes: list[str], hours: dict[str, np.ndarray]) -> pd.DataFrame:
    rows = []
    for code in codes:
        f = BOOK / f"{code}.parquet"
        if not f.exists() or code not in hours:
            continue
        b = pd.read_parquet(f).sort_values("ts")
        # 5-minute bar stamped at its start; known once the bar closes
        b["avail"] = b["ts"] + 300
        hs = pd.DataFrame({"ts": hours[code]})
        m = pd.merge_asof(hs, b, left_on="ts", right_on="avail", direction="backward", tolerance=1800,
                          suffixes=("", "_b"))
        dep2 = np.log(m["bid_2"] + m["ask_2"])
        dep02 = np.log(m["bid_02"] + m["ask_02"])
        one = pd.DataFrame({
            "code": code, "ts": hs["ts"],
            "bk_imb_02": ((m["bid_02"] - m["ask_02"]) / (m["bid_02"] + m["ask_02"])).to_numpy(),
            "bk_imb_1": m["imb_1"].to_numpy(), "bk_imb_2": m["imb_2"].to_numpy(), "bk_imb_5": m["imb_5"].to_numpy(),
            "bk_dep02": dep02.to_numpy(), "bk_dep2": dep2.to_numpy(),
        })
        one["bk_imb_2_chg4"] = one["bk_imb_2"] - one["bk_imb_2"].shift(4)
        one["bk_imb_2_24"] = one["bk_imb_2"].rolling(24, min_periods=12).mean()
        rows.append(one)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def coinalyze_features() -> pd.DataFrame:
    from src.data.storage import get_storage
    st = get_storage()
    with st._connect() as c:  # noqa: SLF001
        cur = c.raw.cursor()
        cur.execute("SELECT base, exchange, ts, oi_usd, liq_long, liq_short, funding, buy_volume, volume "
                    "FROM coinalyze_1h")
        d = pd.DataFrame.from_records(cur.fetchall(), columns=[x[0] for x in cur.description])
    if d.empty:
        return d
    d["base"] = d["base"].str.upper()
    d["avail"] = d["ts"].astype("int64") + H_       # hourly bar closes one hour after its open stamp
    num = ["oi_usd", "liq_long", "liq_short", "funding", "buy_volume", "volume"]
    d[num] = d[num].astype(float)
    tot = d.groupby(["base", "avail"])[["oi_usd", "liq_long", "liq_short", "buy_volume", "volume"]].sum(min_count=1)
    bin_ = d[d["exchange"] == "binance"].groupby(["base", "avail"])["oi_usd"].sum(min_count=1).rename("oi_bin")
    nex = d[d["oi_usd"] > 0].groupby(["base", "avail"])["exchange"].nunique().rename("n_ex")
    t = tot.join(bin_).join(nex).reset_index().sort_values(["base", "avail"])
    g = t.groupby("base", sort=False)
    out = pd.DataFrame({"base": t["base"], "ts": t["avail"]})
    out["cz_oi_24h"] = np.log(t["oi_usd"] / g["oi_usd"].shift(24))
    out["cz_oi_4h"] = np.log(t["oi_usd"] / g["oi_usd"].shift(4))
    out["cz_nonbin_oi_24h"] = np.log((t["oi_usd"] - t["oi_bin"]).clip(lower=1) /
                                     (g["oi_usd"].shift(24) - g["oi_bin"].shift(24)).clip(lower=1))
    out["cz_bin_share"] = t["oi_bin"] / t["oi_usd"]
    out["cz_bin_share_chg24"] = out["cz_bin_share"] - out.groupby(t["base"])["cz_bin_share"].shift(24)
    out["cz_n_ex"] = t["n_ex"]
    liq24 = (t["liq_long"].fillna(0) + t["liq_short"].fillna(0)).groupby(t["base"]).transform(
        lambda s: s.rolling(24, min_periods=12).sum())
    ll24 = t["liq_long"].fillna(0).groupby(t["base"]).transform(lambda s: s.rolling(24, min_periods=12).sum())
    if t["liq_long"].notna().any():
        out["cz_liq_oi_24h"] = liq24 / t["oi_usd"]
        out["cz_liq_imb_24h"] = (2 * ll24 - liq24) / liq24.replace(0, np.nan)
    if t["volume"].notna().any():
        bv = t["buy_volume"].groupby(t["base"]).transform(lambda s: s.rolling(24, min_periods=12).sum())
        vv = t["volume"].groupby(t["base"]).transform(lambda s: s.rolling(24, min_periods=12).sum())
        out["cz_buy_share_24h"] = bv / vv
    return out


def tardis_features() -> pd.DataFrame:
    """Binance premium and liquidations from the Tardis trial days (exploratory, ~10 days)."""
    parts = []
    dd = TARD / "deriv" / "binance-futures"
    ld = TARD / "liq" / "binance-futures"
    if not dd.exists():
        return pd.DataFrame()
    for f in sorted(dd.glob("*.parquet")):
        d = pd.read_parquet(f, columns=["symbol", "minute", "mark_price", "index_price", "last_price"])
        d["hour"] = (d["minute"] // H_ + 1) * H_          # minute bars inside [h-1, h) are known at h
        d["prem"] = d["last_price"] / d["index_price"] - 1
        a = d.sort_values("minute").groupby(["symbol", "hour"]).agg(td_prem=("prem", "last"),
                                                                    td_prem_1h=("prem", "mean")).reset_index()
        lf = ld / f.name
        if lf.exists():
            q = pd.read_parquet(lf)
            q["hour"] = (q["timestamp"] // 1_000_000 // H_ + 1) * H_
            # Binance liquidation order side 'sell' = a long being liquidated
            q["long_liq"] = np.where(q["side"] == "sell", q["usd"], 0.0)
            q["short_liq"] = np.where(q["side"] == "buy", q["usd"], 0.0)
            lq = q.groupby(["symbol", "hour"])[["long_liq", "short_liq"]].sum().reset_index()
            a = a.merge(lq, on=["symbol", "hour"], how="left")
        parts.append(a)
    if not parts:
        return pd.DataFrame()
    t = pd.concat(parts, ignore_index=True).rename(columns={"symbol": "code", "hour": "ts"})
    for k in ("long_liq", "short_liq"):
        if k not in t:
            t[k] = np.nan
    t[["long_liq", "short_liq"]] = t[["long_liq", "short_liq"]].fillna(0.0)
    t = t.sort_values(["code", "ts"])
    return t


def build() -> pd.DataFrame:
    t0 = time.time()
    p = pd.read_parquet(PANEL)
    for c in p.columns:
        if p[c].dtype == np.float64:
            p[c] = p[c].astype(np.float32)
    p = p.sort_values(["symbol", "ts"]).reset_index(drop=True)
    p["code"] = p["symbol"].str.replace("/", "", regex=False)
    sym = p["symbol"]
    g = p.groupby("ts", sort=False)
    log.info("panel %s in %.0fs", p.shape, time.time() - t0)

    # ---- targets
    for H in (1, 4, 24):
        p[f"ex_ret_{H}h"] = p[f"ret_{H}h"] - g[f"ret_{H}h"].transform("median")
        p[f"d1_ex_ret_{H}h"] = p.groupby("symbol", sort=False)[f"ex_ret_{H}h"].shift(-1)
    p["abs_ex_ret_24h"] = p["ex_ret_24h"].abs()
    btc = p.loc[p["symbol"] == "BTC/USDT"].set_index("ts")["ret_24h"]
    p["btc_fwd_24h"] = p["ts"].map(btc).astype(np.float32)

    # ---- price / momentum
    for k in ("ret_60m", "ret_240m", "ret_1440m", "ret_4320m", "ret_10080m"):
        p["ex_" + k[4:]] = p[k] - g[k].transform("median")
    for k in ("ret_60m", "ret_240m", "ret_1440m"):
        p["exb_" + k[4:]] = p[k] - p[f"btc_{k}"]
    r7 = p["ret_10080m"].astype(np.float64)
    s7 = p.groupby("symbol", sort=False)["ret_10080m"]
    r21 = (1 + r7) * (1 + s7.shift(168)) * (1 + s7.shift(336)) - 1
    p["mom_21d"] = r21.astype(np.float32)
    p["mom_21d_skip1"] = ((1 + r21) / (1 + p["ret_1440m"]) - 1).astype(np.float32)
    p["mom_7d_skip1"] = ((1 + r7) / (1 + p["ret_1440m"]) - 1).astype(np.float32)
    g = p.groupby("ts", sort=False)
    ct = ["ret_1440m", "ret_4320m", "ret_10080m", "mom_21d", "dhi_7d", "dlo_7d", "dhi_30d", "dlo_30d"]
    z = [(p[c] - g[c].transform("mean")) / g[c].transform("std") for c in ct]
    p["ctrend"] = pd.concat(z, axis=1).clip(-3, 3).mean(axis=1).astype(np.float32)

    # ---- volatility / lottery
    r1 = p["ret_60m"].astype(np.float64)
    p["max_7d"] = _grp_roll(r1, sym, 168, "max", 72).astype(np.float32)
    p["min_7d"] = _grp_roll(r1, sym, 168, "min", 72).astype(np.float32)
    dn = _grp_roll(r1.clip(upper=0) ** 2, sym, 24, "mean", 12) ** 0.5
    up = _grp_roll(r1.clip(lower=0) ** 2, sym, 24, "mean", 12) ** 0.5
    p["semi_dn_24h"], p["semi_up_24h"] = dn.astype(np.float32), up.astype(np.float32)
    p["semi_asym_24h"] = ((up - dn) / (up + dn)).astype(np.float32)
    dn7 = _grp_roll(r1.clip(upper=0) ** 2, sym, 168, "mean", 72) ** 0.5
    up7 = _grp_roll(r1.clip(lower=0) ** 2, sym, 168, "mean", 72) ** 0.5
    p["semi_asym_7d"] = ((up7 - dn7) / (up7 + dn7)).astype(np.float32)

    # ---- liquidity
    dv24 = np.expm1(p["dv24_log"].astype(np.float64))
    dv1h = (dv24 * p["vsurge_60m"] / 24 / p["vsurge_1440m"].replace(0, np.nan)).astype(np.float64)
    p["amihud_24h"] = (np.log(p["ret_1440m"].abs() + 1e-4) - p["dv24_log"]).astype(np.float32)
    ami = (r1.abs() / dv1h.replace(0, np.nan)) * 1e6
    p["amihud_7d"] = np.log(_grp_roll(ami, sym, 168, "mean", 72) + 1e-9).astype(np.float32)
    ldv = np.log(dv1h.clip(lower=1))
    key = [sym, p["hour"]]
    gh = ldv.groupby(key, sort=False)
    mu = gh.transform(lambda s: s.shift(1).rolling(30, min_periods=10).mean())
    sd = gh.transform(lambda s: s.shift(1).rolling(30, min_periods=10).std())
    p["volz_30d_hour"] = ((ldv - mu) / sd).astype(np.float32)

    # ---- taker flow standardised over 30 days
    for k in ("taker_60m", "taker_240m", "taker_1440m"):
        x = p[k].astype(np.float64)
        m = _grp_roll(x, sym, 720, "mean", 168)
        s = _grp_roll(x, sym, 720, "std", 168)
        p["tkz_" + k[6:]] = ((x - m) / s).astype(np.float32)

    # ---- derivatives
    p["oiq_24h"] = (np.sign(p["oi_24h"]) * np.sign(p["ret_1440m"])).astype(np.float32)
    p["oi_x_ret_24h"] = (p["oi_24h"] * p["ret_1440m"]).astype(np.float32)
    fh = funding_hours()
    p["fund_h"] = p["code"].map(fh).fillna(8.0).astype(np.float32)
    p["fund_8h"] = (p["funding"] * 8 / p["fund_h"]).astype(np.float32)
    p["fund_8h_7d"] = _grp_roll(p["fund_8h"].astype(np.float64), sym, 168, "mean", 48).astype(np.float32)
    p["fund_extreme_pos"] = (p["fund_8h"] > 0.0005).astype(np.float32)
    p["fund_extreme_neg"] = (p["fund_8h"] < -0.0005).astype(np.float32)
    log.info("panel features done %.0fs", time.time() - t0)

    # ---- bookDepth
    hours = {c: grp["ts"].to_numpy() for c, grp in p[["code", "ts"]].groupby("code", sort=False)}
    bk = book_features(sorted(hours), hours)
    if len(bk):
        p = p.merge(bk, on=["code", "ts"], how="left")
        p["bk_dep2_rel"] = (p["bk_dep2"] - p["dv24_log"]).astype(np.float32)
        p["bk_dep02_rel"] = (p["bk_dep02"] - p["dv24_log"]).astype(np.float32)
    log.info("bookdepth rows %d (%d coins) %.0fs", len(bk), bk["code"].nunique() if len(bk) else 0,
             time.time() - t0)

    # ---- Coinalyze
    try:
        cz = coinalyze_features()
    except Exception as e:  # noqa: BLE001
        log.warning("coinalyze: %r", e)
        cz = pd.DataFrame()
    if len(cz):
        base = p["symbol"].str.split("/").str[0].str.upper()
        p["base"] = base
        p = p.merge(cz, on=["base", "ts"], how="left")
        miss = p["cz_oi_24h"].isna() & p["base"].str.match(r"^1000+")
        if miss.any():
            alt = p.loc[miss, ["base", "ts"]].copy()
            alt["base"] = alt["base"].str.replace(r"^1000+", "", regex=True)
            fill = alt.merge(cz, on=["base", "ts"], how="left")
            for c in cz.columns:
                if c.startswith("cz_"):
                    p.loc[miss, c] = fill[c].to_numpy()
        p = p.drop(columns=["base"])
    log.info("coinalyze rows %d %.0fs", len(cz), time.time() - t0)

    # ---- Tardis trial days
    td = tardis_features()
    if len(td):
        p = p.merge(td, on=["code", "ts"], how="left")
        dv1h_ = (np.expm1(p["dv24_log"].astype(np.float64)) / 24).replace(0, np.nan)
        p["td_liq_long_1h"] = (p["long_liq"] / dv1h_).astype(np.float32)
        p["td_liq_short_1h"] = (p["short_liq"] / dv1h_).astype(np.float32)
        p["td_liq_imb_1h"] = ((p["long_liq"] - p["short_liq"]) /
                              (p["long_liq"] + p["short_liq"]).replace(0, np.nan)).astype(np.float32)
        p = p.drop(columns=["long_liq", "short_liq"])
    log.info("tardis rows %d %.0fs", len(td), time.time() - t0)

    p = p.drop_duplicates(["symbol", "ts"]).reset_index(drop=True)

    # ---- universe and liquidity tercile
    p["univ"] = (p["dv24_log"] >= np.log1p(1e6)) & (p["age_days"] >= 14) & p["ex_ret_1h"].notna()
    rk = p.loc[p["univ"]].groupby("ts")["dv24_log"].rank(pct=True)
    p["terc"] = -1
    p.loc[rk.index, "terc"] = np.minimum((rk.to_numpy() * 3).astype(int), 2)
    for c in p.columns:
        if p[c].dtype == np.float64:
            p[c] = p[c].astype(np.float32)
    return p


# ------------------------------------------------------------------ statistics

def nw_t(x: np.ndarray, lags: int) -> tuple[float, float]:
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 30:
        return np.nan, np.nan
    m = x.mean()
    e = x - m
    v = e @ e / n
    for L in range(1, min(lags, n - 1) + 1):
        w = 1 - L / (lags + 1)
        v += 2 * w * (e[L:] @ e[:-L]) / n
    se = np.sqrt(max(v, 1e-18) / n)
    return float(m), float(m / se)


def ts_corr(gcode: np.ndarray, G: int, x: np.ndarray, y: np.ndarray, minn: int = 20) -> np.ndarray:
    m = np.isfinite(x) & np.isfinite(y)
    g, x, y = gcode[m], x[m].astype(np.float64), y[m].astype(np.float64)
    n = np.bincount(g, minlength=G).astype(np.float64)
    sx, sy = np.bincount(g, x, G), np.bincount(g, y, G)
    sxx, syy, sxy = np.bincount(g, x * x, G), np.bincount(g, y * y, G), np.bincount(g, x * y, G)
    with np.errstate(invalid="ignore", divide="ignore"):
        cov = sxy / n - sx * sy / n ** 2
        vx = sxx / n - (sx / n) ** 2
        vy = syy / n - (sy / n) ** 2
        r = cov / np.sqrt(vx * vy)
    r[(n < minn) | ~np.isfinite(r)] = np.nan
    return r


def bh(p: np.ndarray) -> np.ndarray:
    p = np.asarray(p, float)
    ok = np.isfinite(p)
    q = np.full_like(p, np.nan)
    ps = p[ok]
    o = np.argsort(ps)
    r = ps[o] * len(ps) / (np.arange(len(ps)) + 1)
    r = np.minimum.accumulate(r[::-1])[::-1]
    tmp = np.empty_like(ps)
    tmp[o] = np.minimum(r, 1)
    q[ok] = tmp
    return q


def pval(t: float) -> float:
    from math import erfc, sqrt
    return erfc(abs(t) / sqrt(2)) if np.isfinite(t) else np.nan


# ------------------------------------------------------------------ evaluation

def rank_frame(u: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    g = u.groupby("ts", sort=False)
    return pd.DataFrame({c: g[c].rank(pct=True).astype(np.float32) for c in cols}, index=u.index)


def orthogonalise(R: pd.DataFrame, ts: np.ndarray, feats: list[str]) -> pd.DataFrame:
    ctrl = [c for c in CONTROLS if c in R]
    out = np.full((len(R), len(feats)), np.nan, np.float32)
    Y_all = R[feats].to_numpy(np.float32)
    X_all = R[ctrl].to_numpy(np.float32)
    order = np.argsort(ts, kind="stable")
    tss = ts[order]
    cuts = np.flatnonzero(np.diff(tss)) + 1
    for idx in np.split(order, cuts):
        if len(idx) < 30:
            continue
        X = np.nan_to_num(X_all[idx].astype(np.float64), nan=0.5)
        X = np.column_stack([np.ones(len(idx)), X])
        Y = Y_all[idx].astype(np.float64)
        nanm = ~np.isfinite(Y)
        Yf = np.where(nanm, 0.5, Y)
        beta, *_ = np.linalg.lstsq(X, Yf, rcond=None)
        res = Yf - X @ beta
        res[nanm] = np.nan
        out[idx] = res
    return pd.DataFrame(out, index=R.index, columns=[f"{f}" for f in feats])


def ic_table(u: pd.DataFrame, R: pd.DataFrame, O: pd.DataFrame, feats: list[str], targets: dict[str, int],
             hold_start: int) -> pd.DataFrame:
    ts = u["ts"].to_numpy()
    uts, tcode = np.unique(ts, return_inverse=True)
    G = len(uts)
    terc = u["terc"].to_numpy()
    g3 = tcode * 3 + np.clip(terc, 0, 2)
    dev = uts < hold_start
    folds = np.array_split(np.flatnonzero(dev), 6)
    btc30 = u.groupby("ts")["btc_ret_1440m"].first().reindex(uts).to_numpy()
    # 30 day BTC trend = sum of last 30 daily returns, approx via rolling on hourly 24h returns / 24
    btc30 = pd.Series(btc30).rolling(720, min_periods=240).sum().to_numpy() / 24
    bull = btc30 > 0
    rows = []
    for tgt, H in targets.items():
        y = R[tgt].to_numpy()
        lags = max(2 * H, 6)
        for src, M in (("raw", R), ("orth", O)):
            for f in feats:
                if f not in M:
                    continue
                x = M[f].to_numpy()
                ic = ts_corr(tcode, G, x, y)
                n_ok = np.isfinite(ic[dev]).sum()
                if n_ok < 100:
                    continue
                m_dev, t_dev = nw_t(ic[dev], lags)
                m_ho, t_ho = nw_t(ic[~dev], lags)
                fs = [np.nanmean(ic[i]) for i in folds]
                same = int(np.sum(np.sign(fs) == np.sign(m_dev)))
                row = dict(feature=f, family=family(f), target=tgt, src=src, ic=m_dev, t=t_dev, n_hours=int(n_ok),
                           folds_same=same, ic_hold=m_ho, t_hold=t_ho,
                           ic_bull=np.nanmean(ic[dev & bull]), ic_bear=np.nanmean(ic[dev & ~bull]))
                if src == "raw":
                    ic3 = ts_corr(g3, G * 3, x, y).reshape(G, 3)
                    for k, name in enumerate(("lo", "mid", "hi")):
                        row[f"ic_{name}"], row[f"t_{name}"] = nw_t(ic3[dev, k], lags)
                rows.append(row)
        log.info("IC %s done (%d rows)", tgt, len(rows))
    df = pd.DataFrame(rows)
    df["p"] = df["t"].map(pval)
    df["q"] = bh(df["p"].to_numpy())
    return df


def portfolio(u: pd.DataFrame, score: np.ndarray, H: int, delay: bool, hold_start: int) -> dict:
    """Quintile long-short on excess return and long-only top quintile vs BTC, net of costs and funding."""
    d = pd.DataFrame({"ts": u["ts"].to_numpy(), "s": score, "terc": u["terc"].to_numpy(),
                      "y": u[f"{'d1_' if delay else ''}ex_ret_{H}h"].to_numpy(),
                      "r": u[f"ret_{H}h"].to_numpy(), "f": u["fund_8h"].fillna(0).to_numpy(),
                      "btc": u["btc_fwd_24h"].to_numpy() if H == 24 else np.nan})
    if delay:
        d["r"] = u.groupby("symbol", sort=False)[f"ret_{H}h"].shift(-1).to_numpy()
    d = d[np.isfinite(d["s"]) & np.isfinite(d["y"])]
    d = d[(d["ts"] // H_) % H == 0]            # non-overlapping rebalances
    q = d.groupby("ts")["s"].rank(pct=True)
    d["q"] = np.minimum((q * 5).astype(int), 4)
    d["cost"] = 2 * (FEE + np.take(np.array(SLIP), d["terc"].clip(0, 2).to_numpy()))
    d["fund"] = d["f"] * H / 8
    top, bot = d[d["q"] == 4], d[d["q"] == 0]
    a = top.groupby("ts").agg(y=("y", "mean"), r=("r", "mean"), c=("cost", "mean"), f=("fund", "mean"),
                              btc=("btc", "first"))
    b = bot.groupby("ts").agg(y=("y", "mean"), c=("cost", "mean"), f=("fund", "mean"))
    j = a.join(b, rsuffix="_s", how="inner")
    j["ls_gross"] = j["y"] - j["y_s"]
    j["ls_net"] = j["ls_gross"] - j["c"] - j["c_s"] - j["f"] + j["f_s"]
    j["lo_net_btc"] = j["r"] - j["c"] - j["f"] - (j["btc"] if H == 24 else 0)
    out = {}
    for name, sel in (("dev", j.index < hold_start), ("hold", j.index >= hold_start)):
        s = j[sel]
        for k in ("ls_gross", "ls_net", "lo_net_btc"):
            m, t = nw_t(s[k].to_numpy(), 1)
            out[f"{name}_{k}"], out[f"{name}_{k}_t"] = m, t
        out[f"{name}_n"] = int(len(s))
    return out


def walk_forward(u: pd.DataFrame, R: pd.DataFrame, feats: list[str], target: str, kind: str,
                 hold_start: int) -> tuple[np.ndarray, list[dict]]:
    """8w train / 2w val / 2w test rolling, 24h purge on both sides, alpha/regularisation picked on val."""
    from sklearn.linear_model import LogisticRegression, Ridge
    import lightgbm as lgb
    ts = u["ts"].to_numpy()
    X = np.nan_to_num(R[feats].to_numpy(np.float32), nan=0.5)
    y = (R[target] if kind == "reg" else u[target]).to_numpy(np.float64)
    pred = {"lin": np.full(len(u), np.nan), "gbm": np.full(len(u), np.nan)}
    W, V, T, P = 56 * 86400, 14 * 86400, 14 * 86400, 86400
    start = int(ts.min())
    info = []
    t_test = start + W + V + 2 * P
    while t_test < ts.max():
        tr = (ts >= t_test - V - P - W - P) & (ts < t_test - V - 2 * P) & np.isfinite(y) & ((ts // H_) % 4 == 0)
        va = (ts >= t_test - V - P) & (ts < t_test - P) & np.isfinite(y)
        te = (ts >= t_test) & (ts < t_test + T)
        if tr.sum() < 20000 or va.sum() < 5000 or te.sum() == 0:
            t_test += T
            continue
        best = None
        for a in ((10.0, 1e3, 1e5) if kind == "reg" else (0.001, 0.01, 0.1)):
            m = Ridge(alpha=a) if kind == "reg" else LogisticRegression(C=a, max_iter=300)
            m.fit(X[tr], y[tr])
            pv = m.predict(X[va]) if kind == "reg" else m.predict_proba(X[va])[:, 1]
            sc = _score(u.loc[va, "ts"].to_numpy(), pv, y[va], kind)
            if best is None or sc > best[0]:
                best = (sc, a, m)
        pred["lin"][te] = best[2].predict(X[te]) if kind == "reg" else best[2].predict_proba(X[te])[:, 1]
        params = dict(objective="regression" if kind == "reg" else "binary", learning_rate=0.05, num_leaves=31,
                      min_data_in_leaf=500, feature_fraction=0.7, bagging_fraction=0.7, bagging_freq=1,
                      lambda_l2=10.0, verbose=-1, num_threads=6)
        dtr = lgb.Dataset(X[tr], y[tr])
        dva = lgb.Dataset(X[va], y[va], reference=dtr)
        gb = lgb.train(params, dtr, 400, valid_sets=[dva], callbacks=[lgb.early_stopping(30, verbose=False)])
        pred["gbm"][te] = gb.predict(X[te], num_iteration=gb.best_iteration)
        info.append(dict(test_start=int(t_test), alpha=best[1], val=best[0], trees=gb.best_iteration,
                         hold=bool(t_test >= hold_start)))
        t_test += T
    return pred, info


def _score(ts: np.ndarray, p: np.ndarray, y: np.ndarray, kind: str) -> float:
    uts, code = np.unique(ts, return_inverse=True)
    if kind == "reg":
        r = pd.Series(p).groupby(code).rank(pct=True).to_numpy()
        yr = pd.Series(y).groupby(code).rank(pct=True).to_numpy()
        return float(np.nanmean(ts_corr(code, len(uts), r, yr)))
    return prec_at_k(ts, p, y, 10)[0]


def prec_at_k(ts: np.ndarray, p: np.ndarray, y: np.ndarray, k: int) -> tuple[float, float]:
    d = pd.DataFrame({"ts": ts, "p": p, "y": y}).dropna()
    d["r"] = d.groupby("ts")["p"].rank(ascending=False, method="first")
    top = d[d["r"] <= k]
    return float(top["y"].mean()), float(d["y"].mean())


def event_study(p: pd.DataFrame) -> list[dict]:
    rows = []
    for ev in ("ev_listing72", "ev_warning72", "ev_delisting72"):
        if ev not in p:
            continue
        on = (p[ev] > 0) & (p.groupby("symbol", sort=False)[ev].shift(1).fillna(0) == 0)
        e = p.loc[on]
        if len(e) < 5:
            continue
        r = dict(event=ev, n=int(len(e)))
        for k in ("ex_1440m", "ex_ret_1h", "ex_ret_4h", "ex_ret_24h", "d1_ex_ret_24h"):
            x = e[k].dropna()
            r[k] = float(x.mean()) if len(x) else np.nan
            r[k + "_t"] = float(x.mean() / (x.std() / np.sqrt(len(x)))) if len(x) > 2 else np.nan
        rows.append(r)
    return rows


# ------------------------------------------------------------------ report

def fmt(x, d=3):
    return "" if x is None or not np.isfinite(x) else f"{x:.{d}f}"


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    if a.reuse and LITP.exists():
        p = pd.read_parquet(LITP)
    else:
        p = build()
        p.to_parquet(LITP, index=False)
    log.info("lit panel %s %.0fs", p.shape, time.time() - t0)

    u = p.loc[p["univ"]].reset_index(drop=True)
    del p
    hold_start = int(u["ts"].max()) - HOLDOUT_DAYS * 86400
    labels = [c for c in u.columns if c.startswith(LABEL_PREFIX)]
    feats = [c for c in u.columns if c not in DROP and c not in labels and u[c].dtype.kind in "fi"
             and u[c].notna().mean() > 0.02]
    feats = [c for c in feats if c not in ("btc_ret_60m", "btc_ret_240m", "btc_ret_1440m", "mkt_ret_60m",
                                           "mkt_ret_240m", "mkt_ret_1440m", "breadth_24h", "fng")]  # constant per hour
    targets = {"ex_ret_1h": 1, "ex_ret_4h": 4, "ex_ret_24h": 24, "d1_ex_ret_24h": 24, "abs_ex_ret_24h": 24,
               "up10_24h": 24, "dn10_24h": 24, "win10_24h": 24, "up10_4h": 4, "up10_1h": 1}
    log.info("universe rows %d, features %d, holdout from %s", len(u), len(feats),
             pd.to_datetime(hold_start, unit="s"))
    R = rank_frame(u, feats + list(targets))
    O = orthogonalise(R, u["ts"].to_numpy(), feats)
    log.info("ranks + orthogonalisation %.0fs", time.time() - t0)
    ic = ic_table(u, R, O, feats, targets, hold_start)
    ic.to_csv(OUT / "ic_table.csv", index=False)
    log.info("IC table %d rows %.0fs", len(ic), time.time() - t0)

    # promoted: |t|>3, q<0.05, all 6 folds same sign, same sign in holdout
    def promoted(df):
        return df[(df["t"].abs() > 3) & (df["q"] < 0.05) & (df["folds_same"] == 6)
                  & (np.sign(df["ic_hold"]) == np.sign(df["ic"]))]

    # portfolios for top directional features (raw ranks) on 24h and 4h
    port_rows = []
    for tgt, H in (("ex_ret_24h", 24), ("ex_ret_4h", 4)):
        d = ic[(ic["target"] == tgt) & (ic["src"] == "raw")].copy()
        d = d.reindex(d["t"].abs().sort_values(ascending=False).index).head(12)
        for _, r in d.iterrows():
            s = np.sign(r["ic"]) * R[r["feature"]].to_numpy()
            for delay in (False, True):
                res = portfolio(u, s, H, delay, hold_start)
                port_rows.append(dict(feature=r["feature"], H=H, sign=int(np.sign(r["ic"])), delay=delay, **res))
        log.info("portfolios %s %.0fs", tgt, time.time() - t0)

    # walk-forward models
    wf = {}
    preds = {}
    for target, kind in (("ex_ret_24h", "reg"), ("up10_24h", "clf"), ("dn10_24h", "clf"), ("win10_24h", "clf")):
        pr, info = walk_forward(u, R, feats, target, kind, hold_start)
        preds[target] = pr
        res = dict(folds=info)
        ts = u["ts"].to_numpy()
        for m, pv in pr.items():
            ok = np.isfinite(pv)
            for name, sel in (("dev", ok & (ts < hold_start)), ("hold", ok & (ts >= hold_start))):
                if sel.sum() == 0:
                    continue
                if kind == "reg":
                    uts, code = np.unique(ts[sel], return_inverse=True)
                    pr_r = pd.Series(pv[sel]).groupby(code).rank(pct=True).to_numpy()
                    icv = ts_corr(code, len(uts), pr_r, R[target].to_numpy()[sel])
                    res[f"{m}_{name}_ic"], res[f"{m}_{name}_t"] = nw_t(icv, 48)
                else:
                    pk, base = prec_at_k(ts[sel], pv[sel], u[target].to_numpy()[sel], 10)
                    res[f"{m}_{name}_p10"], res[f"{m}_{name}_base"] = pk, base
            if kind == "reg":
                sc = np.where(ok, pv, np.nan)
                rk = pd.Series(sc).groupby(u["ts"].to_numpy()).rank(pct=True).to_numpy()
                for delay in (False, True):
                    res[f"{m}_port{'_d1' if delay else ''}"] = portfolio(u, rk, 24, delay, hold_start)
        wf[target] = res
        log.info("walk-forward %s %.0fs", target, time.time() - t0)

    # direction inside predicted big movers: of the top-10 by up10 prob, how many also dn10?
    ts = u["ts"].to_numpy()
    both = {}
    for m in ("lin", "gbm"):
        pu, pdn = preds["up10_24h"][m], preds["dn10_24h"][m]
        d = pd.DataFrame({"ts": ts, "pu": pu, "pd": pdn, "up": u["up10_24h"], "dn": u["dn10_24h"],
                          "win": u["win10_24h"], "ex": u["ex_ret_24h"]}).dropna()
        d["mv"] = d["pu"] + d["pd"]
        d["r"] = d.groupby("ts")["mv"].rank(ascending=False, method="first")
        top = d[d["r"] <= 10].copy()
        top["lean_up"] = top["pu"] > top["pd"] * (d["up"].mean() / max(d["dn"].mean(), 1e-9))
        both[m] = dict(n=int(len(top)), p_up=float(top["up"].mean()), p_dn=float(top["dn"].mean()),
                       p_any=float(((top["up"] + top["dn"]) > 0).mean()), base_any=float(((d["up"] + d["dn"]) > 0).mean()),
                       lean_up_hit=float(top.loc[top["lean_up"], "win"].mean()) if top["lean_up"].any() else np.nan,
                       lean_up_ex=float(top.loc[top["lean_up"], "ex"].mean()) if top["lean_up"].any() else np.nan,
                       lean_dn_ex=float(top.loc[~top["lean_up"], "ex"].mean()) if (~top["lean_up"]).any() else np.nan,
                       lean_up_share=float(top["lean_up"].mean()))

    ev = event_study(u)
    # seasonality: median alt forward 1h return by UTC hour (market level)
    mk = u.groupby("ts")["ret_1h"].median()
    hr = (mk.index // H_) % 24
    seas = []
    for h in range(24):
        m, t = nw_t(mk[hr == h].to_numpy(), 1)
        seas.append(dict(hour_utc=h, mean=m, t=t))

    res = dict(generated=int(time.time()), rows=int(len(u)), coins=int(u["symbol"].nunique()),
               start=int(u["ts"].min()), end=int(u["ts"].max()), hold_start=hold_start, n_features=len(feats),
               promoted=promoted(ic).to_dict("records"), portfolios=port_rows, walk_forward=wf, big_mover_dir=both,
               events=ev, seasonality=seas,
               coverage={c: float(u[c].notna().mean()) for c in feats if c.startswith(("bk_", "cz_", "td_", "kr_",
                                                                                     "kimchi"))})
    (OUT / "latest.json").write_text(json.dumps(res, default=float, indent=1))
    write_md(res, ic)
    log.info("done %.0fs", time.time() - t0)
    return 0


def write_md(res: dict, ic: pd.DataFrame) -> None:
    d0 = pd.to_datetime(res["start"], unit="s").date()
    d1 = pd.to_datetime(res["end"], unit="s").date()
    dh = pd.to_datetime(res["hold_start"], unit="s").date()
    L = [f"# 논문 기반 재검증 ({d0} ~ {d1}, 홀드아웃 {dh} 이후)", "",
         f"코인 {res['coins']}개, {res['rows']:,}개 코인·시간, 변수 {res['n_features']}개. "
         "유니버스: 24h 거래대금 100만 달러 이상, 상장 14일 이상. 타깃은 같은 시각 전체 코인 중앙값 대비 초과수익.", ""]
    L += ["## 통과 기준 (|t|>3, FDR q<0.05, 6개 구간 모두 같은 부호, 홀드아웃에서도 같은 부호)", ""]
    pr = pd.DataFrame(res["promoted"])
    if len(pr):
        pr = pr.sort_values("t", key=abs, ascending=False)
        for tgt, grp in pr.groupby("target"):
            L.append(f"### {tgt} ({len(grp)}개)")
            L.append("| 변수 | 종류 | raw/orth | IC | t | 홀드아웃 IC | t | 저유동 IC | 고유동 IC |")
            L.append("|---|---|---|---|---|---|---|---|---|")
            for _, r in grp.head(15).iterrows():
                L.append(f"| {r['feature']} | {r['family']} | {r['src']} | {fmt(r['ic'],4)} | {fmt(r['t'],1)} | "
                         f"{fmt(r['ic_hold'],4)} | {fmt(r['t_hold'],1)} | {fmt(r.get('ic_lo'),4)} | {fmt(r.get('ic_hi'),4)} |")
            L.append("")
    else:
        L += ["없음.", ""]
    L += ["## 가족별 최강 변수 (ex_ret_24h, 잔차화 후)", "", "| 가족 | 변수 | IC | t | 홀드아웃 IC | 6구간 일치 |",
          "|---|---|---|---|---|---|"]
    d = ic[(ic["target"] == "ex_ret_24h") & (ic["src"] == "orth")]
    for fam, grp in d.groupby("family"):
        r = grp.loc[grp["t"].abs().idxmax()]
        L.append(f"| {fam} | {r['feature']} | {fmt(r['ic'],4)} | {fmt(r['t'],1)} | {fmt(r['ic_hold'],4)} | {r['folds_same']}/6 |")
    L += ["", "## 포트폴리오 (5분위 롱숏 / 상위 5분위 롱 vs BTC, 수수료+슬리피지+펀딩 차감, 거래당 평균)", "",
          "| 변수 | H | 지연 | dev 총 | dev 순 | t | 홀드 순 | t | 롱only-BTC 홀드 |", "|---|---|---|---|---|---|---|---|---|"]
    for r in res["portfolios"]:
        L.append(f"| {r['feature']} | {r['H']}h | {'1h' if r['delay'] else '0'} | {fmt(r['dev_ls_gross']*100,2)}% | "
                 f"{fmt(r['dev_ls_net']*100,2)}% | {fmt(r['dev_ls_net_t'],1)} | {fmt(r['hold_ls_net']*100,2)}% | "
                 f"{fmt(r['hold_ls_net_t'],1)} | {fmt(r['hold_lo_net_btc']*100,2)}% |")
    L += ["", "## 워크포워드 모델 (8주 학습 / 2주 검증 / 2주 테스트, 24h 퍼지)", ""]
    for tgt, r in res["walk_forward"].items():
        if tgt == "ex_ret_24h":
            L.append(f"- {tgt}: Ridge IC dev {fmt(r.get('lin_dev_ic'),4)} (t {fmt(r.get('lin_dev_t'),1)}), "
                     f"홀드 {fmt(r.get('lin_hold_ic'),4)} (t {fmt(r.get('lin_hold_t'),1)}); "
                     f"GBM dev {fmt(r.get('gbm_dev_ic'),4)} (t {fmt(r.get('gbm_dev_t'),1)}), "
                     f"홀드 {fmt(r.get('gbm_hold_ic'),4)} (t {fmt(r.get('gbm_hold_t'),1)})")
            for m in ("lin", "gbm"):
                for k in ("port", "port_d1"):
                    q = r.get(f"{m}_{k}", {})
                    L.append(f"  - {m} {k}: 롱숏 순 dev {fmt(q.get('dev_ls_net',np.nan)*100,2)}% "
                             f"(t {fmt(q.get('dev_ls_net_t'),1)}), 홀드 {fmt(q.get('hold_ls_net',np.nan)*100,2)}% "
                             f"(t {fmt(q.get('hold_ls_net_t'),1)}), 롱only-BTC 홀드 {fmt(q.get('hold_lo_net_btc',np.nan)*100,2)}%")
        else:
            L.append(f"- {tgt}: 매시간 상위10 적중률 Logit dev {fmt(r.get('lin_dev_p10'))} / 홀드 {fmt(r.get('lin_hold_p10'))}, "
                     f"GBM dev {fmt(r.get('gbm_dev_p10'))} / 홀드 {fmt(r.get('gbm_hold_p10'))}, 기본확률 {fmt(r.get('gbm_hold_base'))}")
    L += ["", "## 크게 움직일 코인 상위 10개의 방향", ""]
    for m, r in res["big_mover_dir"].items():
        L.append(f"- {m}: ±10% 도달 {fmt(r['p_any'])} (기본 {fmt(r['base_any'])}), 위 {fmt(r['p_up'])}, 아래 {fmt(r['p_dn'])}; "
                 f"위로 기운 예측의 +10% 선도달 {fmt(r['lean_up_hit'])}, 초과수익 위 {fmt(r['lean_up_ex']*100,2)}% / "
                 f"아래 {fmt(r['lean_dn_ex']*100,2)}%")
    L += ["", "## 이벤트 (발표 직후 1시간 안에 진입)", "", "| 이벤트 | n | 직전 24h | +1h | +4h | +24h | 1h 늦게 +24h |",
          "|---|---|---|---|---|---|---|"]
    for r in res["events"]:
        L.append(f"| {r['event']} | {r['n']} | {fmt(r['ex_1440m']*100,1)}% | {fmt(r['ex_ret_1h']*100,1)}% | "
                 f"{fmt(r['ex_ret_4h']*100,1)}% | {fmt(r['ex_ret_24h']*100,1)}% (t {fmt(r['ex_ret_24h_t'],1)}) | "
                 f"{fmt(r['d1_ex_ret_24h']*100,1)}% |")
    L += ["", "## 데이터 커버리지 (유니버스 행 중 값 있는 비율)", ""]
    L.append(", ".join(f"{k} {v:.0%}" for k, v in sorted(res["coverage"].items())))
    (OUT / "latest.md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
