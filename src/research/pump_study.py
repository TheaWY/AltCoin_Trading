"""Pump study: which fast rises keep going long enough to ride, how far they
go, and which exit captures it -- on 1-minute bars, net of costs.

A RULE = trigger x exit.

Trigger (all computed from bars <= t):
  w-minute log return in units of the coin's own 4h volatility
  (z = ret_w / (sigma_1m * sqrt(w))) >= z_min, w-minute quote volume
  >= vol_min x its 4h per-minute average, taker-buy share >= taker_min,
  last-hour quote volume >= MIN_DOLLAR_VOL (tradeable). One event per
  symbol per COOLDOWN_MIN.

Entry at the trigger bar's close plus slippage. Exit, simulated minute by
minute on highs/lows for up to HORIZON_MIN:
  * hard stop at -STOP_PCT
  * trailing stop trail_pct below the running peak
  * optional take-profit at the EXPECTED PEAK (median run-up of this
    trigger in the training window) -- "to what point it will rise"
Costs: FEE per side + SLIP per side (pumps are expensive to trade).

Significance: net returns are averaged per clock hour first (events in the
same hour are not independent), t over hours, Benjamini-Hochberg across all
rules. A rule is VALIDATED only if it is significant in the first 70% of
the sample AND its net expectancy stays positive (t >= OOS_MIN_T) on the
last 30%, with its take-profit target fitted on the first 70% only.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from src.research.sentiment_lab import bh_qvalues

LOOKBACK_MIN = 240
HORIZON_MIN = 240
COOLDOWN_MIN = 60
MIN_DOLLAR_VOL = 200_000.0
STOP_PCT = 0.04
FEE = 0.0005
SLIP = 0.001
TRAIN_FRAC = 0.7
FDR_Q = 0.10
OOS_MIN_T = 1.5
MIN_EVENTS = 30

WINDOWS = (3, 5, 15)
Z_MINS = (3.0, 4.5, 6.0)
VOL_MINS = (2.0, 4.0)
TAKER_MINS = (0.0, 0.6)
TRAILS = (0.015, 0.03, 0.05)
USE_TP = (False, True)


@dataclass(frozen=True)
class Trigger:
    w: int
    z_min: float
    vol_min: float
    taker_min: float

    @property
    def key(self) -> str:
        return f"w{self.w}_z{self.z_min:g}_v{self.vol_min:g}_t{self.taker_min:g}"


@dataclass(frozen=True)
class Rule:
    trigger: Trigger
    trail: float
    use_tp: bool

    @property
    def key(self) -> str:
        return f"{self.trigger.key}|trail{self.trail:g}|{'tp' if self.use_tp else 'notp'}"


def triggers() -> list[Trigger]:
    return [Trigger(*p) for p in itertools.product(WINDOWS, Z_MINS, VOL_MINS, TAKER_MINS)]


def rules() -> list[Rule]:
    return [Rule(t, tr, tp) for t in triggers() for tr in TRAILS for tp in USE_TP]


# --------------------------------------------------------------------------
# features
# --------------------------------------------------------------------------

def features(panel: dict[str, pd.DataFrame], w: int) -> dict[str, pd.DataFrame]:
    close, qv, tb = panel["close"], panel["quote_volume"], panel["taker_buy_quote"]
    lr = np.log(close / close.shift(1))
    sigma = lr.rolling(LOOKBACK_MIN, min_periods=120).std().shift(w)   # vol BEFORE the move
    ret_w = np.log(close / close.shift(w))
    z = ret_w / (sigma * math.sqrt(w))
    qv_w = qv.rolling(w, min_periods=w).sum()
    base = qv.rolling(LOOKBACK_MIN, min_periods=120).mean().shift(w) * w
    tb_w = tb.rolling(w, min_periods=w).sum()
    return {
        "z": z,
        "vol_ratio": qv_w / base.replace(0.0, np.nan),
        "taker": tb_w / qv_w.replace(0.0, np.nan),
        "dvol60": qv.rolling(60, min_periods=30).sum(),
    }


def trigger_mask(f: dict[str, pd.DataFrame], t: Trigger) -> pd.DataFrame:
    return ((f["z"] >= t.z_min) & (f["vol_ratio"] >= t.vol_min)
            & (f["taker"] >= t.taker_min) & (f["dvol60"] >= MIN_DOLLAR_VOL))


def events(mask: pd.DataFrame) -> list[tuple[int, str]]:
    """(row index, symbol) of trigger events, one per symbol per cooldown."""
    out: list[tuple[int, str]] = []
    arr = mask.to_numpy(dtype=bool)
    for j, sym in enumerate(mask.columns):
        idx = np.flatnonzero(arr[:, j])
        last = -10 ** 9
        for i in idx:
            if i - last >= COOLDOWN_MIN:
                out.append((int(i), sym))
                last = i
    out.sort()
    return out


# --------------------------------------------------------------------------
# outcomes
# --------------------------------------------------------------------------

def path(panel: dict[str, pd.DataFrame], i: int, sym: str) -> tuple[float, np.ndarray, np.ndarray]:
    """Entry price and the next HORIZON_MIN highs/lows."""
    entry = float(panel["close"][sym].iat[i])
    hi = panel["high"][sym].to_numpy()[i + 1:i + 1 + HORIZON_MIN]
    lo = panel["low"][sym].to_numpy()[i + 1:i + 1 + HORIZON_MIN]
    ok = ~(np.isnan(hi) | np.isnan(lo))
    return entry, hi[ok], lo[ok]


def run_up(entry: float, hi: np.ndarray, lo: np.ndarray) -> tuple[float, int, float]:
    """(max favourable excursion, minutes to peak, max adverse before peak)."""
    if len(hi) == 0:
        return 0.0, 0, 0.0
    k = int(np.argmax(hi))
    mfe = hi[k] / entry - 1
    mae = (lo[:k + 1].min() / entry - 1) if k >= 0 else 0.0
    return float(mfe), k + 1, float(mae)


def simulate_exit(entry: float, hi: np.ndarray, lo: np.ndarray, trail: float,
                  tp: float | None) -> tuple[float, int, str]:
    """Net return, minutes held, reason. Conservative ordering inside a bar:
    stops are checked before the take-profit."""
    buy = entry * (1 + SLIP)
    peak = entry
    stop_hard = entry * (1 - STOP_PCT)
    for m in range(len(hi)):
        stop = max(stop_hard, peak * (1 - trail))
        if lo[m] <= stop:
            px, why = stop, ("hard_stop" if stop == stop_hard else "trail")
            break
        if tp is not None and hi[m] >= entry * (1 + tp):
            px, why = entry * (1 + tp), "target"
            break
        peak = max(peak, hi[m])
    else:
        m = len(hi) - 1
        px, why = (lo[-1] + hi[-1]) / 2 if len(hi) else entry, "timeout"
    sell = px * (1 - SLIP)
    net = (sell * (1 - FEE)) / (buy * (1 + FEE)) - 1
    return float(net), m + 1, why


def _hourly_t(ts: np.ndarray, x: np.ndarray) -> tuple[float, float, int]:
    """Mean, t-stat and n after averaging events within the same clock hour."""
    if len(x) == 0:
        return 0.0, 0.0, 0
    s = pd.Series(x).groupby(ts // 3600).mean()
    n = len(s)
    if n < 3 or s.std(ddof=1) == 0:
        return float(s.mean()), 0.0, n
    return float(s.mean()), float(s.mean() / s.std(ddof=1) * math.sqrt(n)), n


def _p(t: float) -> float:
    return math.erfc(abs(t) / math.sqrt(2.0))


def evaluate(panel: dict[str, pd.DataFrame], split: int, key: str, trig_dict: dict,
             evs: list[tuple[int, str]], results: list[dict[str, Any]]) -> None:
    """Simulate every exit policy for one trigger's events, train/test split."""
    idx = panel["close"].index.to_numpy()
    n_rows = len(idx)
    paths = [(i, s, *path(panel, i, s)) for i, s in evs if i + 1 < n_rows]
    paths = [p for p in paths if len(p[3]) >= 5]
    if not paths:
        return
    train = [p for p in paths if p[0] < split]
    test = [p for p in paths if p[0] >= split]
    ups = np.array([run_up(e, h, l)[0] for _, _, e, h, l in train]) if train else np.array([])
    target = float(np.median(ups)) if len(ups) else None
    mfe_all = np.array([run_up(e, h, l) for _, _, e, h, l in paths])
    for trail, use_tp in itertools.product(TRAILS, USE_TP):
        tp = target if use_tp else None
        if use_tp and (tp is None or tp <= 2 * (FEE + SLIP)):
            continue
        out = {}
        for name, part in (("train", train), ("test", test)):
            ts = np.array([int(idx[i]) for i, *_ in part], dtype=np.int64)
            sims = [simulate_exit(e, h, l, trail, tp) for _, _, e, h, l in part]
            nets = np.array([x[0] for x in sims])
            mean, t, hours = _hourly_t(ts, nets)
            out[name] = {"events": len(part), "hours": hours, "mean_net": mean, "t": t,
                         "win_rate": float((nets > 0).mean()) if len(nets) else None,
                         "hold_min_median": float(np.median([x[1] for x in sims])) if sims else None}
        results.append({
            "rule": f"{key}|trail{trail:g}|{'tp' if use_tp else 'notp'}", "trigger": trig_dict,
            "trail": trail, "use_tp": use_tp, "target_pct": target, "events": len(paths),
            "mfe_median": float(np.median(mfe_all[:, 0])),
            "mfe_p75": float(np.quantile(mfe_all[:, 0], 0.75)),
            "minutes_to_peak_median": float(np.median(mfe_all[:, 1])),
            "mae_median": float(np.median(mfe_all[:, 2])),
            **{f"{k}_{m}": v for k, d in out.items() for m, v in d.items()},
        })


