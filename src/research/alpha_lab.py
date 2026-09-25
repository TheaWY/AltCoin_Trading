"""Alpha lab: what predicts a coin's NEXT 24 HOURS -- especially 10%+ moves --
using every hourly signal we have, on ~90 days (the span of the hourly open
interest / long-short history).

Signals (hourly, point-in-time):
  price/momentum  returns 1h..7d, cross-sectional rank, excess vs BTC,
                  distance from 1d/7d/30d highs and lows, RSI, MA distance
  volatility      realised vol 24h/7d, vol ratio, 24h range
  volume/size     dollar volume, volume surge, turnover = volume / market cap,
                  Amihud illiquidity, market cap
  sentiment       funding level / z-score / change, long-short ratio level /
                  z-score / change, open interest change 1h..3d, OI / market
                  cap, OI / volume, OI-vs-price divergence, liquidations
                  (long, short, net) scaled by volume
  market          BTC 24h return, breadth, market median return

Market cap = latest circulating supply (Binance futures data) x price, so it
tracks price but not supply changes (unlocks) -- an approximation.

Targets: fwd24 = close[t+24] / close[t] - 1; up10 = fwd24 >= +10%,
dn10 = fwd24 <= -10%.

Tests
  1. every signal alone: daily cross-sectional rank IC with fwd24 (one
     snapshot per day so targets never overlap), t-stat, first vs second
     half; AUC for up10 and dn10
  2. how signals relate: rank-correlation matrix and clusters of signals
     that say the same thing
  3. how strength depends on size and sentiment: IC inside market-cap
     terciles, and the key interactions (pump x OI surge, pump x funding)
  4. all signals together: gradient-boosted trees, walk-forward (train only
     on the past, 24h embargo), out-of-sample AUC, precision of the top 1% /
     5%, a daily long-top / short-bottom book net of costs, and permutation
     importance -> which signals the model actually uses
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable

import numpy as np
import pandas as pd

HOUR = 3600
H = 24
MOVE = 0.10
COST = 0.003


def _wide(rows, col: str) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df["timestamp"] = df["timestamp"].astype("int64") // HOUR * HOUR
    return df.pivot_table(index="timestamp", columns="symbol", values=col, aggfunc="last").sort_index()


def _q(storage: Any, sql: str, params: tuple) -> pd.DataFrame:
    """Query straight into a DataFrame (tuples, not dicts: millions of rows)."""
    with storage._connect() as c:  # noqa: SLF001
        with c.raw.cursor() as cur:
            cur.execute(sql.replace("?", "%s"), params)
            cols = [d[0] for d in cur.description]
            return pd.DataFrame.from_records(cur.fetchall(), columns=cols)


def _pivot(df: pd.DataFrame, col: str, ts: str = "ts") -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    return df.pivot_table(index=ts, columns="symbol", values=col, aggfunc="last").sort_index().astype("float64")


def load(storage: Any, days: int = 185) -> dict[str, pd.DataFrame]:
    """Every USDT perp with hourly prices over `days`. Sentiment comes from
    metrics_5m (Binance archive: OI, long/short, taker ratio, full history),
    falling back to the older hourly open_interest / long_short_ratio tables."""
    since = int(time.time()) - days * 86400
    px = _q(storage, "SELECT symbol, (timestamp/3600)*3600 AS ts, high, low, close, volume FROM prices "
                     "WHERE timeframe='1h_perp' AND timestamp >= ?", (since,))
    sp = _q(storage, "SELECT symbol, (timestamp/3600)*3600 AS ts, high, low, close, volume FROM prices "
                     "WHERE timeframe='1h' AND timestamp >= ?", (since,))
    met = _q(storage, "SELECT DISTINCT ON (symbol, ts/3600) symbol, (ts/3600)*3600 + 3600 AS ts, oi, oi_usd, "
                      "ls_top_acct, ls_top_pos, ls_global, taker_ratio FROM metrics_5m WHERE ts >= ? "
                      "ORDER BY symbol, ts/3600, ts DESC", (since,))
    tk = _q(storage, "SELECT symbol, (ts/3600)*3600 + 3600 AS ts, AVG(taker_ratio) AS taker_1h FROM metrics_5m "
                     "WHERE ts >= ? GROUP BY 1, 2", (since,))
    fu = _q(storage, "SELECT symbol, (timestamp/3600)*3600 AS ts, funding_rate FROM funding_rates WHERE timestamp >= ?",
            (since - 86400,))
    lq = _q(storage, "SELECT symbol, timestamp AS ts, long_liq_notional, short_liq_notional FROM liquidation_agg_1h "
                     "WHERE timestamp >= ?", (since,))
    try:
        sup = _q(storage, "SELECT DISTINCT ON (symbol) symbol, supply FROM perp_5m WHERE supply IS NOT NULL "
                          "ORDER BY symbol, ts DESC", ())
    except Exception:  # noqa: BLE001
        sup = pd.DataFrame(columns=["symbol", "supply"])
    try:
        up = _q(storage, "SELECT symbol, ts, close, value_krw FROM upbit_1h WHERE ts >= ?", (since,))
        notes = _q(storage, "SELECT source, ts, kind, symbols FROM exchange_notices WHERE ts >= ?", (since - 7 * 86400,))
    except Exception:  # noqa: BLE001
        up, notes = pd.DataFrame(), pd.DataFrame()

    perp_syms = set(px["symbol"].unique()) | set(met["symbol"].unique())
    p: dict[str, pd.DataFrame] = {}
    for k in ("high", "low", "close", "volume"):
        p[k] = _pivot(px, k).combine_first(_pivot(sp, k))
    cols = pd.Index(sorted(c for c in p["close"].columns if c in perp_syms))
    idx = pd.RangeIndex(int(p["close"].index.min()), int(p["close"].index.max()) + HOUR, HOUR)
    p = {k: v.reindex(index=idx, columns=cols) for k, v in p.items()}
    def rx(f: pd.DataFrame, lim: int = 3) -> pd.DataFrame:
        if f.empty:
            return pd.DataFrame(np.nan, index=idx, columns=cols)
        g = f.reindex(index=idx.union(f.index))
        if lim:
            g = g.ffill(limit=lim)
        return g.reindex(index=idx, columns=cols)
    for k in ("oi", "oi_usd", "ls_top_acct", "ls_top_pos", "ls_global"):
        p[k] = rx(_pivot(met, k))
    p["taker_fut"] = rx(_pivot(tk, "taker_1h"))
    p["funding"] = rx(_pivot(fu, "funding_rate"), 12)
    p["liq_long"] = rx(_pivot(lq, "long_liq_notional"), 0).fillna(0)
    p["liq_short"] = rx(_pivot(lq, "short_liq_notional"), 0).fillna(0)
    if not lq.empty:  # before liquidation collection started, "no data" is not "zero liquidations"
        first = int(lq["ts"].min())
        for k in ("liq_long", "liq_short"):
            p[k].loc[p[k].index < first] = np.nan
    supply = sup.set_index("symbol")["supply"].astype(float).reindex(cols) if not sup.empty else pd.Series(np.nan, index=cols)
    p["mcap"] = p["close"].mul(supply, axis=1)
    p["dv"] = p["close"] * p["volume"]
    # Upbit: KRW price and value, USDT/KRW for FX (+ the Korean premium baseline)
    if not up.empty:
        ukrw = _pivot(up, "close").reindex(index=idx)
        uval = _pivot(up, "value_krw").reindex(index=idx)
        usdt = ukrw.get("USDT/KRW")
        if usdt is not None:
            usdt = usdt.ffill(limit=24)
            p["kimchi"] = ukrw.reindex(columns=cols).div(usdt, axis=0) / p["close"] - 1
            p["upbit_dv"] = uval.reindex(columns=cols).div(usdt, axis=0)
    # exchange notices -> hours since the last listing / warning / delisting notice per coin
    for kind in ("listing", "warning", "delisting"):
        ev = pd.DataFrame(0.0, index=idx, columns=cols)
        if not notes.empty:
            for _, n in notes[notes["kind"] == kind].iterrows():
                for t in str(n["symbols"] or "").split(","):
                    sym = f"{t}/USDT"
                    if t and sym in ev.columns:
                        h = int(n["ts"]) // HOUR * HOUR + HOUR
                        if h in ev.index:
                            ev.at[h, sym] = 1.0
                        elif idx[0] > h >= idx[0] - 7 * 86400:
                            ev.iat[0, ev.columns.get_loc(sym)] = 1.0
        p[f"ev_{kind}"] = ev
    return p


# ------------------------------------------------------------------ signals

TEXT: dict[str, tuple[str, str]] = {}


def _rsi(c: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def signals(p: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    c, hi, lo, dv, mcap = p["close"], p["high"], p["low"], p["dv"], p["mcap"]
    oi_usd = p["oi_usd"] if "oi_usd" in p else p["oi"] * c
    lr = np.log(c / c.shift(1))
    btc = lr["BTC/USDT"] if "BTC/USDT" in lr else lr.median(axis=1)
    s: dict[str, pd.DataFrame] = {}

    def add(name, cat, ko, f):
        TEXT[name] = (cat, ko)
        s[name] = f.replace([np.inf, -np.inf], np.nan)

    for h in (1, 4, 12, 24, 72, 168):
        add(f"r{h}", "가격", f"{h}시간 수익률", np.log(c / c.shift(h)))
    add("r24_rank", "가격", "24시간 수익률 순위 (1=최고)", s["r24"].rank(axis=1, pct=True))
    add("r168_rank", "가격", "7일 수익률 순위", s["r168"].rank(axis=1, pct=True))
    add("xbtc24", "가격", "24시간 BTC 대비 초과수익", s["r24"].sub(btc.rolling(24).sum(), axis=0))
    add("xbtc168", "가격", "7일 BTC 대비 초과수익", s["r168"].sub(btc.rolling(168).sum(), axis=0))
    for h, lab in ((24, "1일"), (168, "7일"), (720, "30일")):
        add(f"dhi{h}", "가격위치", f"{lab} 고점 대비", np.log(c / hi.rolling(h, min_periods=h // 2).max()))
        add(f"dlo{h}", "가격위치", f"{lab} 저점 대비", np.log(c / lo.rolling(h, min_periods=h // 2).min()))
    add("rsi14", "가격위치", "RSI 14 (1시간봉)", _rsi(c))
    add("ma24", "가격위치", "24시간 평균 대비 이격", np.log(c / c.rolling(24).mean()))
    add("ma168", "가격위치", "7일 평균 대비 이격", np.log(c / c.rolling(168, min_periods=100).mean()))

    add("rv24", "변동성", "24시간 변동성", lr.rolling(24, min_periods=12).std())
    add("rv168", "변동성", "7일 변동성", lr.rolling(168, min_periods=84).std())
    add("rv_ratio", "변동성", "24시간 ÷ 7일 변동성 (확대 중이면 >1)", s["rv24"] / s["rv168"])
    add("range24", "변동성", "24시간 고저폭", np.log(hi.rolling(24).max() / lo.rolling(24).min()))

    dv24 = dv.rolling(24, min_periods=12).sum()
    add("dv24", "거래량·규모", "24시간 거래대금 (log)", np.log1p(dv24))
    add("dv_surge", "거래량·규모", "24시간 거래대금 ÷ 직전 7일 평균", dv24 / (dv.rolling(168, min_periods=84).sum().shift(24) / 7))
    add("dv_surge4", "거래량·규모", "4시간 거래대금 ÷ 직전 7일 평균 4시간", dv.rolling(4).sum() / (dv.rolling(168, min_periods=84).sum().shift(4) / 42))
    add("turnover", "거래량·규모", "24시간 거래대금 ÷ 시가총액 (회전율)", dv24 / mcap)
    add("amihud", "거래량·규모", "비유동성: |수익률| ÷ 거래대금 (24시간)", (lr.abs() / dv.replace(0, np.nan)).rolling(24, min_periods=12).mean() * 1e6)
    add("mcap", "거래량·규모", "시가총액 (log)", np.log(mcap))

    f = p["funding"]
    add("funding", "심리", "펀딩비", f)
    add("funding_z", "심리", "펀딩비 z점수 (30일)", (f - f.rolling(720, min_periods=240).mean()) / f.rolling(720, min_periods=240).std())
    add("funding_chg", "심리", "펀딩비 24시간 변화", f - f.shift(24))
    ls = p["ls_global"]
    add("ls", "심리", "롱/숏 계정 비율 (전체)", ls)
    add("ls_top_pos", "심리", "상위 트레이더 포지션 롱/숏", p["ls_top_pos"])
    add("ls_top_acct", "심리", "상위 트레이더 계정 롱/숏", p["ls_top_acct"])
    add("smart_crowd", "심리", "상위 트레이더 ÷ 전체 롱숏 (고수가 더 롱이면 >1)", p["ls_top_pos"] / ls)
    add("smart_chg24", "심리", "상위 트레이더 롱숏 24시간 변화", np.log(p["ls_top_pos"] / p["ls_top_pos"].shift(24)))
    tf = p["taker_fut"]
    add("taker_fut", "심리", "선물 시장가 매수/매도 비율 (1시간)", tf)
    add("taker_fut24", "심리", "선물 시장가 매수/매도 비율 (24시간 평균)", tf.rolling(24, min_periods=12).mean())
    add("ls_z", "심리", "롱/숏 비율 z점수 (30일)", (ls - ls.rolling(720, min_periods=240).mean()) / ls.rolling(720, min_periods=240).std())
    add("ls_chg24", "심리", "롱/숏 비율 24시간 변화", np.log(ls / ls.shift(24)))
    for h in (1, 4, 24, 72):
        add(f"oi{h}", "심리", f"미결제약정 {h}시간 변화", np.log(p["oi"] / p["oi"].shift(h)))
    add("oi_mcap", "심리", "미결제약정 ÷ 시가총액 (레버리지)", oi_usd / mcap)
    add("oi_dv", "심리", "미결제약정 ÷ 24시간 거래대금", oi_usd / dv24)
    add("oi_div24", "심리", "24시간 OI 변화 - 가격 변화 (가격보다 OI가 앞서면 +)", s["oi24"] - s["r24"])
    ll, ls_ = p["liq_long"].rolling(24).sum(), p["liq_short"].rolling(24).sum()
    add("liq_long24", "청산", "24시간 롱 청산 ÷ 거래대금", ll / dv24)
    add("liq_short24", "청산", "24시간 숏 청산 ÷ 거래대금", ls_ / dv24)
    add("liq_net24", "청산", "(숏-롱) 청산 ÷ 거래대금", (ls_ - ll) / dv24)
    add("liq_mcap", "청산", "24시간 총 청산 ÷ 시가총액", (ll + ls_) / mcap)

    if "kimchi" in p:
        k = p["kimchi"]
        add("kimchi", "한국", "김치 프리미엄 (업비트 원화가 ÷ 바이낸스가, USDT 환율 기준)", k)
        add("kimchi_chg24", "한국", "김치 프리미엄 24시간 변화", k - k.shift(24))
        udv24 = p["upbit_dv"].rolling(24, min_periods=6).sum()
        add("upbit_share", "한국", "업비트 거래대금 ÷ 바이낸스 선물 거래대금 (24시간)", udv24 / dv24)
        add("upbit_surge", "한국", "업비트 4시간 거래대금 ÷ 직전 7일 평균", p["upbit_dv"].rolling(4).sum()
            / (p["upbit_dv"].rolling(168, min_periods=48).sum().shift(4) / 42))
        add("on_upbit", "한국", "업비트 원화마켓 상장 여부", p["upbit_dv"].notna().astype(float).rolling(24).max())
    for kind, ko in (("listing", "상장/신규지원"), ("warning", "유의종목 지정"), ("delisting", "상장폐지")):
        ev = p.get(f"ev_{kind}")
        if ev is not None:
            add(f"ev_{kind}72", "공지", f"최근 72시간 내 {ko} 공지 (업비트/바이낸스)", ev.rolling(72, min_periods=1).max())

    bc = lambda v: pd.DataFrame(np.repeat(v.to_numpy()[:, None], c.shape[1], axis=1), index=c.index, columns=c.columns)  # noqa: E731
    add("btc24", "시장", "BTC 24시간 수익률", bc(btc.rolling(24).sum()))
    add("breadth24", "시장", "24시간 오른 코인 비율", bc((s["r24"] > 0).where(s["r24"].notna()).mean(axis=1)))
    add("mkt24", "시장", "전체 코인 24시간 수익률 중앙값", bc(s["r24"].median(axis=1)))
    return s


# ------------------------------------------------------------------ analysis

def table(p: dict[str, pd.DataFrame], s: dict[str, pd.DataFrame], min_dv24: float = 1e6) -> pd.DataFrame:
    c = p["close"]
    fwd = c.shift(-H) / c - 1
    dv24 = p["dv"].rolling(24, min_periods=12).sum()
    parts = {k: v.stack(future_stack=True).astype("float32") for k, v in s.items()}
    df = pd.DataFrame(parts)
    df["fwd24"] = fwd.stack(future_stack=True).astype("float32")
    df["dv24_usd"] = dv24.stack(future_stack=True).astype("float32")
    df = df[(df["dv24_usd"] >= min_dv24) & df["fwd24"].notna()]
    df.index.names = ["ts", "symbol"]
    df = df.reset_index()
    df["up10"] = df["fwd24"] >= MOVE
    df["dn10"] = df["fwd24"] <= -MOVE
    df["day"] = df["ts"] // 86400
    return df


def _auc(pos: np.ndarray, neg: np.ndarray) -> float | None:
    pos, neg = pos[np.isfinite(pos)], neg[np.isfinite(neg)]
    if len(pos) < 10 or len(neg) < 10:
        return None
    r = pd.Series(np.concatenate([pos, neg])).rank().to_numpy()
    return float((r[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def univariate(df: pd.DataFrame, feats: list[str]) -> list[dict[str, Any]]:
    snap = df[df["ts"] % 86400 == 0]                      # one snapshot per day: targets never overlap
    days = np.sort(snap["day"].unique())
    mid_day = days[len(days) // 2] if len(days) else 0
    mid_ts = df["ts"].quantile(0.5)
    out = []
    for f in feats:
        ics = snap.groupby("day").apply(lambda g: g[f].rank().corr(g["fwd24"].rank()) if g[f].notna().sum() > 10 else np.nan,
                                        include_groups=False).dropna()
        row = {"name": f, "category": TEXT[f][0], "ko": TEXT[f][1], "days": int(len(ics))}
        for part, ser in (("all", ics), ("h1", ics[ics.index < mid_day]), ("h2", ics[ics.index >= mid_day])):
            n = len(ser)
            row[f"ic_{part}"] = float(ser.mean()) if n else None
            row[f"t_{part}"] = float(ser.mean() / ser.std(ddof=1) * math.sqrt(n)) if n > 2 and ser.std(ddof=1) > 0 else None
        for lab in ("up10", "dn10"):
            for part, m in (("h1", df["ts"] < mid_ts), ("h2", df["ts"] >= mid_ts)):
                d = df[m]
                row[f"{lab}_auc_{part}"] = _auc(d.loc[d[lab], f].to_numpy(), d.loc[~d[lab], f].to_numpy())
        row["coverage"] = float(df[f].notna().mean())
        a, b = row.get("ic_h1"), row.get("ic_h2")
        row["ic_holds"] = bool(a is not None and b is not None and a * b > 0 and min(abs(a), abs(b)) >= 0.02)
        out.append(row)
    out.sort(key=lambda r: -abs(r.get("t_all") or 0))
    return out


def correlations(df: pd.DataFrame, feats: list[str], cut: float = 0.7) -> dict[str, Any]:
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform

    sample = df[feats].sample(min(60_000, len(df)), random_state=0)
    corr = sample.rank().corr().fillna(0.0)
    dist = 1 - corr.abs().to_numpy()
    np.fill_diagonal(dist, 0)
    lab = fcluster(linkage(squareform(np.clip(dist, 0, None), checks=False), "average"), 1 - cut, "distance")
    clusters: dict[int, list[str]] = {}
    for f, k in zip(feats, lab):
        clusters.setdefault(int(k), []).append(f)
    pairs = []
    for i, a in enumerate(feats):
        for b in feats[i + 1:]:
            if abs(corr.loc[a, b]) >= cut:
                pairs.append([a, b, round(float(corr.loc[a, b]), 2)])
    pairs.sort(key=lambda x: -abs(x[2]))
    return {"clusters": sorted(clusters.values(), key=len, reverse=True), "pairs": pairs[:60],
            "matrix": {a: {b: round(float(corr.loc[a, b]), 2) for b in feats} for a in feats}}


def by_size(df: pd.DataFrame, feats: list[str]) -> list[dict[str, Any]]:
    snap = df[df["ts"] % 86400 == 0].copy()
    snap["size"] = snap.groupby("day")["mcap"].transform(lambda x: pd.qcut(x.rank(method="first"), 3, labels=False)
                                                          if x.notna().sum() >= 9 else np.nan)
    out = []
    for f in feats:
        row = {"name": f}
        for k, lab in ((0, "small"), (1, "mid"), (2, "large")):
            g = snap[snap["size"] == k]
            ics = g.groupby("day").apply(lambda x: x[f].rank().corr(x["fwd24"].rank()) if x[f].notna().sum() > 8 else np.nan,
                                         include_groups=False).dropna()
            row[lab] = float(ics.mean()) if len(ics) else None
            row[f"{lab}_t"] = float(ics.mean() / ics.std(ddof=1) * math.sqrt(len(ics))) if len(ics) > 2 and ics.std() > 0 else None
        out.append(row)
    return out


def interactions(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Conditional outcomes for the patterns the retrospective pointed at."""
    d = df.copy()
    d["pumped"] = d["r24_rank"] >= 0.95
    d["dumped"] = d["r24_rank"] <= 0.05
    med = lambda col: d.groupby("day")[col].transform("median")  # noqa: E731
    conds = {
        "24h 상위 5% 급등": d["pumped"],
        "  + 미결제약정 24h 증가(중앙값 위)": d["pumped"] & (d["oi24"] > med("oi24")),
        "  + 미결제약정 24h 감소": d["pumped"] & (d["oi24"] <= med("oi24")),
        "  + 펀딩비 양수 상위": d["pumped"] & (d["funding"] > med("funding")),
        "  + 롱숏비율 상승": d["pumped"] & (d["ls_chg24"] > 0),
        "  + 소형 (시총 하위 1/3)": d["pumped"] & (d["mcap"] <= d.groupby("day")["mcap"].transform(lambda x: x.quantile(1 / 3))),
        "  + 대형 (시총 상위 1/3)": d["pumped"] & (d["mcap"] >= d.groupby("day")["mcap"].transform(lambda x: x.quantile(2 / 3))),
        "24h 하위 5% 급락": d["dumped"],
        "  + 롱 청산 많음": d["dumped"] & (d["liq_long24"] > med("liq_long24")),
        "  + 미결제약정 감소": d["dumped"] & (d["oi24"] < med("oi24")),
        "  + 펀딩비 음수": d["dumped"] & (d["funding"] < 0),
        "레버리지 상위 5% (OI/시총)": d["oi_mcap"] >= d.groupby("day")["oi_mcap"].transform(lambda x: x.quantile(0.95)),
        "OI가 가격보다 앞섬 상위 5%": d["oi_div24"] >= d.groupby("day")["oi_div24"].transform(lambda x: x.quantile(0.95)),
        "전체 (기준)": pd.Series(True, index=d.index),
    }
    out = []
    mid = d["ts"].quantile(0.5)
    for name, m in conds.items():
        g = d[m.fillna(False)]
        row = {"condition": name, "n": int(len(g)), "coins": int(g["symbol"].nunique())}
        for part, mm in (("h1", g["ts"] < mid), ("h2", g["ts"] >= mid)):
            x = g.loc[mm, "fwd24"]
            row[f"{part}_mean"] = float(x.mean()) if len(x) else None
            row[f"{part}_up10"] = float((x >= MOVE).mean()) if len(x) else None
            row[f"{part}_dn10"] = float((x <= -MOVE).mean()) if len(x) else None
        out.append(row)
    return out


