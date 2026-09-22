"""Live sentiment gate.

Scores each symbol with the composite the sentiment lab validated
(weights live in system_status["sentiment_weights"]) and decides whether an
entry agrees with sentiment.

Modes (config.SENTIMENT_GATE_MODE):
  off     never consulted
  shadow  decide and log, never block
  auto    block only while the latest research run passed its
          out-of-sample test; otherwise behave like shadow   (default)
  enforce always block on disagreement (manual override)

A score is a weighted sum of cross-sectional z-scores, so +1 means "sentiment
points to this name beating the rest of the universe by about one standard
deviation". Directional LONGs are blocked below -threshold, SHORTs above
+threshold. For a pair the long leg's score minus the short leg's score must
not fall below -threshold.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import numpy as np
import pandas as pd

from src import config
from src.research import sentiment_lab as lab

logger = logging.getLogger(__name__)

HOUR = 3600
WEIGHTS_KEY = "sentiment_weights"
STATS_KEY = "sentiment_gate_stats"
LIVE_LOOKBACK_HOURS = 800   # funding_z needs 720h

_cache: dict[str, Any] = {"hour": None, "scores": None}


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------

def _wide(rows: list[Any], value: str) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df["hour"] = (df["timestamp"].astype("int64") // HOUR) * HOUR
    df = df.sort_values("timestamp").drop_duplicates(["symbol", "hour"], keep="last")
    return df.pivot(index="hour", columns="symbol", values=value).sort_index()


def load_panel(storage: Any, since_ts: int, symbols: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """Hourly wide panel from the DB. Prices use the '1h' timeframe (the
    1h_perp table stopped updating on 2026-06-30)."""
    sym_sql, params = "", [since_ts]
    if symbols:
        sym_sql = " AND symbol IN (" + ",".join("?" * len(symbols)) + ")"
        params += list(symbols)
    with storage._connect() as conn:  # noqa: SLF001
        prices = conn.execute(
            "SELECT symbol, timestamp, close, volume FROM prices "
            "WHERE timeframe = '1h' AND timestamp >= ?" + sym_sql, tuple(params)).fetchall()
        funding = conn.execute(
            "SELECT symbol, timestamp, funding_rate FROM funding_rates "
            "WHERE timestamp >= ?" + sym_sql, tuple([since_ts - 16 * HOUR] + params[1:])).fetchall()
        ls = conn.execute(
            "SELECT symbol, timestamp, ratio FROM long_short_ratio "
            "WHERE timestamp >= ?" + sym_sql, tuple(params)).fetchall()
        oi = conn.execute(
            "SELECT symbol, timestamp, open_interest FROM open_interest "
            "WHERE timestamp >= ?" + sym_sql, tuple(params)).fetchall()
    close = _wide(prices, "close")
    vol = _wide(prices, "volume")
    ls_w = _wide(ls, "ratio")
    cols = close.columns.intersection(ls_w.columns)   # sentiment universe only
    close = close[cols]
    full = pd.RangeIndex(int(close.index.min()), int(close.index.max()) + HOUR, HOUR) if len(close) else close.index
    close = close.reindex(full)
    return {
        "close": close,
        "dollar_volume": (vol.reindex(index=full, columns=cols) * close),
        "funding": _wide(funding, "funding_rate").reindex(columns=cols).reindex(full, method="ffill"),
        "ls": ls_w.reindex(index=full, columns=cols),
        "oi": _wide(oi, "open_interest").reindex(index=full, columns=cols),
    }


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------

def load_state(storage: Any) -> dict[str, Any]:
    row = storage.get_system_status(WEIGHTS_KEY)
    if not row or not row.get("value"):
        return {"weights": {}, "enforce_ok": False}
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return {"weights": {}, "enforce_ok": False}


def save_state(storage: Any, result: dict[str, Any]) -> None:
    storage.set_system_status(WEIGHTS_KEY, json.dumps({
        "weights": result["weights"],
        "target_weights": result["target_weights"],
        "enforce_ok": result["enforce_ok"],
        "oos": result["oos"],
        "run_at": result["run_at"],
    }, default=float))


def effective_mode(state: dict[str, Any]) -> str:
    mode = str(getattr(config, "SENTIMENT_GATE_MODE", "auto")).lower()
    if mode == "auto":
        return "enforce" if state.get("enforce_ok") and state.get("weights") else "shadow"
    return mode if mode in ("off", "shadow", "enforce") else "shadow"


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def compute_scores(panel: dict[str, pd.DataFrame], weights: dict[str, float]) -> dict[str, float]:
    """Composite score per symbol at the latest hour of the panel."""
    if not weights or panel["close"].empty:
        return {}
    feats = lab.build_features(panel)
    total: pd.Series | None = None
    for f, w in weights.items():
        if not w or f not in feats:
            continue
        z = lab.xs_zscore(feats[f].iloc[-1]).fillna(0.0) * w
        total = z if total is None else total.add(z, fill_value=0.0)
    if total is None:
        return {}
    return {s: float(v) for s, v in total.items() if np.isfinite(v)}


def scores(storage: Any, state: dict[str, Any] | None = None) -> dict[str, float]:
    """Cached once per hour: the inputs only change hourly."""
    hour = int(time.time()) // HOUR
    if _cache["hour"] == hour and _cache["scores"] is not None:
        return _cache["scores"]
    state = state or load_state(storage)
    try:
        panel = load_panel(storage, int(time.time()) - LIVE_LOOKBACK_HOURS * HOUR)
        out = compute_scores(panel, state.get("weights") or {})
    except Exception:  # noqa: BLE001 -- the gate must never break a cycle
        logger.exception("sentiment scores failed; gate stays open this hour")
        out = {}
    _cache.update(hour=hour, scores=out)
    return out


# --------------------------------------------------------------------------
# decisions
# --------------------------------------------------------------------------

def _threshold() -> float:
    return float(getattr(config, "SENTIMENT_GATE_THRESHOLD", 1.0))


def decide(score: float | None, direction: str, threshold: float | None = None) -> tuple[bool, str]:
    """(agrees, reason). Unknown score always agrees."""
    thr = _threshold() if threshold is None else threshold
    if score is None:
        return True, "no sentiment score"
    if direction.upper() == "LONG" and score <= -thr:
        return False, f"sentiment {score:+.2f} opposes LONG (<= -{thr})"
    if direction.upper() == "SHORT" and score >= thr:
        return False, f"sentiment {score:+.2f} opposes SHORT (>= +{thr})"
    return True, f"sentiment {score:+.2f} ok for {direction}"


def _record(storage: Any, mode: str, blocked: bool, would_block: bool) -> None:
    try:
        row = storage.get_system_status(STATS_KEY)
        stats = json.loads(row["value"]) if row and row.get("value") else {}
        stats["checked"] = stats.get("checked", 0) + 1
        stats["would_block"] = stats.get("would_block", 0) + int(would_block)
        stats["blocked"] = stats.get("blocked", 0) + int(blocked)
        stats["mode"] = mode
        stats["updated_at"] = int(time.time())
        storage.set_system_status(STATS_KEY, json.dumps(stats))
    except Exception:  # noqa: BLE001
        logger.debug("sentiment stats write failed", exc_info=True)


def allow_entry(storage: Any, symbol: str, direction: str) -> tuple[bool, str]:
    """Directional entry check. Returns (allowed, reason)."""
    state = load_state(storage)
    mode = effective_mode(state)
    if mode == "off":
        return True, "sentiment gate off"
    agrees, reason = decide(scores(storage, state).get(symbol), direction)
    blocked = (not agrees) and mode == "enforce"
    _record(storage, mode, blocked, not agrees)
    if not agrees:
        logger.info("SENTIMENT %s %s %s: %s", "BLOCK" if blocked else "SHADOW-WOULD-BLOCK", symbol, direction, reason)
    return (not blocked), f"[{mode}] {reason}"


def allow_pair(storage: Any, long_symbol: str, short_symbol: str) -> tuple[bool, str]:
    """Pair entry check: the long leg should not be sentiment-weaker than the
    short leg by more than the threshold."""
    state = load_state(storage)
    mode = effective_mode(state)
    if mode == "off":
        return True, "sentiment gate off"
    sc = scores(storage, state)
    sl, ss = sc.get(long_symbol), sc.get(short_symbol)
    if sl is None or ss is None:
        agrees, reason = True, "no sentiment score for a leg"
    else:
        agrees, reason = decide(sl - ss, "LONG")
        reason = f"long {long_symbol} {sl:+.2f} vs short {short_symbol} {ss:+.2f}: " + reason
    blocked = (not agrees) and mode == "enforce"
    _record(storage, mode, blocked, not agrees)
    if not agrees:
        logger.info("SENTIMENT %s pair: %s", "BLOCK" if blocked else "SHADOW-WOULD-BLOCK", reason)
    return (not blocked), f"[{mode}] {reason}"
