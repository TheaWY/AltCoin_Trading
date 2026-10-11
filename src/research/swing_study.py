"""Swing study: days-to-weeks trades on daily bars, tested the same way as
the pump study (train/test split in time, costs, FDR), plus a portfolio
simulation against simply holding BTC.

Data: the hourly `prices` table (timeframe '1h', 2020 onward, ~300-500
coins) rolled up to UTC days. Entries are decided on a day's close and
filled at the next day's open; stops are checked on daily highs/lows with
the stop assumed to fill first (conservative).

Rule families (long and short):
  breakout   close above the prior N-day high (short: below the N-day low)
  pullback   uptrend (close > 100d MA) but RSI14 oversold (short: mirror)
  squeeze    volatility in its bottom 20% of the year, then a 20d breakout
  rs_break   20d breakout on a coin in the top decile of 30d strength vs BTC
optional filters: volume >= 2x its 20d average, BTC regime (BTC above or
below its 100d MA).

Exits: initial stop STOP_ATR x ATR14, trailing stop k x ATR from the peak,
time stop after H days. Costs: 0.3% round trip + 0.02%/day funding.
"""

from __future__ import annotations

import math
import pickle
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.research.sentiment_lab import bh_qvalues

DAY = 86400
ROUND_TRIP = 0.003
FUNDING_PER_DAY = 0.0002
STOP_ATR = 2.0
TRAILS = (2.0, 3.0, 4.0)
HOLDS = (10, 30)
MIN_DVOL = 2e6
MIN_AGE = 60
TRAIN_FRAC = 0.7
FDR_Q = 0.10
OOS_MIN_T = 1.5
MIN_TEST_TRADES = 30
CACHE = Path("data/cache/daily_panel.pkl")
STABLES = {"USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "USDE", "USD1", "EUR"}


# ---------------------------------------------------------------- data

DAILY_SQL = """
SELECT symbol, (timestamp / 86400) * 86400 AS d,
       (array_agg(open ORDER BY timestamp))[1] AS o, MAX(high) AS h, MIN(low) AS l,
       (array_agg(close ORDER BY timestamp DESC))[1] AS c,
       SUM(COALESCE(quote_volume, volume * close)) AS qv, COUNT(*) AS n
FROM prices WHERE timeframe = '1h' AND timestamp >= ?
GROUP BY 1, 2
"""


def load_daily(storage: Any, since_ts: int = 1577836800, max_age_s: int = 6 * 3600) -> dict[str, pd.DataFrame]:
    """Wide daily frames (index = UTC day start, columns = symbols).
    Cached on disk; days with fewer than 20 hourly bars are dropped."""
    if CACHE.exists() and time.time() - CACHE.stat().st_mtime < max_age_s:
        with CACHE.open("rb") as fh:
            return pickle.load(fh)
    with storage._connect() as c:  # noqa: SLF001
        rows = c.execute(DAILY_SQL, (since_ts,)).fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    df = df[df["n"] >= 20]
    out = {}
    for col, name in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"), ("qv", "qv")):
        out[name] = df.pivot(index="d", columns="symbol", values=col).sort_index().astype("float64")
    idx = pd.RangeIndex(int(out["close"].index.min()), int(out["close"].index.max()) + DAY, DAY)
    out = {k: v.reindex(idx) for k, v in out.items()}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with CACHE.open("wb") as fh:
        pickle.dump(out, fh)
    return out


# ---------------------------------------------------------------- features

def _rsi(close: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def features(p: dict[str, pd.DataFrame], btc: str = "BTC/USDT") -> dict[str, pd.DataFrame]:
    c, h, lo, qv = p["close"], p["high"], p["low"], p["qv"]
    prev = c.shift(1)
    tr = pd.concat([h - lo, (h - prev).abs(), (lo - prev).abs()]).groupby(level=0).max()
    f: dict[str, Any] = {}
    f["atr"] = (tr.rolling(14, min_periods=10).mean() / c)
    f["dvol30"] = qv.rolling(30, min_periods=20).mean()
    f["age"] = c.notna().cumsum()
    for n in (20, 55):
        f[f"hi{n}"] = h.shift(1).rolling(n, min_periods=n).max()
        f[f"lo{n}"] = lo.shift(1).rolling(n, min_periods=n).min()
    f["ma100"] = c.rolling(100, min_periods=100).mean()
    f["rsi"] = _rsi(c)
    f["vol_ratio"] = qv / qv.shift(1).rolling(20, min_periods=15).mean()
    lr = np.log(c / prev)
    rv = lr.rolling(20, min_periods=15).std()
    f["rv_pct"] = rv.rolling(250, min_periods=120).rank(pct=True)
    ret30 = c / c.shift(30) - 1
    b = c[btc] if btc in c else c.mean(axis=1)
    f["rs30_rank"] = ret30.sub(b / b.shift(30) - 1, axis=0).rank(axis=1, pct=True)
    bma = b.rolling(100, min_periods=100).mean()
    f["btc_up"] = pd.DataFrame(np.repeat((b > bma).to_numpy()[:, None], c.shape[1], axis=1),
                               index=c.index, columns=c.columns)
    stable = [s for s in c.columns if s.split("/")[0] in STABLES]
    f["tradable"] = (f["dvol30"] >= MIN_DVOL) & (f["age"] >= MIN_AGE) & f["atr"].notna()
    f["tradable"][stable] = False
    return f


# ---------------------------------------------------------------- rules

def entry_rules() -> list[dict[str, Any]]:
    out = []
    for n in (20, 55):
        for vol in (False, True):
            for reg in (False, True):
                out.append({"name": f"L_break{n}{'_vol' if vol else ''}{'_btcup' if reg else ''}",
                            "side": 1, "kind": "breakout", "n": n, "vol": vol, "reg": reg})
                out.append({"name": f"S_break{n}{'_vol' if vol else ''}{'_btcdn' if reg else ''}",
                            "side": -1, "kind": "breakout", "n": n, "vol": vol, "reg": reg})
    for lvl in (30, 40):
        for reg in (False, True):
            out.append({"name": f"L_pull{lvl}{'_btcup' if reg else ''}", "side": 1, "kind": "pullback",
                        "lvl": lvl, "reg": reg})
            out.append({"name": f"S_rally{100 - lvl}{'_btcdn' if reg else ''}", "side": -1, "kind": "pullback",
                        "lvl": 100 - lvl, "reg": reg})
    for reg in (False, True):
        out.append({"name": f"L_squeeze{'_btcup' if reg else ''}", "side": 1, "kind": "squeeze", "reg": reg})
        out.append({"name": f"L_rsbreak{'_btcup' if reg else ''}", "side": 1, "kind": "rs_break", "reg": reg})
        out.append({"name": f"S_rsbreak{'_btcdn' if reg else ''}", "side": -1, "kind": "rs_break", "reg": reg})
    return out


def entry_mask(p: dict[str, pd.DataFrame], f: dict[str, pd.DataFrame], r: dict[str, Any]) -> pd.DataFrame:
    c, side = p["close"], r["side"]
    if r["kind"] == "breakout":
        m = (c > f[f"hi{r['n']}"]) if side > 0 else (c < f[f"lo{r['n']}"])
        if r["vol"]:
            m &= f["vol_ratio"] >= 2.0
    elif r["kind"] == "pullback":
        m = ((c > f["ma100"]) & (f["rsi"] < r["lvl"])) if side > 0 else ((c < f["ma100"]) & (f["rsi"] > r["lvl"]))
    elif r["kind"] == "squeeze":
        m = (f["rv_pct"] <= 0.2) & (c > f["hi20"])
    else:  # rs_break
        m = ((f["rs30_rank"] >= 0.9) & (c > f["hi20"])) if side > 0 else ((f["rs30_rank"] <= 0.1) & (c < f["lo20"]))
    if r["reg"]:
        m &= f["btc_up"] if side > 0 else ~f["btc_up"]
    return m & f["tradable"]


# ---------------------------------------------------------------- simulation

def simulate(o, h, lo, c, i: int, side: int, atr: float, trail: float, hold: int,
             record: bool = False) -> tuple[float, int, int, list[float]]:
    """One trade on one symbol's arrays. Signal on day i, fill at open i+1.
    Returns (net return, exit day index, days held, daily returns if record)."""
    n = len(c)
    j0 = i + 1
    if j0 >= n or not np.isfinite(o[j0]) or o[j0] <= 0:
        return float("nan"), i, 0, []
    entry = o[j0]
    a = atr * entry
    stop = entry - side * STOP_ATR * a
    peak = entry
    last = entry
    daily: list[float] = []
    exit_px, j = None, j0
    for j in range(j0, min(n, j0 + hold)):
        if not np.isfinite(c[j]):
            exit_px = last
            break
        hit = lo[j] <= stop if side > 0 else h[j] >= stop
        if hit:
            gap = o[j] < stop if side > 0 else o[j] > stop
            exit_px = o[j] if (gap and j > j0) else stop
            if record:
                daily.append(side * (exit_px / last - 1))
            break
        peak = max(peak, h[j]) if side > 0 else min(peak, lo[j])
        stop = max(stop, peak - trail * a) if side > 0 else min(stop, peak + trail * a)
        if record:
            daily.append(side * (c[j] / last - 1))
        last = c[j]
    if exit_px is None:
        exit_px = last
    days = j - j0 + 1
    gross = side * (exit_px / entry - 1)
    return gross - ROUND_TRIP - FUNDING_PER_DAY * days, j, days, daily


def trades_for(p, f, r, trail: float, hold: int, record: bool = False) -> list[dict[str, Any]]:
    mask = entry_mask(p, f, r).to_numpy(dtype=bool)
    o, h, lo, c = (p[k].to_numpy() for k in ("open", "high", "low", "close"))
    atr = f["atr"].to_numpy()
    days = p["close"].index.to_numpy()
    cols = list(p["close"].columns)
    out = []
    for k in np.flatnonzero(mask.any(axis=0)):
        free_from = -1
        for i in np.flatnonzero(mask[:, k]):
            if i <= free_from:
                continue
            net, j, held, daily = simulate(o[:, k], h[:, k], lo[:, k], c[:, k], int(i), r["side"],
                                           float(atr[i, k]), trail, hold, record)
            if not np.isfinite(net):
                continue
            free_from = j
            t = {"sym": cols[k], "day": int(days[i]), "net": net, "held": held}
            if record:
                t["entry_idx"] = int(i) + 1
                t["daily"] = daily
            out.append(t)
    return out


def _weekly_t(days: np.ndarray, x: np.ndarray) -> tuple[float, float, int]:
    if len(x) == 0:
        return 0.0, 0.0, 0
    s = pd.Series(x).groupby(days // (7 * DAY)).mean()
    n = len(s)
    if n < 3 or s.std(ddof=1) == 0:
        return float(s.mean()), 0.0, n
    return float(s.mean()), float(s.mean() / s.std(ddof=1) * math.sqrt(n)), n


def _p(t: float) -> float:
    return math.erfc(abs(t) / math.sqrt(2.0))


def run_study(p: dict[str, pd.DataFrame], log=lambda _m: None) -> dict[str, Any]:
    f = features(p)
    idx = p["close"].index
    split = int(idx[int(len(idx) * TRAIN_FRAC)])
    results = []
    for r in entry_rules():
        for trail in TRAILS:
            for hold in HOLDS:
                tr = trades_for(p, f, r, trail, hold)
                if not tr:
                    continue
                d = np.array([t["day"] for t in tr])
                x = np.array([t["net"] for t in tr])
                held = np.array([t["held"] for t in tr])
                a, b = d < split, d >= split
                m_tr, t_tr, w_tr = _weekly_t(d[a], x[a])
                m_te, t_te, w_te = _weekly_t(d[b], x[b])
                years = {}
                for y, g in pd.Series(x).groupby(pd.to_datetime(d, unit="s").year):
                    years[int(y)] = [round(float(g.mean()), 5), int(len(g))]
                results.append({
                    "rule": f"{r['name']}|trail{trail:g}|hold{hold}", "entry": r, "trail": trail, "hold": hold,
                    "trades": len(tr), "train_trades": int(a.sum()), "test_trades": int(b.sum()),
                    "train_mean_net": m_tr, "train_t": t_tr, "train_weeks": w_tr,
                    "test_mean_net": m_te, "test_t": t_te, "test_weeks": w_te,
                    "win_rate": float((x > 0).mean()), "test_win_rate": float((x[b] > 0).mean()) if b.any() else None,
                    "median_hold_days": float(np.median(held)), "by_year": years,
                    "p_train": _p(t_tr) if m_tr > 0 else 1.0,
                })
        log(f"{r['name']}: done")
    q = bh_qvalues([x["p_train"] for x in results]) if results else []
    for x, qq in zip(results, q):
        x["q"] = qq
        x["validated"] = bool(qq < FDR_Q and x["train_mean_net"] > 0 and x["test_trades"] >= MIN_TEST_TRADES
                              and x["test_mean_net"] > 0 and x["test_t"] >= OOS_MIN_T)
    def rank(x):
        enough = x["train_trades"] >= 60 and x["test_trades"] >= MIN_TEST_TRADES
        return (not x["validated"], not enough, -(x["test_mean_net"] if x["train_mean_net"] > 0 else -1))

    results.sort(key=rank)
    return {"days": len(idx), "symbols": int(p["close"].shape[1]), "from": int(idx[0]), "to": int(idx[-1]),
            "split_ts": split, "rules_tested": len(results), "results": results, "features": f}


# ---------------------------------------------------------------- portfolio

def portfolio(p, f, rule: dict[str, Any], slot: float = 0.10, max_pos: int = 10,
              btc: str = "BTC/USDT") -> dict[str, Any]:
    """Fixed-fraction book: every signal takes `slot` of equity while fewer
    than `max_pos` trades are open. Daily returns vs holding BTC."""
    tr = trades_for(p, f, rule["entry"], rule["trail"], rule["hold"], record=True)
    tr.sort(key=lambda t: (t["entry_idx"], t["sym"]))  # chronological; ties by name, never by outcome
    n = len(p["close"].index)
    ret = np.zeros(n)
    busy = np.zeros(n, dtype=int)
    taken = 0
    for t in tr:
        s = t["entry_idx"]
        span = range(s, min(n, s + len(t["daily"])))
        if s >= n or busy[s] >= max_pos:  # only what is known on the entry day
            continue
        taken += 1
        for off, k in enumerate(span):
            busy[k] += 1
            ret[k] += slot * (t["daily"][off] - FUNDING_PER_DAY)
        ret[s] -= slot * ROUND_TRIP
    b = p["close"][btc].pct_change().fillna(0).to_numpy() if btc in p["close"] else np.zeros(n)
    split_i = int(n * TRAIN_FRAC)

    def stats(x: np.ndarray) -> dict[str, float]:
        eq = np.cumprod(1 + x)
        dd = float((eq / np.maximum.accumulate(eq) - 1).min()) if len(eq) else 0.0
        sd = x.std(ddof=1)
        return {"total": float(eq[-1] - 1) if len(eq) else 0.0,
                "cagr": float(eq[-1] ** (365 / max(1, len(x))) - 1) if len(eq) else 0.0,
                "sharpe": float(x.mean() / sd * math.sqrt(365)) if sd > 0 else 0.0, "max_dd": dd}

    idx = p["close"].index
    return {"trades_taken": taken, "signals": len(tr), "avg_open": float(busy.mean()),
            "full": stats(ret), "test": stats(ret[split_i:]),
            "btc_full": stats(b), "btc_test": stats(b[split_i:]),
            "test_from": int(idx[split_i]),
            "equity": [[int(idx[k]), float(v)] for k, v in enumerate(np.cumprod(1 + ret)) if k % 7 == 0],
            "btc_equity": [[int(idx[k]), float(v)] for k, v in enumerate(np.cumprod(1 + b)) if k % 7 == 0]}


# ---------------------------------------------------------------- books
# Rules above judge single trades. Swing edges in crypto are usually about
# *what to hold this week* (trend, momentum), so books are tested directly
# as daily return streams against holding BTC.

SWITCH_COST = ROUND_TRIP / 2  # per unit of turnover, one side


def _stats(x: np.ndarray) -> dict[str, float]:
    if len(x) < 2:
        return {"total": 0.0, "cagr": 0.0, "sharpe": 0.0, "max_dd": 0.0, "t": 0.0}
    eq = np.cumprod(1 + x)
    sd = x.std(ddof=1)
    sr = float(x.mean() / sd * math.sqrt(365)) if sd > 0 else 0.0
    return {"total": float(eq[-1] - 1), "cagr": float(eq[-1] ** (365 / len(x)) - 1),
            "sharpe": sr, "max_dd": float((eq / np.maximum.accumulate(eq) - 1).min()),
            "t": float(x.mean() / sd * math.sqrt(len(x))) if sd > 0 else 0.0}


def _run_weights(w: pd.DataFrame, rets: pd.DataFrame, funding: bool = True) -> np.ndarray:
    """Weights decided at day t's close earn day t+1's return; turnover pays
    trading costs and open exposure pays perp funding (the BTC-hold benchmark
    is spot, so it pays neither)."""
    w = w.fillna(0.0)
    held = w.shift(1).fillna(0.0)
    gross = (held * rets.fillna(0.0)).sum(axis=1)
    turn = w.diff().abs().sum(axis=1)
    turn.iloc[0] = w.iloc[0].abs().sum()  # the first day's entry is turnover too
    turn = turn.shift(1).fillna(0.0)
    cost = turn * SWITCH_COST + (held.abs().sum(axis=1) * FUNDING_PER_DAY if funding else 0.0)
    return (gross - cost).to_numpy()


def book_weights(p, f, btc: str = "BTC/USDT") -> dict[str, pd.DataFrame]:
    c = p["close"]
    out: dict[str, pd.DataFrame] = {}
    zero = pd.DataFrame(0.0, index=c.index, columns=c.columns)
    b = c[btc]
    for m in (20, 30, 50, 75, 100, 150, 200):
        w = zero.copy()
        w[btc] = (b > b.rolling(m, min_periods=m).mean()).astype(float)
        out[f"btc_trend_ma{m}"] = w
    for m in (50, 100):
        w = zero.copy()
        w[btc] = np.where(b > b.rolling(m, min_periods=m).mean(), 1.0, -1.0)
        w.loc[b.rolling(m, min_periods=m).mean().isna(), btc] = 0.0
        out[f"btc_longshort_ma{m}"] = w
    majors = [s for s in ("BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT") if s in c]
    for m in (50, 100):
        up = pd.DataFrame({s: (c[s] > c[s].rolling(m, min_periods=m).mean()).astype(float) for s in majors})
        w = zero.copy()
        w[majors] = up / len(majors)
        out[f"majors_trend_ma{m}"] = w
        if "ETH/USDT" in c:
            be = zero.copy()
            for s in (btc, "ETH/USDT"):
                be[s] = (c[s] > c[s].rolling(m, min_periods=m).mean()).astype(float) / 2
            out[f"btc_eth_trend_ma{m}"] = be
            e = zero.copy()
            e["ETH/USDT"] = (c["ETH/USDT"] > c["ETH/USDT"].rolling(m, min_periods=m).mean()).astype(float)
            out[f"eth_trend_ma{m}"] = e
    week = (np.arange(len(c)) % 7 == 0)
    liquid_rank = f["dvol30"].where(f["tradable"]).rank(axis=1, ascending=False)
    universe = liquid_rank <= 100
    for lb in (7, 14, 30, 60):
        mom = (c / c.shift(lb) - 1).where(universe)
        top = mom.rank(axis=1, ascending=False) <= 10
        base = top.astype(float).div(top.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
        base.loc[~week] = np.nan
        base = base.ffill().fillna(0.0)
        out[f"xs_mom{lb}_top10"] = base
        out[f"xs_mom{lb}_top10_btcup"] = base.mul(f["btc_up"].iloc[:, 0].astype(float), axis=0)
    # Same idea as majors_trend without hindsight: the "majors" are whatever
    # were the most liquid coins at the time (top K by 30d dollar volume).
    for k in (3, 5, 10):
        for m in (50, 100):
            top = (liquid_rank <= k) & (c > c.rolling(m, min_periods=m).mean())
            wt = top.astype(float) / k
            wt.loc[~week] = np.nan
            out[f"top{k}liq_trend_ma{m}"] = wt.ffill().fillna(0.0)
    for m in (20, 50):
        above = (c > c.rolling(m, min_periods=m).mean()) & universe & (liquid_rank <= 50)
        wt = above.astype(float).div(above.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
        wt.loc[~week] = np.nan
        out[f"alt_trend_ma{m}_top50"] = wt.ffill().fillna(0.0)
    hold = zero.copy()
    hold[btc] = 1.0
    out["btc_hold"] = hold
    return out


def run_books(p, f, btc: str = "BTC/USDT") -> dict[str, Any]:
    rets = p["close"].pct_change(fill_method=None).clip(-0.9, 5.0)
    n = len(rets)
    split_i = int(n * TRAIN_FRAC)
    res = {}
    for name, w in book_weights(p, f, btc).items():
        x = np.nan_to_num(_run_weights(w, rets, funding=name != "btc_hold"))
        res[name] = {"train": _stats(x[:split_i]), "test": _stats(x[split_i:]), "full": _stats(x),
                     "exposure": float(w.abs().sum(axis=1).mean()),
                     "equity": [[int(rets.index[k]), float(v)] for k, v in enumerate(np.cumprod(1 + x)) if k % 7 == 0]}
    bt = res["btc_hold"]
    for name, r in res.items():
        r["beats_btc_test"] = bool(r["test"]["total"] > bt["test"]["total"] and r["test"]["sharpe"] > bt["test"]["sharpe"])
        r["beats_btc_train"] = bool(r["train"]["total"] > bt["train"]["total"] and r["train"]["sharpe"] > bt["train"]["sharpe"])
    return {"split_ts": int(rets.index[split_i]), "books": res}
