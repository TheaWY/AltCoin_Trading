"""Pump precursors: what did the market look like in the minutes BEFORE a
real pump started?

Pump onset: minute t where the coin's high over the next PUMP_WINDOW minutes
is >= PUMP_MIN above close_t, and it was NOT already pumping (return over the
previous 15 min < PUMP_MIN / 3). One onset per symbol per 2h.

For each onset we snapshot precursor features at t (bars <= t only) and
compare them with every other tradeable (symbol, minute) -- the base rate.
For each feature: lift of the top decile (P(onset | top decile) / P(onset)),
rank AUC, and a train/test split so a precursor only counts if it holds on
both halves of time.

Literature these precursors come from:
  * volume/price anomaly vs a trailing average (Kamps & Kleinberg 2018)
  * taker buy/sell imbalance as order-flow imbalance (Cont, Kukanov & Stoikov)
  * volatility compression before breakouts, breakout from a recent high
  * trade-count and average-trade-size surges (large accounts entering)
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

PUMP_MIN = 0.08
PUMP_WINDOW = 60
ONSET_COOLDOWN = 120
MIN_DOLLAR_VOL = 100_000.0
SAMPLE_EVERY = 5          # thin the base-rate sample to every 5th minute

FEATURE_TEXT = {
    "vol_ratio_5": "5m quote volume vs 4h per-minute average",
    "vol_ratio_15": "15m quote volume vs 4h per-minute average",
    "taker_15": "taker-buy share of volume, last 15m",
    "taker_60": "taker-buy share of volume, last 60m",
    "ret_15": "return over the last 15m",
    "ret_60": "return over the last 60m",
    "vol_compress": "1h realised vol / 24h realised vol (low = compressed)",
    "hi_break": "close vs the 24h high (0 = at the high)",
    "trades_ratio_15": "15m trade count vs 4h average",
    "avg_trade_ratio": "15m average trade size vs 4h average",
    "green_run": "consecutive rising 1m closes",
}


def precursor_features(p: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    c, qv, tb = p["close"], p["quote_volume"], p["taker_buy_quote"]
    tr = p.get("trades")
    lr = np.log(c / c.shift(1))
    base_qv = qv.rolling(240, min_periods=120).mean()
    out = {
        "vol_ratio_5": qv.rolling(5).sum() / (base_qv.shift(5) * 5),
        "vol_ratio_15": qv.rolling(15).sum() / (base_qv.shift(15) * 15),
        "taker_15": tb.rolling(15).sum() / qv.rolling(15).sum(),
        "taker_60": tb.rolling(60).sum() / qv.rolling(60).sum(),
        "ret_15": np.log(c / c.shift(15)),
        "ret_60": np.log(c / c.shift(60)),
        "vol_compress": lr.rolling(60, min_periods=30).std() / lr.rolling(1440, min_periods=600).std(),
        "hi_break": np.log(c / p["high"].rolling(1440, min_periods=600).max()),
    }
    out["green_run"] = _run_length((lr > 0).astype(float))
    if tr is not None and not tr.empty:
        base_tr = tr.rolling(240, min_periods=120).mean()
        out["trades_ratio_15"] = tr.rolling(15).sum() / (base_tr.shift(15) * 15)
        avg_sz = qv / tr.replace(0, np.nan)
        out["avg_trade_ratio"] = avg_sz.rolling(15).mean() / avg_sz.rolling(240, min_periods=120).mean().shift(15)
    return {k: v.replace([np.inf, -np.inf], np.nan) for k, v in out.items()}


def _run_length(up: pd.DataFrame) -> pd.DataFrame:
    a = up.to_numpy()
    out = np.zeros_like(a)
    for i in range(1, len(a)):
        out[i] = np.where(a[i] > 0, out[i - 1] + 1, 0)
    return pd.DataFrame(out, index=up.index, columns=up.columns)


def onsets(p: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Boolean frame: True at pump onset minutes."""
    c, hi = p["close"], p["high"]
    fwd_hi = hi[::-1].rolling(PUMP_WINDOW, min_periods=PUMP_WINDOW // 2).max()[::-1].shift(-1)
    fwd = fwd_hi / c - 1
    # the move must actually START now: half of it inside the next 15 minutes,
    # otherwise "onset" lands long before anything happens
    fwd15 = hi[::-1].rolling(15, min_periods=10).max()[::-1].shift(-1) / c - 1
    prior = c / c.shift(15) - 1
    liquid = p["quote_volume"].rolling(60, min_periods=30).sum() >= MIN_DOLLAR_VOL
    raw = (fwd >= PUMP_MIN) & (fwd15 >= PUMP_MIN / 2) & (prior < PUMP_MIN / 3) & liquid
    arr = raw.to_numpy(dtype=bool)
    out = np.zeros_like(arr)
    for j in range(arr.shape[1]):
        last = -10 ** 9
        for i in np.flatnonzero(arr[:, j]):
            if i - last >= ONSET_COOLDOWN:
                out[i, j] = True
                last = i
    return pd.DataFrame(out, index=c.index, columns=c.columns)


def _auc(pos: np.ndarray, neg: np.ndarray) -> float | None:
    if len(pos) < 5 or len(neg) < 5:
        return None
    ranks = pd.Series(np.concatenate([pos, neg])).rank().to_numpy()
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def study(p: dict[str, pd.DataFrame], train_frac: float = 0.7) -> dict[str, Any]:
    feats = precursor_features(p)
    on = onsets(p)
    liquid = p["quote_volume"].rolling(60, min_periods=30).sum() >= MIN_DOLLAR_VOL
    n = len(on.index)
    split = int(n * train_frac)
    rows = []
    for name, f in feats.items():
        res: dict[str, Any] = {"feature": name, "text": FEATURE_TEXT.get(name, name)}
        for part, sl in (("train", slice(0, split)), ("test", slice(split, n))):
            fo, oo, lo = f.iloc[sl], on.iloc[sl], liquid.iloc[sl]
            pos = fo.to_numpy()[oo.to_numpy(dtype=bool)]
            base_mask = lo.to_numpy(dtype=bool) & ~oo.to_numpy(dtype=bool)
            base_mask &= (np.arange(len(base_mask)) % SAMPLE_EVERY == 0)[:, None]
            neg = fo.to_numpy()[base_mask]
            pos, neg = pos[np.isfinite(pos)], neg[np.isfinite(neg)]
            auc = _auc(pos, neg)
            lift = None
            if len(neg) > 100 and len(pos) >= 5:
                cut = np.quantile(neg, 0.9)
                p_top = (pos >= cut).mean()
                lift = float(p_top / 0.1)
            res[f"{part}_onsets"] = int(len(pos))
            res[f"{part}_auc"] = auc
            res[f"{part}_lift_top10"] = lift
            res[f"{part}_median_at_onset"] = float(np.median(pos)) if len(pos) else None
            res[f"{part}_median_base"] = float(np.median(neg)) if len(neg) else None
        a1, a2 = res.get("train_auc"), res.get("test_auc")
        res["holds"] = bool(a1 is not None and a2 is not None and
                            ((a1 > 0.55 and a2 > 0.55) or (a1 < 0.45 and a2 < 0.45)))
        rows.append(res)
    rows.sort(key=lambda r: -abs((r.get("test_auc") or 0.5) - 0.5))
    return {"onsets_total": int(on.to_numpy().sum()), "minutes": n, "symbols": int(on.shape[1]),
            "split_ts": int(on.index[split]) if n else None, "precursors": rows}
