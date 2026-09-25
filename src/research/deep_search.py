"""Deep search for what precedes +10% moves, built on 1-minute data.

Panel: every coin (delisted included) x every hour of the last ~6 months.
Features only use minutes before the decision hour; labels use the minutes
after it (entry = open of the decision minute).

Labels, for H in 1h / 4h / 24h:
  up10_H   the high reaches +10% within H
  win10_H  +10% is reached before -5% (a trade with a 5% stop would win)
  dn10_H   the low reaches -10% within H
  ret_H    close-to-close return over H

Feature families (all causal):
  price     returns 1m..7d, distance from 1h/24h/7d/30d highs and lows, RSI,
            biggest 1m/5m candle in the last hour, burst counts
  vol       realised vol 1h/24h/7d from minutes, vol ratio, skew/kurtosis
  volume    quote volume vs its own 7-day baseline at 5m/15m/1h/4h/24h,
            trade count surge, average trade size change
  flow      taker-buy share 5m..24h and its z-score, CVD normalised
  deriv     open interest change 1h/4h/24h, OI/volume, OI/market cap,
            long/short ratios (global, top accounts, top positions), smart
            crowd, futures taker ratio, funding and its z-score
  size      market cap (CoinGecko point in time, else supply x price),
            24h dollar volume, days since the coin's first perp bar
  market    BTC 1h/4h/24h, cross-section breadth and median, Fear & Greed,
            hour of day, weekday, cross-sectional ranks of key features
  korea     (last ~60 days) on Upbit/Bithumb, kimchi premium, Korean
            share of volume, Korean 15m/1h volume surge, Korea-leads-Binance
  events    Upbit/Binance listing, warning, delisting notice in last 72h
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
K1M = ROOT / "data" / "cache" / "k1m"
KRH = ROOT / "data" / "cache" / "kr1m_hist"
KRA = ROOT / "data" / "cache" / "kr1m"
PANEL = ROOT / "data" / "cache" / "deep_panel.parquet"
HORIZONS = (1, 4, 24)
MIN = 60
H_ = 3600


# ------------------------------------------------------------------ per-symbol build

def _q(storage: Any, sql: str, params: tuple) -> pd.DataFrame:
    with storage._connect() as c:  # noqa: SLF001
        cur = c.raw.cursor() if hasattr(c, "raw") else c.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        cols = [d[0] for d in cur.description]
        return pd.DataFrame.from_records(cur.fetchall(), columns=cols)


def _minutes(code: str, since: int, until: int, raw: pd.DataFrame | None = None) -> pd.DataFrame | None:
    if raw is None:
        path = K1M / f"{code}.parquet"
        if not path.exists():
            return None
        raw = pd.read_parquet(path)
    d = raw
    d = d[(d["ts"] >= since - 31 * 86400) & (d["ts"] < until + 86400 + MIN)]
    if len(d) < 3 * 1440:
        return None
    idx = pd.RangeIndex(int(d["ts"].iloc[0]), int(d["ts"].iloc[-1]) + MIN, MIN)
    d = d.set_index("ts").reindex(idx)
    # empty minutes: no trade -> carry the close, zero volume
    d["c"] = d["c"].ffill()
    for k in ("o", "h", "l"):
        d[k] = d[k].fillna(d["c"])
    for k in ("qv", "tbq"):
        d[k] = d[k].fillna(0.0)
    d["n"] = d["n"].fillna(0)
    return d


def _labels(d: pd.DataFrame, pos: np.ndarray) -> dict[str, np.ndarray]:
    o, h, l_, c = (d[k].to_numpy(np.float64) for k in ("o", "h", "l", "c"))
    out: dict[str, np.ndarray] = {}
    n = len(c)
    for H in HORIZONS:
        w = H * 60
        ok = pos + w <= n
        e = np.where(ok, o[np.minimum(pos, n - 1)], np.nan)
        hi_w = sliding_window_view(h, w)       # row i = minutes i .. i+w-1
        lo_w = sliding_window_view(l_, w)
        p = np.minimum(pos, len(hi_w) - 1)
        mh, ml = hi_w[p].max(axis=1), lo_w[p].min(axis=1)
        out[f"mfe_{H}h"] = np.where(ok, mh / e - 1, np.nan)
        out[f"mae_{H}h"] = np.where(ok, ml / e - 1, np.nan)
        out[f"ret_{H}h"] = np.where(ok, c[np.minimum(pos + w - 1, n - 1)] / e - 1, np.nan)
        up = hi_w[p] >= (e * 1.10)[:, None]
        st = lo_w[p] <= (e * 0.95)[:, None]
        fu = np.where(up.any(axis=1), up.argmax(axis=1), w + 1)
        fs = np.where(st.any(axis=1), st.argmax(axis=1), w + 1)
        out[f"up10_{H}h"] = np.where(ok, (fu <= w).astype(float), np.nan)
        out[f"win10_{H}h"] = np.where(ok, ((fu <= w) & (fu < fs)).astype(float), np.nan)
        out[f"dn10_{H}h"] = np.where(ok, (out[f"mae_{H}h"] <= -0.10).astype(float), np.nan)
    return out


def _rsum(x: pd.Series, w: int) -> pd.Series:
    return x.rolling(w, min_periods=max(1, w // 2)).sum()


def build_symbol(code: str, since: int, until: int, aux: dict[str, pd.DataFrame],
                 raw: pd.DataFrame | None = None, live: bool = False) -> pd.DataFrame | None:
    """raw: minute frame (ts o h l c qv n tbq) instead of the parquet archive (live use)."""
    d = _minutes(code, since, until, raw)
    if d is None:
        return None
    sym = f"{code[:-4]}/USDT"
    ts = d.index.to_numpy()
    c, h, l_, qv, n, tbq = d["c"], d["h"], d["l"], d["qv"], d["n"], d["tbq"]
    lr = np.log(c / c.shift(1))
    # decision points: hour starts inside [since, until); features read minute pos-1
    hours = ts[(ts % H_ == 0) & (ts >= since) & (ts < until)]
    if len(hours) == 0:
        return None
    pos = ((hours - ts[0]) // MIN).astype(int)
    prev = pos - 1
    ok = prev >= 0
    pos, prev, hours = pos[ok], prev[ok], hours[ok]
    F: dict[str, np.ndarray] = {}

    def at(s: pd.Series) -> np.ndarray:
        return s.to_numpy(np.float64)[prev]

    cc = c.to_numpy(np.float64)
    for m in (1, 5, 15, 60, 240, 1440, 4320, 10080):
        F[f"ret_{m}m"] = np.log(cc[prev] / cc[np.maximum(prev - m, 0)])
    for m, lab in ((60, "1h"), (1440, "24h"), (10080, "7d"), (43200, "30d")):
        F[f"dhi_{lab}"] = np.log(cc[prev] / at(h.rolling(m, min_periods=m // 3).max()))
        F[f"dlo_{lab}"] = np.log(cc[prev] / at(l_.rolling(m, min_periods=m // 3).min()))
    ch = c.iloc[::60]
    dh = ch.diff()
    up = dh.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-dh.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = (100 - 100 / (1 + up / dn.replace(0, np.nan))).reindex(c.index).ffill()
    F["rsi_1h"] = at(rsi)
    F["max1m_60"] = at(lr.rolling(60).max())
    F["min1m_60"] = at(lr.rolling(60).min())
    r5 = np.log(c / c.shift(5))
    F["max5m_1440"] = at(r5.rolling(1440, min_periods=300).max())
    F["bursts3_1440"] = at((r5 > 0.03).astype(float).rolling(1440, min_periods=300).sum())
    rv60 = lr.rolling(60, min_periods=30).std()
    rv1440 = lr.rolling(1440, min_periods=600).std()
    rv10080 = lr.rolling(10080, min_periods=3000).std()
    F["rv_1h"], F["rv_24h"], F["rv_7d"] = at(rv60), at(rv1440), at(rv10080)
    F["rv_ratio_1h_24h"] = F["rv_1h"] / F["rv_24h"]
    F["rv_ratio_24h_7d"] = F["rv_24h"] / F["rv_7d"]
    F["skew_24h"] = at(lr.rolling(1440, min_periods=600).skew())
    F["kurt_24h"] = at(lr.rolling(1440, min_periods=600).kurt())
    # volume vs its own 7-day baseline, per window
    base_q = qv.rolling(10080, min_periods=2000).mean()
    base_n = n.rolling(10080, min_periods=2000).mean()
    for m in (5, 15, 60, 240, 1440):
        F[f"vsurge_{m}m"] = at(_rsum(qv, m) / (base_q * m))
    F["nsurge_60m"] = at(_rsum(n, 60) / (base_n * 60))
    F["tsize_chg"] = at((_rsum(qv, 60) / _rsum(n, 60).replace(0, np.nan)) / (base_q / base_n.replace(0, np.nan)))
    dv24 = _rsum(qv, 1440)
    F["dv24_log"] = np.log1p(at(dv24))
    # order flow
    for m in (5, 15, 60, 240, 1440):
        F[f"taker_{m}m"] = at(_rsum(tbq, m) / _rsum(qv, m).replace(0, np.nan))
    tk60 = _rsum(tbq, 60) / _rsum(qv, 60).replace(0, np.nan)
    F["taker_z_60m"] = at((tk60 - tk60.rolling(10080, min_periods=2000).mean()) / tk60.rolling(10080, min_periods=2000).std())
    cvd = (2 * tbq - qv)
    for m in (60, 240, 1440):
        F[f"cvd_{m}m"] = at(_rsum(cvd, m) / dv24.replace(0, np.nan))
    # derivatives metrics (5m archive), funding
    st = _worker_storage()
    s0 = since - 40 * 86400
    met = _q(st, "SELECT ts, oi, oi_usd, ls_global, ls_top_pos, ls_top_acct, taker_ratio FROM metrics_5m "
                 "WHERE symbol = ? AND ts >= ? ORDER BY ts", (sym, s0)).set_index("ts").astype(float)
    fpath = ROOT / "data" / "cache" / "funding" / f"{code}.parquet"
    fu = None
    if live:
        fu = _q(st, "SELECT (timestamp/3600)*3600 AS ts, AVG(funding_rate) AS f FROM funding_rates "
                    "WHERE symbol = ? AND timestamp >= ? GROUP BY 1 ORDER BY 1", (sym, s0))
        fu = fu.set_index("ts")["f"].astype(float) if len(fu) else None
    elif fpath.exists():
        fu = pd.read_parquet(fpath)
        fu = fu[fu["ts"] >= s0].set_index("ts")["f"].astype(float)
        fu = fu[~fu.index.duplicated()] if len(fu) else None
    hs = pd.Index(hours)
    if met is not None and len(met):
        mm = met.reindex(met.index.union(hs - MIN)).ffill(limit=6).reindex(hs - MIN)
        oi = mm["oi"].to_numpy(np.float64)
        for k, lag in (("oi_1h", 1), ("oi_4h", 4), ("oi_24h", 24)):
            past = met["oi"].reindex(met["oi"].index.union(hs - MIN - lag * H_)).ffill(limit=6).reindex(hs - MIN).to_numpy(np.float64)
            F[k] = np.log(oi / past)
        oi_usd = mm["oi_usd"].to_numpy(np.float64)
        F["oi_dv"] = oi_usd / at(dv24)
        F["ls_global"] = mm["ls_global"].to_numpy(np.float64)
        F["ls_top_pos"] = mm["ls_top_pos"].to_numpy(np.float64)
        F["ls_top_acct"] = mm["ls_top_acct"].to_numpy(np.float64)
        F["smart_crowd"] = F["ls_top_pos"] / F["ls_global"]
        ls24 = met["ls_global"].reindex(met.index.union(hs - MIN - 24 * H_)).ffill(limit=6).reindex(hs - MIN).to_numpy(np.float64)
        F["ls_chg24"] = np.log(F["ls_global"] / ls24)
        tr = met["taker_ratio"].rolling(12, min_periods=3).mean()
        F["fut_taker_1h"] = tr.reindex(tr.index.union(hs - MIN)).ffill(limit=6).reindex(hs - MIN).to_numpy(np.float64)
    else:
        oi_usd = np.full(len(hours), np.nan)
    if fu is not None and len(fu):
        fz = (fu - fu.rolling(90, min_periods=30).mean()) / fu.rolling(90, min_periods=30).std()
        F["funding"] = fu.reindex(fu.index.union(hs)).ffill(limit=12).reindex(hs).to_numpy(np.float64)
        F["funding_z"] = fz.reindex(fz.index.union(hs)).ffill(limit=12).reindex(hs).to_numpy(np.float64)
    # size
    mc = aux["mcap"].get(sym)
    px = cc[prev]
    if mc is not None and len(mc):
        mcs = mc.reindex(mc.index.union(hs)).ffill(limit=40).reindex(hs).to_numpy(np.float64)
        mc_px = aux["mcap_px"][sym].reindex(aux["mcap_px"][sym].index.union(hs)).ffill(limit=40).reindex(hs).to_numpy(np.float64)
        mcap = mcs * px / mc_px
    else:
        sup = aux["supply"].get(sym, np.nan)
        mcap = px * sup
    F["mcap_log"] = np.log(mcap)
    F["oi_mcap"] = oi_usd / mcap
    F["turnover"] = at(dv24) / mcap
    first = int(ts[np.argmax(d["qv"].to_numpy() > 0)])
    F["age_days"] = (hours - first) / 86400.0
    F["age_censored"] = np.full(len(hours), float(first <= since - 30 * 86400))
    # events
    ev = aux["events"].get(code[:-4])
    for kind in ("listing", "warning", "delisting"):
        arr = np.zeros(len(hours))
        if ev is not None:
            for t in ev.get(kind, []):
                arr[(hours > t) & (hours <= t + 72 * H_)] = 1.0
        F[f"ev_{kind}72"] = arr
    F.update(_labels(d, pos))
    df = pd.DataFrame(F)
    df.insert(0, "ts", hours)
    df.insert(0, "symbol", sym)
    return df.replace([np.inf, -np.inf], np.nan)


# ------------------------------------------------------------------ aux data

def load_aux(storage: Any, since: int) -> dict[str, Any]:
    t0 = time.time()
    out: dict[str, Any] = {"since": since}
    try:
        cg = _q(storage, "SELECT symbol, ts + 3600 AS ts, mcap, price FROM cg_daily WHERE mcap > 0 AND ts >= ?",
                (since - 5 * 86400,))
    except Exception:  # noqa: BLE001
        cg = pd.DataFrame(columns=["symbol", "ts", "mcap", "price"])
    out["mcap"] = {s: g.set_index("ts")["mcap"].sort_index().astype(float) for s, g in cg.groupby("symbol")}
    out["mcap_px"] = {s: g.set_index("ts")["price"].sort_index().astype(float) for s, g in cg.groupby("symbol")}
    sup = _q(storage, "SELECT DISTINCT ON (symbol) symbol, supply FROM perp_5m WHERE supply > 0 ORDER BY symbol, ts DESC", ())
    out["supply"] = dict(zip(sup["symbol"], sup["supply"].astype(float)))
    notes = _q(storage, "SELECT ts, kind, symbols FROM exchange_notices WHERE ts >= ?", (since - 5 * 86400,))
    ev: dict[str, dict[str, list[int]]] = {}
    for _, r in notes.iterrows():
        for t in str(r["symbols"] or "").split(","):
            if t:
                ev.setdefault(t, {}).setdefault(r["kind"], []).append(int(r["ts"]))
    out["events"] = ev
    fng = _q(storage, "SELECT ts, value FROM fng_daily WHERE ts >= ?", (since - 10 * 86400,))
    out["fng"] = fng.set_index("ts")["value"].sort_index().astype(float)
    logger.info("aux loaded in %.0fs", time.time() - t0)
    return out


# ------------------------------------------------------------------ Korea

def korea_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Adds Korean-exchange features for rows where 1m KRW history exists."""
    frames = []
    for ex in ("upbit", "bithumb"):
        for p in (KRH / ex).glob("*.parquet") if (KRH / ex).exists() else []:
            frames.append(pd.read_parquet(p, columns=["exchange", "symbol", "ts", "c", "value_krw"]))
        for p in (KRA / ex).glob("*.parquet") if (KRA / ex).exists() else []:
            frames.append(pd.read_parquet(p, columns=["exchange", "symbol", "ts", "c", "value_krw"]))
    if not frames:
        return panel
    kr = pd.concat(frames).drop_duplicates(["exchange", "symbol", "ts"])
    kr_start = int(kr["ts"].min())
    # USDT/KRW for FX
    fx = {}
    for ex, g in kr[kr["symbol"] == "USDT"].groupby("exchange"):
        fx[ex] = g.set_index("ts")["c"].sort_index()
    base = panel["symbol"].str.split("/").str[0]
    hs = panel["ts"].to_numpy()
    cols: dict[str, np.ndarray] = {k: np.full(len(panel), np.nan) for k in (
        "kr_listed", "kimchi", "kr_share_1h", "kr_share_24h", "kr_surge_15m", "kr_surge_60m", "kr_lead_15m")}
    in_kr = hs >= kr_start + 7 * 86400
    cols["kr_listed"][in_kr] = 0.0
    val_by = {}
    for (ex, s), g in kr.groupby(["exchange", "symbol"]):
        if s == "USDT" or ex not in fx:
            continue
        g = g.set_index("ts").sort_index()
        idx = pd.RangeIndex(int(g.index[0]), int(g.index[-1]) + MIN, MIN)
        g = g.reindex(idx)
        usd_val = (g["value_krw"].fillna(0) / fx[ex].reindex(idx).ffill()).fillna(0)
        px_usd = g["c"].ffill() / fx[ex].reindex(idx).ffill()
        val_by.setdefault(s, []).append((usd_val, px_usd))
    for s, parts in val_by.items():
        rows = np.where((base.to_numpy() == s) & in_kr)[0]
        if not len(rows):
            continue
        tot = parts[0][0]
        for v, _ in parts[1:]:
            tot = tot.add(v, fill_value=0)
        px = parts[0][1]
        t_prev = hs[rows] - MIN
        def at(x: pd.Series) -> np.ndarray:
            return x.reindex(x.index.union(t_prev)).ffill(limit=5).reindex(t_prev).to_numpy(np.float64)
        r60, r1440 = tot.rolling(60, min_periods=1).sum(), tot.rolling(1440, min_periods=60).sum()
        base_ = tot.rolling(10080, min_periods=1440).mean()
        cols["kr_listed"][rows] = 1.0
        cols["kr_surge_15m"][rows] = at(tot.rolling(15, min_periods=1).sum() / (base_ * 15))
        cols["kr_surge_60m"][rows] = at(r60 / (base_ * 60))
        cols["kr_share_1h"][rows] = at(r60)
        cols["kr_share_24h"][rows] = at(r1440)
        cols["kimchi"][rows] = at(px)
        cols["kr_lead_15m"][rows] = at(np.log(px / px.shift(15)))
    out = panel.copy()
    out["kr_listed"] = cols["kr_listed"]
    out["kr_surge_15m"] = cols["kr_surge_15m"]
    out["kr_surge_60m"] = cols["kr_surge_60m"]
    dv1h = np.expm1(out["dv24_log"]) * out["vsurge_60m"] / 24 / out["vsurge_1440m"].replace(0, np.nan)
    out["kr_share_1h"] = cols["kr_share_1h"] / dv1h
    out["kr_share_24h"] = cols["kr_share_24h"] / np.expm1(out["dv24_log"])
    out["_kr_px"] = cols["kimchi"]
    out["kr_lead_15m"] = cols["kr_lead_15m"] - out["ret_15m"]
    return out


