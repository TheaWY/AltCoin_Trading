"""Live-strategy guard — the discipline in code, not memory (plan Phase 5).

Nothing trades live unless it has a PASSING gate record OR is explicitly
acknowledged as an eyes-open forward-test. If a SETUP_*_ENABLED strategy is
neither, the worker REFUSES to start -- so the book can't silently drift back to
trading strategies that failed (or never faced) validation. Also checks live
feature-data freshness (the stale-dispersion class of bug that froze the gate).

Validation source: the validated_strategies table, written by record_gate_pass()
when a real gate passes. Empty today (nothing currently clears the gate), so
every live strategy must be listed in EYES_OPEN_STRATEGIES, logged loudly each
start.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

# Every SETUP_*_ENABLED flag -> the strategy it turns on live.
SETUP_TO_STRATEGY = {
    "SETUP_FUNDING_ENABLED": "funding_rate",
    "SETUP_VOLUME_ENABLED": "volume_spike",
    "SETUP_MEANREV_ENABLED": "mean_reversion",
    "SETUP_BREAKOUT_ENABLED": "breakout",
    "SETUP_TSMOM_ENABLED": "tsmom28",
    "SETUP_SWING_ENABLED": "swing",
    "SETUP_REL_STRENGTH_ENABLED": "rel_strength_rotation",
    "SETUP_CAPITULATION_BAR_ENABLED": "capitulation_bar",
    "SETUP_VOLUME_ZSCORE_ENABLED": "volume_zscore_3plus",
    "SETUP_PUMP24_EXTREME_ENABLED": "pump24_extreme",
    "SETUP_FAILED_PUMP_LONG_ENABLED": "failed_pump_long",
    "SETUP_FAILED_PUMP_ENABLED": "failed_pump_short",
    "SETUP_MEAN_REVERSION_LONG_ENABLED": "mean_reversion_long",
    "SETUP_MEAN_REVERSION_SHORT_ENABLED": "mean_reversion_short",
    "SETUP_VOLATILITY_EXPANSION_ENABLED": "volatility_expansion",
    "SETUP_FUNDING_CARRY_ENABLED": "funding_carry",
}


class LiveGuardError(RuntimeError):
    """A strategy is enabled live without a passing gate record or an explicit
    eyes-open acknowledgement. Raising this REFUSES to start the worker."""


def ensure_schema(storage: Any) -> None:
    pg = storage.is_postgres
    ddl = (
        "CREATE TABLE IF NOT EXISTS validated_strategies ("
        "strategy TEXT PRIMARY KEY, dsr DOUBLE PRECISION, window_frac DOUBLE PRECISION, "
        "config_note TEXT, passed_at BIGINT)"
        if pg else
        "CREATE TABLE IF NOT EXISTS validated_strategies ("
        "strategy TEXT PRIMARY KEY, dsr REAL, window_frac REAL, config_note TEXT, passed_at INTEGER)"
    )
    with storage._connect() as c:
        c.execute(ddl)


def validated_strategies(storage: Any) -> set[str]:
    ensure_schema(storage)
    with storage._connect() as c:
        return {dict(r)["strategy"] for r in
                c.execute("SELECT strategy FROM validated_strategies").fetchall()}


def record_gate_pass(storage: Any, strategy: str, dsr: float, window_frac: float,
                     config_note: str = "") -> None:
    """Call this from a gate runner when a strategy PASSES the real gate."""
    ensure_schema(storage)
    ph = "%s" if storage.is_postgres else "?"
    upd = ("ON CONFLICT (strategy) DO UPDATE SET dsr=EXCLUDED.dsr, "
           "window_frac=EXCLUDED.window_frac, config_note=EXCLUDED.config_note, "
           "passed_at=EXCLUDED.passed_at" if storage.is_postgres else
           "ON CONFLICT(strategy) DO UPDATE SET dsr=excluded.dsr, "
           "window_frac=excluded.window_frac, config_note=excluded.config_note, "
           "passed_at=excluded.passed_at")
    with storage._connect() as c:
        c.execute(f"INSERT INTO validated_strategies (strategy,dsr,window_frac,config_note,passed_at) "
                  f"VALUES ({ph},{ph},{ph},{ph},{ph}) " + upd,
                  (strategy, float(dsr), float(window_frac), config_note, int(time.time())))


def _eyes_open() -> set[str]:
    return {s.strip() for s in os.getenv("EYES_OPEN_STRATEGIES", "").split(",") if s.strip()}


def enabled_live_strategies() -> set[str]:
    """Strategies whose SETUP flag is truthy in the env right now."""
    out = set()
    for flag, strat in SETUP_TO_STRATEGY.items():
        if os.getenv(flag, "").strip().lower() in ("true", "1", "yes", "on"):
            out.add(strat)
    return out


def check_live_strategies(storage: Any) -> None:
    """Raise LiveGuardError if any enabled strategy is neither validated nor
    explicitly eyes-open. Logs the disposition of every live strategy."""
    validated = validated_strategies(storage)
    eyes = _eyes_open()
    enabled = enabled_live_strategies()
    if not enabled:
        logger.info("LIVE GUARD: no setup strategies enabled (all-cash).")
        return
    offenders = []
    for strat in sorted(enabled):
        if strat in validated:
            logger.info("LIVE GUARD: %s — VALIDATED (passing gate record).", strat)
        elif strat in eyes:
            logger.warning("LIVE GUARD: %s — UNVALIDATED, running EYES-OPEN "
                           "(no passing gate record; explicit override).", strat)
        else:
            offenders.append(strat)
    if offenders:
        raise LiveGuardError(
            f"REFUSING TO START: {offenders} enabled live with no passing gate record and "
            f"not in EYES_OPEN_STRATEGIES. Gate them, or add to EYES_OPEN_STRATEGIES to run "
            f"eyes-open on purpose."
        )


def check_feature_freshness(storage: Any, max_stale_h: float = 6.0) -> None:
    """Warn if a live feature dependency (xsec_dispersion, read by the dispersion
    gate) is stale -- the class of bug that silently froze the gate."""
    try:
        with storage._connect() as c:
            row = c.execute("SELECT MAX(timestamp) mx FROM xsec_dispersion").fetchone()
        latest = int(dict(row)["mx"]) if row and dict(row).get("mx") else 0
    except Exception:
        return
    if latest == 0:
        return
    age_h = (time.time() - latest) / 3600.0
    if age_h > max_stale_h:
        logger.warning("LIVE GUARD: xsec_dispersion is STALE (%.0fh old) — the dispersion "
                       "gate may be frozen. Is com.altcoin.xsec running?", age_h)
