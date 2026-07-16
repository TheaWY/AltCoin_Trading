"""Phase-3 discovery engine (playbook step 7-9). Auto-generates candidate
signals over the feature set, event-studies each market-neutralized, and applies
the multiple-comparison correction that keeps flukes out of the book.

The discipline the playbook demands, in code:
  - COUNT EVERY TEST. That number is the denominator for Bonferroni + BH.
  - Bonferroni survivors are FINDINGS; BH survivors are candidates.
  - DIRECTION AUDIT: every survivor's inverse is tested too -- an anti-predictive
    signal (short weakness, the trap we hit twice) must be caught here.
  - Holdout is sacred: search runs on data < RESEARCH_HOLDOUT_START only.
  - Discovery is FREE (event studies, no trial budget); PROMOTION is earned.

Features are computed VECTORIZED per symbol (not via features.compute_features
per-bar, which is far too slow for a full-history sweep) but mirror the same
definitions. Each candidate is "feature in top/bottom decile of its own trailing
window" -> forward market-neutral return; the engine returns a funnel:
tested -> BH -> direction-audited -> (Bonferroni findings).
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any, Callable

import numpy as np

HOUR = 3600
DAY = 86400
_COST = 0.0032          # market-neutral round-trip
_TRAIL = 2 * 365 * 24   # trailing window for the decile threshold
_MIN_HISTORY = 720
_HORIZON_H = 72         # forward horizon (the effect grew to 72h)


# --------------------------------------------------------------------------
# vectorized feature extractors: symbol arrays -> feature series (same length)
# --------------------------------------------------------------------------
def _feature_series(ts: np.ndarray, o: np.ndarray, h: np.ndarray,
                    l: np.ndarray, c: np.ndarray, v: np.ndarray) -> dict[str, np.ndarray]:
    n = len(c)
    def roll_ret(w):
        r = np.full(n, np.nan); r[w:] = (c[w:] - c[:-w]) / c[:-w]; return r
    rng = np.where(h - l > 0, h - l, np.nan)
    logret = np.full(n, np.nan)
    logret[1:] = np.where((c[1:] > 0) & (c[:-1] > 0), np.log(c[1:] / c[:-1]), np.nan)
    def roll_std(a, w):
        out = np.full(n, np.nan)
        for i in range(w, n):
            s = a[i - w:i]; s = s[np.isfinite(s)]
            if len(s) >= w // 2: out[i] = s.std()
        return out
    feats = {
        "ret_24h": roll_ret(24), "ret_72h": roll_ret(72), "ret_7d": roll_ret(168),
        "ret_30d": roll_ret(720),
        "rvol_24h": roll_std(logret, 24), "rvol_7d": roll_std(logret, 168),
        "close_pos_in_range": (c - l) / rng,
        "body_pct": np.abs(c - o) / rng,
        "range_pct": rng / np.where(c > 0, c, np.nan) * 100.0,
        "dollar_vol": c * v,
    }
    # volume z (30d)
    dv = c * v
    dvz = np.full(n, np.nan)
    for i in range(48, n):
        w = dv[max(0, i - 720):i]; w = w[np.isfinite(w)]
        if len(w) >= 48 and w.std() > 0: dvz[i] = (dv[i] - w.mean()) / w.std()
    feats["dollar_vol_zscore"] = dvz
    return feats


def _neutral_fwd(c: np.ndarray, ts: np.ndarray, bmap: dict[int, float],
                 i: int, h: int) -> float | None:
    n = len(c)
    if i + h >= n:
        return None
    raw = (c[i + h] - c[i]) / c[i]
    a = bmap.get((int(ts[i]) // HOUR) * HOUR)
    b = bmap.get((int(ts[i]) + h * HOUR) // HOUR * HOUR)
    if not (a and b):
        return None
    return raw - (b - a) / a          # beta=1 market-neutralization (fast; hedge is BTC)


def _bootstrap_p(vals: list[float], seed: int = 7) -> float:
    """Two-sided bootstrap p that the mean is zero."""
    if len(vals) < 20:
        return 1.0
    rng = np.random.RandomState(seed)
    arr = np.array(vals)
    means = arr[rng.randint(0, len(arr), (400, len(arr)))].mean(axis=1)
    frac_le0 = float((means <= 0).mean())
    return 2 * min(frac_le0, 1 - frac_le0)


def _windows(holdout_start: str, count: int = 40) -> list[tuple[float, float]]:
    end = dt.datetime.strptime(holdout_start, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
    return [(end - i * 60 * DAY, end - (i - 1) * 60 * DAY) for i in range(count, 0, -1)]


def run_discovery(
    storage: Any,
    symbols: list[str],
    holdout_start: str = "2026-06-01",
    horizon_h: int = _HORIZON_H,
    log: Callable[[str], None] = lambda m: None,
) -> dict[str, Any]:
    """Sweep every feature's high/low-decile extreme, event-study neutralized
    forward returns on data BEFORE the holdout, correct for the test count, and
    direction-audit survivors. Returns the funnel + surviving candidates."""
    btc = storage.get_prices("BTC/USDT", limit=200000, timeframe="1h")
    bmap = {(int(r["timestamp"]) // HOUR) * HOUR: float(r["close"]) for r in btc}
    holdout_ts = dt.datetime.strptime(holdout_start, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
    wins = _windows(holdout_start)

    def win_of(t):
        for wi, (a, b) in enumerate(wins):
            if a <= t < b:
                return wi
        return None

    # candidate = (feature, direction 'high'/'low'); accumulate neutralized fwd
    # returns + per-window bucket across the whole universe.
    acc: dict[tuple[str, str], dict[str, Any]] = {}
    for si, sym in enumerate(symbols):
        rows = storage.get_prices(sym, limit=200000, timeframe="1h")
        if len(rows) < _MIN_HISTORY + horizon_h + 48:
            continue
        ts = np.array([int(r["timestamp"]) for r in rows])
        o = np.array([float(r["open"]) for r in rows]); h = np.array([float(r["high"]) for r in rows])
        l = np.array([float(r["low"]) for r in rows]); c = np.array([float(r["close"]) for r in rows])
        v = np.array([float(r.get("volume") or 0.0) for r in rows])
        feats = _feature_series(ts, o, h, l, c, v)
        n = len(c)
        GRID, SCAN = 72, 6      # recompute decile threshold every 3d; scan events every 6h
        for fname, series in feats.items():
            # trailing-decile thresholds on a coarse grid, forward-filled -- the
            # threshold barely moves bar-to-bar, so this is a ~12x speedup over a
            # per-bar percentile with negligible PIT loss (still uses only <t data).
            hi_thr = np.full(n, np.nan); lo_thr = np.full(n, np.nan)
            last_hi = last_lo = np.nan
            for g in range(_MIN_HISTORY, n, GRID):
                hist = series[max(0, g - _TRAIL):g]; hist = hist[np.isfinite(hist)]
                if len(hist) >= _MIN_HISTORY:
                    last_hi = np.percentile(hist, 90); last_lo = np.percentile(hist, 10)
                hi_thr[g:g + GRID] = last_hi; lo_thr[g:g + GRID] = last_lo
            for i in range(_MIN_HISTORY, n, SCAN):
                if ts[i] >= holdout_ts:            # HOLDOUT is sacred
                    continue
                val = series[i]
                if not (np.isfinite(val) and np.isfinite(hi_thr[i])):
                    continue
                which = "high" if val >= hi_thr[i] else ("low" if val <= lo_thr[i] else None)
                if which is None:
                    continue
                nf = _neutral_fwd(c, ts, bmap, i, horizon_h)
                if nf is None:
                    continue
                nf -= _COST
                a = acc.setdefault((fname, which), {"ret": [], "win": {}})
                a["ret"].append(nf)
                wi = win_of(int(ts[i]))
                if wi is not None:
                    a["win"].setdefault(wi, []).append(nf)
        if (si + 1) % 25 == 0:
            log(f"  ...{si+1}/{len(symbols)} symbols")

    # score every candidate. The p is bootstrapped over per-WINDOW mean returns
    # (~40 roughly-independent 60-day blocks), NOT the pooled events -- the
    # events are heavily overlapping/correlated (clustered in time), so a pooled
    # bootstrap reports p=0 for everything (the same overlap trap that inflated
    # DSR earlier). Window-block bootstrap is the honest test.
    tested = []
    for (fname, which), a in acc.items():
        R = a["ret"]
        if len(R) < 100:
            continue
        mean = float(np.mean(R))
        wmeans = [float(np.mean(w)) for w in a["win"].values() if w]
        posw = sum(1 for w in a["win"].values() if sum(w) > 0)
        nw = len(a["win"])
        p = _bootstrap_p(wmeans)
        tested.append({"feature": fname, "dir": which, "n": len(R), "mean_pct": mean * 100,
                       "pos_windows": posw, "n_windows": nw, "p": p})

    N = len(tested)                      # THE denominator (every test counted)
    if N == 0:
        return {"tested": 0, "candidates": [], "findings": []}
    # Benjamini-Hochberg
    tested.sort(key=lambda x: x["p"])
    bh = []
    for rank, t in enumerate(tested, 1):
        if t["p"] <= (rank / N) * 0.05:
            bh.append(t)
    bonf = [t for t in tested if t["p"] <= 0.05 / N]

    # direction audit: a real edge's INVERSE must NOT also look good. Flag any
    # BH survivor whose opposite-direction twin is also positive-mean (a sign the
    # "edge" is really a volatility artifact, not a directional signal).
    mean_by = {(t["feature"], t["dir"]): t["mean_pct"] for t in tested}
    for t in bh:
        opp = "low" if t["dir"] == "high" else "high"
        twin = mean_by.get((t["feature"], opp))
        t["direction_clean"] = (twin is None) or (twin < 0) or (t["mean_pct"] > 0 and twin < t["mean_pct"] / 2)
        # gate-relevant: window consistency
        t["clears_windows"] = t["n_windows"] >= 10 and (t["pos_windows"] / t["n_windows"]) >= 0.66

    audited = [t for t in bh if t.get("direction_clean")]
    findings = [t for t in bonf if t in bh]  # Bonferroni ∩ BH
    return {
        "tested": N, "bh": len(bh), "direction_audited": len(audited),
        "bonferroni_findings": len(findings),
        "candidates": sorted(audited, key=lambda x: -x["mean_pct"]),
        "findings": findings,
    }
