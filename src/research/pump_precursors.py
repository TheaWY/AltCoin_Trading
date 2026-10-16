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


def onsets(p: dict[str, pd.DataFrame], direction: int = 1) -> pd.DataFrame:
    """Boolean frame: True at pump (direction=1) or dump (direction=-1) onset minutes."""
    c = p["close"]
    if direction > 0:
        hi = p["high"]
        fwd = hi[::-1].rolling(PUMP_WINDOW, min_periods=PUMP_WINDOW // 2).max()[::-1].shift(-1) / c - 1
        # the move must actually START now: half of it inside the next 15 minutes,
        # otherwise "onset" lands long before anything happens
        fwd15 = hi[::-1].rolling(15, min_periods=10).max()[::-1].shift(-1) / c - 1
        prior = c / c.shift(15) - 1
    else:
        lo = p["low"]
        fwd = 1 - lo[::-1].rolling(PUMP_WINDOW, min_periods=PUMP_WINDOW // 2).min()[::-1].shift(-1) / c
        fwd15 = 1 - lo[::-1].rolling(15, min_periods=10).min()[::-1].shift(-1) / c
        prior = 1 - c / c.shift(15)
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
    res = study_stream(p, ((k, FEATURE_TEXT.get(k, k), v) for k, v in feats.items()), train_frac,
                       {"pump": onsets(p, 1)})
    return res["pump"]


def _side(f: np.ndarray, on: np.ndarray, base: np.ndarray, label: str, res: dict[str, Any]) -> None:
    pos = f[on]
    neg = f[base & ~on]
    pos, neg = pos[np.isfinite(pos)], neg[np.isfinite(neg)]
    res[f"{label}_onsets"] = int(len(pos))
    res[f"{label}_auc"] = _auc(pos, neg)
    lift_hi = lift_lo = None
    if len(neg) > 100 and len(pos) >= 5:
        hi, lo = np.quantile(neg, [0.9, 0.1])
        lift_hi = float((pos >= hi).mean() / 0.1)
        lift_lo = float((pos <= lo).mean() / 0.1)
    res[f"{label}_lift_top10"] = lift_hi
    res[f"{label}_lift_bottom10"] = lift_lo
    res[f"{label}_median_at_onset"] = float(np.median(pos)) if len(pos) else None
    res[f"{label}_median_base"] = float(np.median(neg)) if len(neg) else None


def study_stream(p: dict[str, pd.DataFrame], feats, train_frac: float = 0.7,
                 ons: dict[str, pd.DataFrame] | None = None) -> dict[str, dict[str, Any]]:
    """Precursor test for any stream of (name, text, frame) against one or
    more onset sets (e.g. pumps and dumps), one feature frame in memory at a
    time. A feature "holds" when its AUC is on the same side of 0.5 (beyond
    0.55 / 0.45) in both the train and the test part of time."""
    ons = ons or {"pump": onsets(p, 1)}
    liquid = (p["quote_volume"].rolling(60, min_periods=30).sum() >= MIN_DOLLAR_VOL).to_numpy(dtype=bool)
    n = liquid.shape[0]
    split = int(n * train_frac)
    sample = (np.arange(n) % SAMPLE_EVERY == 0)[:, None]
    base = liquid & sample
    on_np = {k: v.to_numpy(dtype=bool) for k, v in ons.items()}
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in ons}
    both = "pump" in on_np and "dump" in on_np
    if both:
        out["direction"] = []
    for name, text, f in feats:
        a = f.to_numpy(dtype="float32")
        if both:
            # given that a big move starts, which way? pos = pump onsets, neg = dump onsets
            res = {"feature": name, "text": text}
            for label, sl in (("train", slice(0, split)), ("test", slice(split, n))):
                pos, neg = a[sl][on_np["pump"][sl]], a[sl][on_np["dump"][sl]]
                pos, neg = pos[np.isfinite(pos)], neg[np.isfinite(neg)]
                res[f"{label}_auc"] = _auc(pos, neg)
                res[f"{label}_onsets"] = int(len(pos) + len(neg))
                res[f"{label}_median_at_onset"] = float(np.median(pos)) if len(pos) else None  # pumps
                res[f"{label}_median_base"] = float(np.median(neg)) if len(neg) else None      # dumps
                res[f"{label}_lift_top10"] = res[f"{label}_lift_bottom10"] = None
            a1, a2 = res.get("train_auc"), res.get("test_auc")
            res["holds"] = bool(a1 is not None and a2 is not None and
                                ((a1 > 0.55 and a2 > 0.55) or (a1 < 0.45 and a2 < 0.45)))
            out["direction"].append(res)
        for k, o in on_np.items():
            res: dict[str, Any] = {"feature": name, "text": text}
            _side(a[:split], o[:split], base[:split], "train", res)
            _side(a[split:], o[split:], base[split:], "test", res)
            a1, a2 = res.get("train_auc"), res.get("test_auc")
            res["holds"] = bool(a1 is not None and a2 is not None and
                                ((a1 > 0.55 and a2 > 0.55) or (a1 < 0.45 and a2 < 0.45)))
            out[k].append(res)
    result = {}
    for k, rows in out.items():
        rows.sort(key=lambda r: (not r["holds"], -min(abs((r.get("train_auc") or 0.5) - 0.5),
                                                          abs((r.get("test_auc") or 0.5) - 0.5))))
        total = int(on_np[k].sum()) if k in on_np else int(on_np["pump"].sum() + on_np["dump"].sum())
        result[k] = {"onsets_total": total, "minutes": n, "symbols": int(p["close"].shape[1]),
                     "split_ts": int(p["close"].index[split]) if n else None, "precursors": rows}
    return result
