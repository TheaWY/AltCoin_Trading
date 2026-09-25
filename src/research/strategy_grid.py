"""Strategy grid: the precursor findings turned into tradeable rules, swept
across look-back windows, thresholds, optional filters, holding times and
stops, then judged the way the other studies are (costs, train/test split in
time, hour-clustered t, BH FDR over every rule tried, plus a stability check
on the train halves and on neighbouring parameters).

Families
  squeeze_long  price flat but open interest rising (oi_chg_W - ret_W high),
                optional: leverage-heavy coin (oi/mcap high), futures takers
                net selling (taker ratio < 1)
  fade_short    coin just ran up (ret_W in its top tail), optional: longs
                piling in (oi_chg rising), high volatility, RSI overbought
  dip_long      coin just dropped (ret_W in its bottom tail), optional: high
                volatility, open interest falling (longs flushed)

Entry at the NEXT bar's open, exit at the close H minutes later or at the
stop (checked on bar highs/lows, gaps fill at the open). One position per
coin per rule at a time. Costs 0.3% round trip.
"""

from __future__ import annotations

import itertools
import math
from typing import Any, Callable

import numpy as np
import pandas as pd

from src.research import indicators as ind
from src.research.sentiment_lab import bh_qvalues

COST = 0.003
TRAIN_FRAC = 0.7
HOLDS = (15, 60, 240)
STOPS = (None, 0.02, 0.04)
MIN_DVOL_60 = 100_000.0
FDR_Q = 0.10
OOS_MIN_T = 1.5
MIN_TEST_TRADES = 30


class Frames:
    """Features the grid needs, computed once from an indicators.Ctx."""

    def __init__(self, ctx: ind.Ctx):
        self.ctx = ctx
        self.cache: dict[str, pd.DataFrame] = {}
        self.split = int(len(ctx.c.index) * TRAIN_FRAC)
        self.liquid = (ctx.qv_sum(60) >= MIN_DVOL_60).to_numpy(dtype=bool)

    def get(self, name: str) -> np.ndarray:
        if name not in self.cache:
            x = self.ctx
            if name.startswith("ret_"):
                f = x.ret(int(name[4:]))
            elif name.startswith("oidiv_"):
                w = int(name[6:])
                f = ind._oi_chg(x, w) - x.ret(w)  # noqa: SLF001
            elif name.startswith("oichg_"):
                f = ind._oi_chg(x, int(name[6:]))  # noqa: SLF001
            elif name == "oi_to_mcap":
                f = x.a("oi_usd") / ind._mcap(x)  # noqa: SLF001
            elif name == "taker_fut":
                f = x.a("taker_ratio")
            elif name == "rv_60":
                f = ind._rv(x, 60)  # noqa: SLF001
            elif name == "rsi_14":
                f = ind._rsi(x.c, 14)  # noqa: SLF001
            else:
                raise KeyError(name)
            self.cache[name] = ind._clean(f)  # noqa: SLF001
        return self.cache[name].to_numpy()

    def q(self, name: str, quant: float) -> float:
        """Threshold from the TRAIN part only (liquid, finite values)."""
        a = self.get(name)[: self.split]
        v = a[self.liquid[: self.split] & np.isfinite(a)]
        if len(v) > 2_000_000:
            v = np.random.default_rng(0).choice(v, 2_000_000, replace=False)
        return float(np.quantile(v, quant)) if len(v) else float("nan")


# ---------------------------------------------------------------- grid

