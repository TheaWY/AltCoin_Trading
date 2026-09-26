"""Pump rider: runs once a minute (called by scripts/stream_1m.py right after
each 1-minute bar closes).

1. Detect: for every rule in system_status["pump_rules"] (written by
   scripts/pump_research.py), check its trigger on the latest bar.
2. Enter: a VALIDATED rule opens a LONG paper trade ('pump_rider') sized
   PUMP_POSITION_PCT of equity (capped by PUMP_MAX_OPEN positions and by
   2% of the coin's last-hour dollar volume, so the order would not move a
   thin book). An unvalidated rule only records a SHADOW signal in
   pump_signals -- what it would have done -- so its live record builds up
   before any money is used.
3. Exit, every minute on the latest bar's high/low: hard stop, trailing stop
   from the running peak, take-profit at the rule's expected peak
   ("how far it should rise"), or timeout. Shadow signals are closed with the
   same logic so shadow and live results are comparable.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import numpy as np

from src import config
from src.research import pump_study as ps

logger = logging.getLogger(__name__)

STRATEGY = "pump_rider"
RULES_KEY = "pump_rules"
BIG = 1e18

SIGNALS_SCHEMA = """
CREATE TABLE IF NOT EXISTS pump_signals (
    id BIGSERIAL PRIMARY KEY, rule TEXT, symbol TEXT, opened_at BIGINT, entry DOUBLE PRECISION,
    target_pct DOUBLE PRECISION, trail DOUBLE PRECISION, peak DOUBLE PRECISION,
    status TEXT, closed_at BIGINT, exit_price DOUBLE PRECISION, net DOUBLE PRECISION,
    reason TEXT, mode TEXT
)
"""


def _cfg(name: str, default: float) -> float:
    return float(getattr(config, name, default))


def load_rules(storage: Any) -> list[dict[str, Any]]:
    row = storage.get_system_status(RULES_KEY)
    try:
        return json.loads(row["value"]).get("rules", []) if row and row.get("value") else []
    except (TypeError, ValueError):
        return []


def reserve_pct(storage: Any) -> float:
    """Share of equity the core must leave free: only while the rider is
    enabled AND at least one rule is validated."""
    if not getattr(config, "PUMP_RIDER_ENABLED", False):
        return 0.0
    try:
        return _cfg("PUMP_RESERVE_PCT", 0.5) if any(r.get("validated") for r in load_rules(storage)) else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def _latest_features(storage: Any, now: int, rules: list[dict]) -> tuple[dict, dict]:
    need_pre = any(r["trigger"].get("type") in ("precursor", "prescore") for r in rules)
    minutes = 1500 if need_pre else ps.LOOKBACK_MIN + 30
    panel = ps.load_panel(storage, now - minutes * 60)
    feats: dict = {w: ps.features(panel, w) for w in ps.WINDOWS}
    if need_pre and not panel["close"].empty:
        from src.research import pump_precursors as pp

        feats["pre"] = pp.precursor_features(panel)
        feats["liquid"] = panel["quote_volume"].rolling(60, min_periods=30).sum()
    return panel, feats


def _fires(feats: dict, rule: dict, sym: str) -> bool:
    t = rule["trigger"]
    if t.get("type") == "prescore":
        try:
            if feats["liquid"][sym].iat[-1] < ps.MIN_DOLLAR_VOL:
                return False
            total, n = 0.0, 0
            for name, (sign, grid) in t["grids"].items():
                v = float(feats["pre"][name][sym].iat[-1])
                p = float(np.interp(v, grid, np.linspace(0, 1, len(grid)))) if np.isfinite(v) else 0.5
                total += p if sign > 0 else 1 - p
                n += 1
            return n > 0 and total / n >= t["thr"]
        except (KeyError, IndexError):
            return False
    if t.get("type") == "precursor":
        try:
            if feats["liquid"][sym].iat[-1] < ps.MIN_DOLLAR_VOL:
                return False
            hits = 0
            for name, (direction, thr) in t["features"].items():
                v = feats["pre"][name][sym].iat[-1]
                hits += bool(v >= thr) if direction == "ge" else bool(v <= thr)
            return hits >= t["m"]
        except (KeyError, IndexError):
            return False
    f = feats[t["w"]]
    try:
        return bool(f["z"][sym].iat[-1] >= t["z_min"] and f["vol_ratio"][sym].iat[-1] >= t["vol_min"]
                    and f["taker"][sym].iat[-1] >= t["taker_min"] and f["dvol60"][sym].iat[-1] >= ps.MIN_DOLLAR_VOL)
    except (KeyError, IndexError):
        return False


def _exit_check(entry: float, peak: float, trail: float, target: float | None,
                hi: float, lo: float, opened: int, now: int) -> tuple[float | None, str, float]:
    """(exit price or None, reason, new peak)."""
    stop = max(entry * (1 - ps.STOP_PCT), peak * (1 - trail))
    if lo <= stop:
        return stop, ("hard_stop" if stop == entry * (1 - ps.STOP_PCT) else "trail"), peak
    if target and hi >= entry * (1 + target):
        return entry * (1 + target), "target", peak
    if now - opened >= ps.HORIZON_MIN * 60:
        return (hi + lo) / 2, "timeout", peak
    return None, "", max(peak, hi)


def _net(entry: float, px: float) -> float:
    return (px * (1 - ps.SLIP) * (1 - ps.FEE)) / (entry * (1 + ps.SLIP) * (1 + ps.FEE)) - 1


def run_minute(storage: Any, now: int | None = None) -> dict[str, Any]:
    now = int(now or time.time())
    rules = load_rules(storage)
    with storage._connect() as c:  # noqa: SLF001
        c.execute(SIGNALS_SCHEMA if getattr(storage, "is_postgres", False)
                  else SIGNALS_SCHEMA.replace("BIGSERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT"))
    open_live = [t for t in storage.get_open_trades() if t.get("strategy") == STRATEGY]
    with storage._connect() as c:  # noqa: SLF001
        open_shadow = [dict(r) for r in c.execute("SELECT * FROM pump_signals WHERE status='open'").fetchall()]
    if not rules and not open_live and not open_shadow:
        return {"rules": 0}
    panel, feats = _latest_features(storage, now, rules)
    if panel["close"].empty:
        return {"rules": len(rules), "note": "no 1m data"}
    last_hi, last_lo = panel["high"].iloc[-1], panel["low"].iloc[-1]
    last_close = panel["close"].iloc[-1]

    # ---- exits
    closed = 0
    for t in open_live:
        sym = t["symbol"]
        hi, lo = last_hi.get(sym), last_lo.get(sym)
        if hi is None or np.isnan(hi):
            continue
        entry, peak = float(t["entry_price"]), float(t.get("trail_price") or t["entry_price"])
        trail = float(t.get("atr_pct") or 0.03)
        tp = float(t["take_profit"])
        target = (tp / entry - 1) if tp < BIG else None
        px, why, peak = _exit_check(entry, peak, trail, target, hi, lo, int(t["opened_at"]), now)
        if px is None:
            storage.update_paper_trade(t["id"], {"trail_price": peak})
            continue
        qty = float(t["quantity"])
        fee_in = float(t.get("fees") or 0.0)
        sell = px * (1 - ps.SLIP)
        fee_out = sell * qty * ps.FEE
        pnl = (sell - entry) * qty - fee_in - fee_out
        storage.update_paper_trade(t["id"], {"status": "closed", "exit_price": px, "closed_at": now,
                                             "pnl": pnl, "exit_reason": why, "fees": fee_in + fee_out})
        cash = float(storage.get_portfolio_state()["cash"])
        storage.update_portfolio_cash(cash + entry * qty + (sell - entry) * qty - fee_out)
        logger.info("pump_rider CLOSE %s %s pnl=%.2f", sym, why, pnl)
        closed += 1
    for s in open_shadow:
        sym = s["symbol"]
        hi, lo = last_hi.get(sym), last_lo.get(sym)
        if hi is None or np.isnan(hi):
            continue
        px, why, peak = _exit_check(s["entry"], s["peak"], s["trail"], s["target_pct"], hi, lo, s["opened_at"], now)
        with storage._connect() as c:  # noqa: SLF001
            if px is None:
                c.execute("UPDATE pump_signals SET peak=? WHERE id=?", (peak, s["id"]))
            else:
                c.execute("UPDATE pump_signals SET status='closed', closed_at=?, exit_price=?, net=?, reason=? "
                          "WHERE id=?", (now, px, _net(s["entry"], px), why, s["id"]))

    # ---- entries
    held = {t["symbol"] for t in storage.get_open_trades() if t.get("strategy") == STRATEGY}
    shadow_held = {(s["rule"], s["symbol"]) for s in open_shadow}
    opened = shadowed = 0
    max_open = int(_cfg("PUMP_MAX_OPEN", 5))
    for rule in rules:
        for sym in panel["close"].columns:
            if not _fires(feats, rule, sym):
                continue
            entry = float(last_close[sym])
            if rule.get("validated") and getattr(config, "PUMP_RIDER_ENABLED", False):
                if sym in held or len(held) >= max_open:
                    continue
                if _open_live(storage, sym, entry, rule, feats, now):
                    held.add(sym)
                    opened += 1
            elif (rule["rule"], sym) not in shadow_held:
                with storage._connect() as c:  # noqa: SLF001
                    c.execute("INSERT INTO pump_signals (rule, symbol, opened_at, entry, target_pct, trail, peak, "
                              "status, mode) VALUES (?,?,?,?,?,?,?,'open','shadow')",
                              (rule["rule"], sym, now, entry, rule.get("target_pct") if rule.get("use_tp") else None,
                               rule["trail"], entry))
                shadow_held.add((rule["rule"], sym))
                shadowed += 1
    return {"rules": len(rules), "closed": closed, "opened": opened, "shadow_opened": shadowed}


def _open_live(storage: Any, sym: str, entry: float, rule: dict, feats: dict, now: int) -> bool:
    from src.engine.paper_trader import PaperTrader

    equity = float(PaperTrader(storage).summary(None).get("equity") or 0.0)
    dvol60 = float(feats[ps.WINDOWS[0]]["dvol60"][sym].iat[-1])
    notional = min(equity * _cfg("PUMP_POSITION_PCT", 0.10), 0.02 * dvol60)
    buy = entry * (1 + ps.SLIP)
    fee = notional * ps.FEE
    cash = float(storage.get_portfolio_state()["cash"])
    if notional < 6 or notional + fee > cash:
        return False
    target = rule.get("target_pct") if rule.get("use_tp") else None
    storage.insert_paper_trade({
        "signal_id": None, "exit_price": None, "symbol": sym, "direction": "LONG",
        "entry_price": buy, "quantity": notional / buy, "stop_loss": 0.0,
        "take_profit": buy * (1 + target) if target else BIG,
        "status": "open", "pnl": None, "opened_at": now, "closed_at": None,
        "strategy": STRATEGY, "style": STRATEGY, "atr_pct": rule["trail"],
        "trail_price": buy, "exit_reason": None, "fees": fee,
    })
    storage.update_portfolio_cash(cash - notional - fee)
    logger.info("pump_rider OPEN %s notional=%.2f target=%s rule=%s", sym, notional, target, rule["rule"])
    return True
