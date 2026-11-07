"""Forward test for the strategy grid's candidates (no money).

Every minute (called from scripts/stream_1m.py after the pump rider):
  * load the candidate rules published by scripts/strategy_grid.py
    (system_status strategy_grid / strategy_grid_cost0.001 -> "shadow")
  * evaluate their entry conditions on the latest bar with the SAME
    thresholds the grid fixed on its train window
  * record a signal per (rule, coin) when it fires and none is open; it is
    closed at the stop (bar high/low) or after `hold` minutes at the close
  * net return is stored at both taker (0.3%) and maker (0.1%) round-trip cost

Results live in grid_signals; the dashboard shows them per rule.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

SCHEMA = """CREATE TABLE IF NOT EXISTS grid_signals (
    id BIGSERIAL PRIMARY KEY, rule TEXT NOT NULL, family TEXT, symbol TEXT NOT NULL, side INTEGER NOT NULL,
    opened_at BIGINT NOT NULL, entry DOUBLE PRECISION NOT NULL, hold INTEGER NOT NULL, stop DOUBLE PRECISION,
    status TEXT NOT NULL, closed_at BIGINT, exit_price DOUBLE PRECISION, gross DOUBLE PRECISION,
    net_taker DOUBLE PRECISION, net_maker DOUBLE PRECISION, reason TEXT)"""
LOOKBACK_MIN = 600


def load_rules(storage: Any) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key in ("strategy_grid", "strategy_grid_cost0.001"):
        row = storage.get_system_status(key)
        if not row or not row.get("value"):
            continue
        try:
            for r in json.loads(row["value"]).get("shadow") or []:
                out.setdefault(r["rule"], r)
        except ValueError:
            continue
    return list(out.values())


def _ensure(storage: Any) -> None:
    with storage._connect() as c:  # noqa: SLF001
        c.execute(SCHEMA if getattr(storage, "is_postgres", False)
                  else SCHEMA.replace("BIGSERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT"))


def _fires_now(fr, rule: dict[str, Any]) -> np.ndarray:
    """Boolean per coin: all conditions true on the last bar (with the
    grid's fixed thresholds), coin liquid."""
    m = fr.liquid[-1].copy()
    for feat, op, thr in rule["thresholds"]:
        a = fr.get(feat)[-1]
        with np.errstate(invalid="ignore"):
            m &= {">=": a >= thr, ">": a > thr, "<=": a <= thr, "<": a < thr}[op]
    return m


def run_minute(storage: Any, now: int | None = None) -> dict[str, Any]:
    from src.research import indicators as ind
    from src.research import strategy_grid as sg

    now = int(now or time.time())
    rules = load_rules(storage)
    _ensure(storage)
    with storage._connect() as c:  # noqa: SLF001
        open_sig = [dict(r) for r in c.execute("SELECT * FROM grid_signals WHERE status='open'").fetchall()]
    if not rules and not open_sig:
        return {"rules": 0}
    ctx = ind.build_ctx(storage, LOOKBACK_MIN, now=now, with_daily=False)
    if ctx.c.empty:
        return {"rules": len(rules), "note": "no data"}
    cols = list(ctx.c.columns)
    hi, lo, cl = (ctx.p[k].iloc[-1] for k in ("high", "low", "close"))
    bar_ts = int(ctx.c.index[-1])

    closed = 0
    for s in open_sig:
        sym = s["symbol"]
        h, lw, px_c = hi.get(sym), lo.get(sym), cl.get(sym)
        if h is None or not np.isfinite(h) or bar_ts < s["opened_at"]:
            continue  # the trade's first bar (opened_at) is the first one checked
        side, entry, stop = int(s["side"]), float(s["entry"]), s["stop"]
        px, why = None, ""
        if stop:
            lvl = entry * (1 - stop) if side > 0 else entry * (1 + stop)
            if (side > 0 and lw <= lvl) or (side < 0 and h >= lvl):
                px, why = lvl, "stop"
        if px is None and bar_ts - int(s["opened_at"]) >= (int(s["hold"]) - 1) * 60:  # close of the hold-th bar
            px, why = float(px_c), "time"
        if px is None:
            continue
        gross = side * (px / entry - 1)
        with storage._connect() as c:  # noqa: SLF001
            c.execute("UPDATE grid_signals SET status='closed', closed_at=?, exit_price=?, gross=?, net_taker=?, "
                      "net_maker=?, reason=? WHERE id=?", (now, px, gross, gross - 0.003, gross - 0.001, why, s["id"]))
        closed += 1

    fr = sg.Frames(ctx)
    held = {(s["rule"], s["symbol"]) for s in open_sig}
    opened = 0
    for rule in rules:
        try:
            fire = _fires_now(fr, rule)
        except Exception:  # noqa: BLE001
            logger.exception("grid rule %s failed", rule.get("rule"))
            continue
        for j in np.flatnonzero(fire):
            sym = cols[j]
            if (rule["rule"], sym) in held or not np.isfinite(cl.get(sym, np.nan)):
                continue
            with storage._connect() as c:  # noqa: SLF001
                c.execute("INSERT INTO grid_signals (rule, family, symbol, side, opened_at, entry, hold, stop, status) "
                          "VALUES (?,?,?,?,?,?,?,?,'open')",
                          (rule["rule"], rule["family"], sym, int(rule["side"]), bar_ts + 60, float(cl[sym]),
                           int(rule["hold"]), rule.get("stop")))
            held.add((rule["rule"], sym))
            opened += 1
    return {"rules": len(rules), "opened": opened, "closed": closed}