def model(df: pd.DataFrame, feats: list[str], log: Callable[[str], None] = lambda _m: None) -> dict[str, Any]:
    """Walk-forward gradient boosting. Each fold trains only on rows whose 24h
    target ended before the fold starts, then predicts the next block."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.inspection import permutation_importance

    days = np.sort(df["day"].unique())
    start = int(len(days) * 0.4)
    step = max(5, (len(days) - start) // 5)
    preds = []
    last = {}
    for k in range(start, len(days), step):
        test_days = days[k:k + step]
        tr = df[df["day"] < test_days[0] - 1]          # 1-day embargo
        te = df[df["day"].isin(test_days)]
        if len(tr) < 5000 or te.empty:
            continue
        out = te[["ts", "day", "symbol", "fwd24", "up10", "dn10"]].copy()
        use = [f for f in feats if tr[f].notna().mean() > 0.05]   # a signal with no history yet cannot be learned
        for lab in ("up10", "dn10"):
            clf = HistGradientBoostingClassifier(max_iter=250, learning_rate=0.05, max_leaf_nodes=31,
                                                 min_samples_leaf=200, l2_regularization=1.0, random_state=0)
            clf.fit(tr[use], tr[lab])
            out[f"p_{lab}"] = clf.predict_proba(te[use])[:, 1]
            last[lab] = (clf, te, use)
        preds.append(out)
        log(f"fold from day {int(test_days[0])}: train {len(tr)} test {len(te)}")
    if not preds:
        return {"note": "not enough data"}
    oos = pd.concat(preds)
    res: dict[str, Any] = {"oos_rows": int(len(oos)), "oos_days": int(oos["day"].nunique()),
                           "oos_from": int(oos["ts"].min())}
    for lab in ("up10", "dn10"):
        y, p = oos[lab].to_numpy(), oos[f"p_{lab}"].to_numpy()
        base = float(y.mean())
        r = {"base_rate": base, "auc": _auc(p[y], p[~y])}
        for top in (0.01, 0.05):
            cut = np.quantile(p, 1 - top)
            sel = p >= cut
            r[f"precision_top{int(top * 100)}"] = float(y[sel].mean())
            r[f"lift_top{int(top * 100)}"] = float(y[sel].mean() / base) if base else None
            r[f"mean_fwd_top{int(top * 100)}"] = float(oos.loc[sel, "fwd24"].mean())
        clf, te, use = last[lab]
        smp = te.sample(min(15_000, len(te)), random_state=0)
        if smp[lab].sum() >= 20:
            pi = permutation_importance(clf, smp[use], smp[lab], scoring="roc_auc", n_repeats=3, random_state=0)
            r["importance"] = sorted([[f, round(float(m), 4)] for f, m in zip(use, pi.importances_mean)],
                                     key=lambda x: -x[1])[:15]
        res[lab] = r
    # trading: once a day at 00 UTC, long the top-k by p_up - p_dn, short the bottom-k, hold 24h
    snap = oos[oos["ts"] % 86400 == 0].copy()
    snap["score"] = snap["p_up10"] - snap["p_dn10"]
    books = {}
    for k in (3, 5, 10):
        daily = []
        for _, g in snap.groupby("day"):
            if len(g) < 2 * k:
                continue
            g = g.sort_values("score")
            long_r = g.tail(k)["fwd24"].mean() - COST
            short_r = -g.head(k)["fwd24"].mean() - COST
            daily.append((long_r, short_r))
        if daily:
            a = np.array(daily)
            books[f"top{k}"] = {
                "days": len(a),
                "long_mean": float(a[:, 0].mean()), "long_win": float((a[:, 0] > 0).mean()),
                "short_mean": float(a[:, 1].mean()), "short_win": float((a[:, 1] > 0).mean()),
                "ls_mean": float(a.mean(axis=1).mean()),
                "ls_t": float(a.mean(axis=1).mean() / a.mean(axis=1).std(ddof=1) * math.sqrt(len(a))) if len(a) > 2 else None,
            }
    res["books"] = books
    return res


def backtests(p: dict[str, pd.DataFrame], s: dict[str, pd.DataFrame], min_dv24: float = 1e6) -> dict[str, Any]:
    """The three candidates from the first alpha pass, as daily books on the
    whole period: rebalance at 00:00 UTC, hold 24h, 0.3% round trip.

      quiet_vs_noisy   long the calmest decile (low 24h vol + low turnover),
                       short the noisiest decile; also the long leg alone
      leverage_long    long the top 5% by open interest / market cap
      breakout_both    on the 10 most volatile coins, enter whichever side
                       first moves X% from the 00:00 price (stop back at the
                       00:00 price, exit at the 24h close); an hour that
                       touches both sides counts as a loss
    """
    c, hi, lo = p["close"], p["high"], p["low"]
    dv24 = p["dv"].rolling(24, min_periods=12).sum()
    fwd = c.shift(-H) / c - 1
    idx = np.asarray(c.index)
    days = [i for i in range(len(idx) - H) if idx[i] % 86400 == 0]
    ok = (dv24 >= min_dv24).to_numpy() & np.isfinite(fwd.to_numpy())
    F = fwd.to_numpy()
    rv = s["rv24"].to_numpy()
    turn = s["turnover"].to_numpy()
    dv_log = s["dv24"].to_numpy()
    lev = s["oi_mcap"].to_numpy()
    oidv = s["oi_dv"].to_numpy()
    C, HI, LO = c.to_numpy(), hi.to_numpy(), lo.to_numpy()
    out: dict[str, list] = {k: [] for k in ("quiet_long", "noisy_short", "quiet_vs_noisy", "leverage_long",
                                             "breakout_5", "breakout_8", "universe",
                                             "oidv_long", "leverage_long_all")}
    stamps = []
    for i in days:
        m = ok[i]
        if m.sum() < 40:
            continue
        stamps.append(int(idx[i]))
        f = F[i]
        # noise score: rank of vol + rank of turnover (dollar volume when market cap is missing)
        t_ = np.where(np.isfinite(turn[i]), turn[i], np.nan)
        r1 = pd.Series(rv[i]).where(m).rank(pct=True)
        r2 = pd.Series(t_).where(m).rank(pct=True)
        r2 = r2.fillna(pd.Series(dv_log[i]).where(m).rank(pct=True))
        noise = ((r1 + r2) / 2).to_numpy()
        q_lo, q_hi = np.nanquantile(noise, 0.1), np.nanquantile(noise, 0.9)
        ql, qs = m & (noise <= q_lo), m & (noise >= q_hi)
        long_r = np.nanmean(f[ql]) - COST if ql.any() else np.nan
        short_r = -np.nanmean(f[qs]) - COST if qs.any() else np.nan
        out["quiet_long"].append(long_r)
        out["noisy_short"].append(short_r)
        out["quiet_vs_noisy"].append(np.nanmean([long_r, short_r]))
        lv = np.where(m, lev[i], np.nan)
        if np.isfinite(lv).sum() >= 20:
            sel = lv >= np.nanquantile(lv, 0.95)
            out["leverage_long"].append(np.nanmean(f[sel]) - COST)
        else:
            out["leverage_long"].append(np.nan)
        out["universe"].append(np.nanmean(f[m]))
        # same idea without market cap (which only exists for coins listed today): OI / volume, all coins
        od = np.where(m, oidv[i], np.nan)
        if np.isfinite(od).sum() >= 20:
            sel = od >= np.nanquantile(od, 0.95)
            out["oidv_long"].append(np.nanmean(f[sel]) - COST)
        else:
            out["oidv_long"].append(np.nan)
        # share of today's universe that has a market cap (delisted coins do not)
        out["leverage_long_all"].append(float(np.isfinite(lv).sum() / max(1, m.sum())))
        top = np.argsort(np.where(m, rv[i], -np.inf))[-10:]
        for x, key in ((0.05, "breakout_5"), (0.08, "breakout_8")):
            rs = []
            for j in top:
                o = C[i, j]
                if not np.isfinite(o):
                    continue
                side, entry = 0, 0.0
                ret = None
                for h in range(1, H + 1):
                    up_hit, dn_hit = HI[i + h, j] >= o * (1 + x), LO[i + h, j] <= o * (1 - x)
                    if side == 0:
                        if up_hit and dn_hit:
                            ret = -x
                            break
                        if up_hit:
                            side, entry = 1, o * (1 + x)
                        elif dn_hit:
                            side, entry = -1, o * (1 - x)
                        if side == 0:
                            continue
                        # same hour: stop checked from the next hour on
                        continue
                    if (side > 0 and LO[i + h, j] <= o) or (side < 0 and HI[i + h, j] >= o):
                        ret = side * (o / entry - 1)
                        break
                if side == 0 and ret is None:
                    continue                                   # never triggered: no trade
                if ret is None:
                    ret = side * (C[i + H, j] / entry - 1)
                rs.append(ret - COST)
            out[key].append(np.mean(rs) if rs else np.nan)
    half = len(stamps) // 2
    res: dict[str, Any] = {"days": len(stamps), "from": stamps[0] if stamps else None, "split": stamps[half] if stamps else None}
    for k, v in out.items():
        a = np.asarray(v, dtype=float)
        row = {}
        for part, sl in (("all", slice(None)), ("h1", slice(0, half)), ("h2", slice(half, None))):
            x = a[sl]
            x = x[np.isfinite(x)]
            if len(x) < 3:
                continue
            eq = np.cumprod(1 + x)
            row[part] = {"days": int(len(x)), "mean": float(x.mean()), "t": float(x.mean() / x.std(ddof=1) * math.sqrt(len(x))),
                         "win": float((x > 0).mean()), "total": float(eq[-1] - 1),
                         "max_dd": float((eq / np.maximum.accumulate(eq) - 1).min())}
        res[k] = row
    return res


def run(storage: Any, days: int = 185, log: Callable[[str], None] = lambda _m: None) -> dict[str, Any]:
    t0 = time.time()
    p = load(storage, days)
    s = signals(p)
    feats = list(s)
    df = table(p, s)
    log(f"table {df.shape}, {df['symbol'].nunique()} coins, {df['day'].nunique()} days, "
        f"up10 {df['up10'].mean():.3f} dn10 {df['dn10'].mean():.3f} in {time.time() - t0:.0f}s")
    uni = univariate(df, feats)
    log("univariate done")
    cor = correlations(df, feats)
    size = by_size(df, [u["name"] for u in uni[:20]])
    inter = interactions(df)
    log("size/interactions done")
    bt = backtests(p, s)
    log("backtests done")
    mdl = model(df[df["ts"] % (4 * HOUR) == 0], feats, log)
    return {"run_at": int(time.time()), "elapsed_s": round(time.time() - t0, 1), "rows": int(len(df)),
            "coins": int(df["symbol"].nunique()), "days": int(df["day"].nunique()),
            "from": int(df["ts"].min()), "to": int(df["ts"].max()),
            "base_up10": float(df["up10"].mean()), "base_dn10": float(df["dn10"].mean()),
            "catalog": {k: {"category": v[0], "ko": v[1]} for k, v in TEXT.items()},
            "univariate": uni, "correlation": cor, "by_size": size, "interactions": inter, "model": mdl,
            "backtests": bt}