def entry_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for w, q, lev, tk in itertools.product((15, 60, 240), (0.9, 0.95, 0.98), (False, True), (False, True)):
        conds = [(f"oidiv_{w}", ">=", ("q", q))]
        if lev:
            conds.append(("oi_to_mcap", ">=", ("q", 0.8)))
        if tk:
            conds.append(("taker_fut", "<", ("v", 1.0)))
        specs.append({"family": "squeeze_long", "side": 1, "conds": conds,
                      "key": ("squeeze_long", w, q, lev, tk),
                      "name": f"squeeze_long|oidiv{w}>=q{q:g}{'|lev' if lev else ''}{'|takersell' if tk else ''}"})
    for w, q, oi, vol, rsi in itertools.product((5, 15, 60), (0.95, 0.98, 0.995), (None, 15, 60), (False, True), (False, True)):
        conds = [(f"ret_{w}", ">=", ("q", q))]
        if oi:
            conds.append((f"oichg_{oi}", ">", ("v", 0.0)))
        if vol:
            conds.append(("rv_60", ">=", ("q", 0.7)))
        if rsi:
            conds.append(("rsi_14", ">=", ("v", 70.0)))
        specs.append({"family": "fade_short", "side": -1, "conds": conds,
                      "key": ("fade_short", w, q, oi, vol, rsi),
                      "name": f"fade_short|ret{w}>=q{q:g}{f'|oiup{oi}' if oi else ''}{'|hivol' if vol else ''}{'|rsi70' if rsi else ''}"})
    for w, q, vol, oi in itertools.product((5, 15, 60), (0.005, 0.02, 0.05), (False, True), (False, True)):
        conds = [(f"ret_{w}", "<=", ("q", q))]
        if vol:
            conds.append(("rv_60", ">=", ("q", 0.7)))
        if oi:
            conds.append(("oichg_60", "<", ("v", 0.0)))
        specs.append({"family": "dip_long", "side": 1, "conds": conds,
                      "key": ("dip_long", w, q, vol, oi),
                      "name": f"dip_long|ret{w}<=q{q:g}{'|hivol' if vol else ''}{'|oiflush' if oi else ''}"})
    return specs


def entry_mask(fr: Frames, spec: dict[str, Any]) -> tuple[np.ndarray, list]:
    m = fr.liquid.copy()
    thresholds = []
    for feat, op, (kind, val) in spec["conds"]:
        a = fr.get(feat)
        thr = fr.q(feat, val) if kind == "q" else val
        thresholds.append([feat, op, thr])
        with np.errstate(invalid="ignore"):
            m &= {">=": a >= thr, ">": a > thr, "<=": a <= thr, "<": a < thr}[op]
    return m, thresholds


def events(mask: np.ndarray, hold: int) -> tuple[np.ndarray, np.ndarray]:
    """(row, col) of entries; a coin can re-enter only after the previous
    trade's holding window ends."""
    n = mask.shape[0]
    rows, cols = [], []
    for j in np.flatnonzero(mask[: n - hold - 1].any(axis=0)):
        nxt = -1
        for i in np.flatnonzero(mask[: n - hold - 1, j]):
            if i > nxt:
                rows.append(i)
                cols.append(j)
                nxt = i + hold
    return np.asarray(rows, dtype=np.int64), np.asarray(cols, dtype=np.int64)


def outcomes(o, h, lo, c, rows, cols, side: int, hold: int) -> dict[Any, np.ndarray]:
    """Net return per event for every stop in STOPS."""
    if len(rows) == 0:
        return {s: np.zeros(0) for s in STOPS}
    entry = o[rows + 1, cols]
    path = rows[:, None] + 1 + np.arange(hold)[None, :]
    ph, pl, po = h[path, cols[:, None]], lo[path, cols[:, None]], o[path, cols[:, None]]
    last = c[rows + hold, cols]
    out = {}
    for s in STOPS:
        if s is None:
            px = last
        else:
            if side > 0:
                stop = entry * (1 - s)
                hit = pl <= stop[:, None]
            else:
                stop = entry * (1 + s)
                hit = ph >= stop[:, None]
            any_hit = hit.any(axis=1)
            first = hit.argmax(axis=1)
            op = po[np.arange(len(rows)), first]
            gap = (op < stop) if side > 0 else (op > stop)
            fill = np.where(gap & (first > 0), op, stop)
            px = np.where(any_hit, fill, last)
        r = side * (px / entry - 1) - COST
        out[s] = np.where(np.isfinite(r), r, np.nan)
    return out


# ---------------------------------------------------------------- stats