# ------------------------------------------------------------------ cross-section

def cross_features(p: pd.DataFrame, fng: pd.Series) -> pd.DataFrame:
    g = p.groupby("ts")
    btc = p[p["symbol"] == "BTC/USDT"].set_index("ts")
    for k in ("ret_60m", "ret_240m", "ret_1440m"):
        p[f"btc_{k}"] = p["ts"].map(btc[k])
        p[f"mkt_{k}"] = g[k].transform("median")
        p[f"xs_{k}"] = p[k] - p[f"mkt_{k}"]
    p["breadth_24h"] = g["ret_1440m"].transform(lambda x: (x > 0).mean())
    for k in ("ret_1440m", "ret_60m", "vsurge_60m", "taker_60m", "oi_4h", "rv_24h", "dv24_log", "mcap_log",
              "oi_mcap", "funding"):
        if k in p:
            p[f"rk_{k}"] = g[k].rank(pct=True)
    fz = fng.reindex(fng.index.union(p["ts"].unique())).ffill(limit=30)
    p["fng"] = p["ts"].map(fz)
    p["hour"] = (p["ts"] // H_) % 24
    p["weekday"] = (p["ts"] // 86400 + 3) % 7
    return p


# ------------------------------------------------------------------ build all

def _worker(args):
    code, since, until = args
    try:
        return build_symbol(code, since, until, _AUX)
    except Exception as e:  # noqa: BLE001
        logger.warning("%s failed: %r", code, e)
        return None


_AUX: dict[str, Any] = {}
_WS: Any = None


def _worker_storage() -> Any:
    """Each worker process gets its own connection pool (pools do not survive fork)."""
    global _WS
    if _WS is None:
        from src.data.storage import Storage
        _WS = Storage()
    return _WS


def add_korea(p: pd.DataFrame) -> pd.DataFrame:
    p = p.drop(columns=[c for c in p.columns if c.startswith(("kr_", "kimchi"))])
    p = korea_features(p)
    if "_kr_px" in p:
        p["kimchi"] = p["_kr_px"] / _binance_px(p) - 1
        p = p.drop(columns="_kr_px")
    return p


def build_panel(storage: Any, days: int = 180, workers: int = 6, log=logger.info, korea: bool = True) -> pd.DataFrame:
    import multiprocessing as mp
    global _AUX
    until = int(time.time()) // 86400 * 86400 - 86400      # leave a day for the 24h labels
    since = until - days * 86400
    _AUX = load_aux(storage, since)
    codes = sorted(p.stem for p in K1M.glob("*.parquet"))
    log(f"building {len(codes)} symbols, {days} days")
    parts = []
    ctx = mp.get_context("fork")
    with ctx.Pool(workers) as pool:
        for i, df in enumerate(pool.imap_unordered(_worker, [(c, since, until) for c in codes], chunksize=4)):
            if df is not None and len(df):
                for k in df.columns:
                    if df[k].dtype == np.float64:
                        df[k] = df[k].astype(np.float32)
                parts.append(df)
            if i % 50 == 0:
                log(f"  {i + 1}/{len(codes)}")
    p = pd.concat(parts, ignore_index=True)
    p = cross_features(p, _AUX["fng"])
    if korea:
        p = add_korea(p)
    for k in p.columns:
        if p[k].dtype == np.float64:
            p[k] = p[k].astype(np.float32)
    p.to_parquet(PANEL, index=False)
    return p


def _binance_px(p: pd.DataFrame) -> np.ndarray:
    """Binance close of the minute before each decision hour, from the 1m files."""
    out = np.full(len(p), np.nan)
    for sym, idx in p.groupby("symbol").groups.items():
        code = sym.replace("/", "")
        path = K1M / f"{code}.parquet"
        if not path.exists():
            continue
        d = pd.read_parquet(path, columns=["ts", "c"]).set_index("ts")["c"]
        t = p.loc[idx, "ts"].to_numpy() - MIN
        out[np.asarray(idx)] = d.reindex(d.index.union(t)).ffill(limit=30).reindex(t).to_numpy()
    return out


LABELS = [f"{k}_{H}h" for H in HORIZONS for k in ("mfe", "mae", "ret", "up10", "win10", "dn10")]


def feature_cols(p: pd.DataFrame) -> list[str]:
    return [c for c in p.columns if c not in LABELS and c not in ("symbol", "ts")]