def run_study(panel: dict[str, pd.DataFrame], log=lambda _m: None,
              extra: list[tuple[str, dict, list]] | None = None) -> dict[str, Any]:
    idx = panel["close"].index.to_numpy()
    n_rows = len(idx)
    split = int(n_rows * TRAIN_FRAC)
    results: list[dict[str, Any]] = []
    feats: dict[int, dict[str, pd.DataFrame]] = {}
    for trig in triggers():
        if trig.w not in feats:
            feats[trig.w] = features(panel, trig.w)
        evs = events(trigger_mask(feats[trig.w], trig))
        evaluate(panel, split, trig.key, {"type": "burst", **asdict(trig)}, evs, results)
        log(f"{trig.key}: {len(evs)} events")
    for key, trig_dict, evs in (extra or []):
        evaluate(panel, split, key, trig_dict, evs, results)
        log(f"{key}: {len(evs)} events")
    # BH on the training-window t across every rule with enough events
    elig = [r for r in results if r["train_events"] >= MIN_EVENTS and r["train_hours"] >= 10]
    qs = bh_qvalues([_p(r["train_t"]) for r in elig])
    for r, q in zip(elig, qs):
        r["train_q"] = q
    for r in results:
        q = r.get("train_q")
        r["validated"] = bool(
            q is not None and q < FDR_Q and r["train_mean_net"] > 0
            and r["test_events"] >= 10 and r["test_mean_net"] > 0 and r["test_t"] >= OOS_MIN_T)
    results.sort(key=lambda r: (not r["validated"], -(r["test_mean_net"] or -1)))
    return {
        "minutes": n_rows, "symbols": int(panel["close"].shape[1]),
        "from": int(idx[0]) if n_rows else None, "to": int(idx[-1]) if n_rows else None,
        "split_ts": int(idx[split]) if n_rows else None,
        "rules_tested": len(results), "validated": [r for r in results if r["validated"]],
        "results": results,
    }


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------

FIELDS = ("open", "high", "low", "close", "quote_volume", "taker_buy_quote", "trades")


def load_panel(storage: Any, since_ts: int, symbols: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """Wide 1-minute panel (index = minute ts, columns = symbols)."""
    sql = ("SELECT symbol, ts, open, high, low, close, quote_volume, taker_buy_quote, trades "
           "FROM prices_1m WHERE ts >= ?")
    params: list[Any] = [since_ts]
    if symbols:
        sql += " AND symbol IN (" + ",".join("?" * len(symbols)) + ")"
        params += list(symbols)
    with storage._connect() as c:  # noqa: SLF001
        rows = c.execute(sql, tuple(params)).fetchall()
    if not rows:
        return {f: pd.DataFrame() for f in FIELDS}
    df = pd.DataFrame([tuple(r.values()) if hasattr(r, "values") else tuple(r) for r in rows],
                      columns=["symbol", "ts", *FIELDS])
    full = pd.RangeIndex(int(df["ts"].min()), int(df["ts"].max()) + 60, 60)
    out = {}
    for f in FIELDS:
        out[f] = df.pivot(index="ts", columns="symbol", values=f).reindex(full).astype(float)
    return out