def _hour_t(ts: np.ndarray, x: np.ndarray) -> tuple[float, float, int]:
    ok = np.isfinite(x)
    ts, x = ts[ok], x[ok]
    if len(x) == 0:
        return 0.0, 0.0, 0
    s = pd.Series(x).groupby(ts // 3600).mean()
    n = len(s)
    if n < 3 or s.std(ddof=1) == 0:
        return float(s.mean()), 0.0, n
    return float(s.mean()), float(s.mean() / s.std(ddof=1) * math.sqrt(n)), n


def _p(t: float) -> float:
    return math.erfc(abs(t) / math.sqrt(2.0))


def run_grid(ctx: ind.Ctx, log: Callable[[str], None] = lambda _m: None) -> dict[str, Any]:
    fr = Frames(ctx)
    p = ctx.p
    o, h, lo, c = (p[k].to_numpy() for k in ("open", "high", "low", "close"))
    ts_idx = np.asarray(ctx.c.index)
    split = fr.split
    results = []
    specs = entry_specs()
    for k, spec in enumerate(specs):
        mask, thresholds = entry_mask(fr, spec)
        for hold in HOLDS:
            rows, cols = events(mask, hold)
            outs = outcomes(o, h, lo, c, rows, cols, spec["side"], hold)
            ets = ts_idx[rows] if len(rows) else np.zeros(0, dtype=np.int64)
            tr = rows < split
            for stop, r in outs.items():
                m_tr, t_tr, n_tr = _hour_t(ets[tr], r[tr])
                m_te, t_te, n_te = _hour_t(ets[~tr], r[~tr])
                half = split // 2
                m_h1 = _hour_t(ets[rows < half], r[rows < half])[0]
                m_h2 = _hour_t(ets[tr & (rows >= half)], r[tr & (rows >= half)])[0]
                fin = np.isfinite(r)
                results.append({
                    "rule": f"{spec['name']}|hold{hold}|stop{stop if stop else 'none'}",
                    "family": spec["family"], "side": spec["side"], "key": list(spec["key"]),
                    "hold": hold, "stop": stop, "thresholds": thresholds, "conds": spec["conds"],
                    "trades": int(fin.sum()), "train_trades": int((fin & tr).sum()), "test_trades": int((fin & ~tr).sum()),
                    "train_mean": m_tr, "train_t": t_tr, "train_hours": n_tr,
                    "test_mean": m_te, "test_t": t_te, "test_hours": n_te,
                    "train_half1": m_h1, "train_half2": m_h2,
                    "win_rate_test": float((r[~tr & fin] > 0).mean()) if (~tr & fin).any() else None,
                    "p_train": _p(t_tr) if m_tr > 0 else 1.0,
                })
        if k % 20 == 0:
            log(f"spec {k + 1}/{len(specs)} {spec['name']}")
    q = bh_qvalues([r["p_train"] for r in results])
    for r, qq in zip(results, q):
        r["q"] = qq
    _neighbours(results)
    for r in results:
        r["stable"] = bool(r["train_half1"] > 0 and r["train_half2"] > 0 and (r["neighbour_train"] or 0) > 0)
        r["validated"] = bool(r["q"] < FDR_Q and r["train_mean"] > 0 and r["stable"]
                              and r["test_trades"] >= MIN_TEST_TRADES and r["test_mean"] > 0 and r["test_t"] >= OOS_MIN_T)
    return {"rules": results, "split_ts": int(ts_idx[split]), "from": int(ts_idx[0]), "to": int(ts_idx[-1]),
            "symbols": int(c.shape[1]), "minutes": int(c.shape[0]), "tried": len(results)}


def _neighbours(results: list[dict[str, Any]]) -> None:
    """Median train mean of rules one parameter step away (same family):
    a real effect should not live on a single knife-edge setting."""
    by_key = {(tuple(r["key"]), r["hold"], r["stop"]): r for r in results}
    for r in results:
        key = tuple(r["key"])
        vals = []
        for (k2, h2, s2), r2 in by_key.items():
            if k2[0] != key[0] or r2 is r:
                continue
            diff = sum(a != b for a, b in zip(k2, key)) + (h2 != r["hold"]) + (s2 != r["stop"])
            if diff == 1 and r2["train_trades"] >= 20:
                vals.append(r2["train_mean"])
        r["neighbour_train"] = float(np.median(vals)) if vals else None


def pick(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Best per family chosen on TRAIN evidence only (then its test is honest)."""
    out = []
    for fam in ("squeeze_long", "fade_short", "dip_long"):
        cands = [r for r in results if r["family"] == fam and r["train_trades"] >= 50 and r["train_mean"] > 0
                 and r["stable"]]
        cands.sort(key=lambda r: -r["train_t"])
        if cands:
            out.append(cands[0])
    return out
