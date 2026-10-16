"""Signal lab: forward-tests the hypothesis engine's composite with paper money
that is kept completely separate from the main book.

Backtests on the same data that found a signal overstate it; the honest
check is whether the signal keeps working on hours that did not exist when it
was discovered. This book does exactly that:

  * every REBALANCE_HOURS, rank the universe by the live composite score
    (the same scores the sentiment gate uses)
  * go long the top QUANTILE and short the bottom QUANTILE, equal weight,
    GROSS x equity split evenly between the two sides (market-neutral)
  * pay FEE_PCT_PER_SIDE on every traded notional
  * mark to market every cycle, record equity in signal_lab_equity

When the engine has no weights the book stays flat, so its equity curve only
measures periods when the research actually had an opinion.

State lives in system_status["signal_lab_state"]; nothing here touches
paper_trades or portfolio_state.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

from src import config

logger = logging.getLogger(__name__)

STATE_KEY = "signal_lab_state"
HOUR = 3600
REBALANCE_HOURS = 24
QUANTILE = 0.2
GROSS = 1.0
MIN_NAMES = 20
RESIZE_TOLERANCE = 0.25   # keep a surviving leg unless it is >25% off its target size


def _fee() -> float:
    return float(getattr(config, "FEE_PCT_PER_SIDE", 0.0005))


def _starting() -> float:
    return float(getattr(config, "PAPER_STARTING_CAPITAL", 1000.0))


def fresh_state(now: int) -> dict[str, Any]:
    return {"cash": _starting(), "positions": [], "started_at": now, "last_rebalance": 0,
            "fees_paid": 0.0, "rebalances": 0, "starting": _starting()}


def load_state(storage: Any, now: int) -> dict[str, Any]:
    row = storage.get_system_status(STATE_KEY)
    if row and row.get("value"):
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            pass
    return fresh_state(now)


def save_state(storage: Any, state: dict[str, Any]) -> None:
    storage.set_system_status(STATE_KEY, json.dumps(state))


def _pos_value(p: dict[str, Any], price: float) -> float:
    pnl = (price - p["entry"]) * p["qty"] if p["dir"] == "LONG" else (p["entry"] - price) * p["qty"]
    return p["entry"] * p["qty"] + pnl


def equity(state: dict[str, Any], price_of: Callable[[str], float | None]) -> float:
    eq = float(state["cash"])
    for p in state["positions"]:
        px = price_of(p["symbol"])
        eq += _pos_value(p, px if px else p["entry"])
    return eq


def target_book(scores: dict[str, float], eq: float) -> dict[str, tuple[str, float]]:
    """symbol -> (direction, notional)."""
    names = sorted(scores, key=lambda s: scores[s])
    if len(names) < MIN_NAMES:
        return {}
    k = max(1, int(len(names) * QUANTILE))
    per_side = GROSS * eq / 2.0
    out: dict[str, tuple[str, float]] = {}
    for s in names[-k:]:
        out[s] = ("LONG", per_side / k)
    for s in names[:k]:
        out[s] = ("SHORT", per_side / k)
    return out


def rebalance(state: dict[str, Any], scores: dict[str, float],
              price_of: Callable[[str], float | None], now: int) -> dict[str, Any]:
    fee = _fee()
    eq = equity(state, price_of)
    target = target_book(scores, eq)
    kept: list[dict] = []

    def close(p: dict) -> None:
        px = price_of(p["symbol"]) or p["entry"]
        cost = px * p["qty"] * fee
        state["cash"] += _pos_value(p, px) - cost
        state["fees_paid"] += cost

    # Keep legs that stay in the book on the same side and within
    # RESIZE_TOLERANCE of their target size: re-trading them would only pay
    # fees. Everything else is closed and reopened at target.
    for p in state["positions"]:
        tgt = target.get(p["symbol"])
        px = price_of(p["symbol"]) or p["entry"]
        if tgt and tgt[0] == p["dir"] and tgt[1] > 0 and \
                abs(_pos_value(p, px) - tgt[1]) <= RESIZE_TOLERANCE * tgt[1]:
            kept.append(p)
        else:
            close(p)
    held = {p["symbol"] for p in kept}
    state["positions"] = kept
    for sym, (direction, notional) in target.items():
        if sym in held:
            continue
        px = price_of(sym)
        if not px or notional <= 0:
            continue
        cost = notional * fee
        state["cash"] -= notional + cost
        state["fees_paid"] += cost
        state["positions"].append({"symbol": sym, "dir": direction, "qty": notional / px, "entry": px})
    state["last_rebalance"] = now
    state["rebalances"] = int(state.get("rebalances", 0)) + 1
    return state


def _ensure_table(storage: Any) -> None:
    with storage._connect() as c:  # noqa: SLF001
        c.execute("CREATE TABLE IF NOT EXISTS signal_lab_equity (timestamp BIGINT, equity DOUBLE PRECISION, "
                  "n_long INTEGER, n_short INTEGER, weights TEXT)")


def run_signal_lab_cycle(storage: Any = None, now: int | None = None) -> dict[str, Any]:
    from src.data.storage import get_storage
    from src.engine import sentiment_gate as gate

    storage = storage or get_storage()
    now = int(now or time.time())
    state = load_state(storage, now)

    cache: dict[str, float | None] = {}

    def price_of(sym: str) -> float | None:
        if sym not in cache:
            row = storage.get_latest_price(sym)
            cache[sym] = float(row["close"]) if row else None
        return cache[sym]

    gstate = gate.load_state(storage)
    weights = {k: w for k, w in (gstate.get("weights") or {}).items() if w}
    action = "hold"
    if now - int(state.get("last_rebalance") or 0) >= REBALANCE_HOURS * HOUR:
        scores = gate.scores(storage, gstate) if weights else {}
        state = rebalance(state, scores, price_of, now)
        action = "rebalance" if scores else "flat"
    eq = equity(state, price_of)
    save_state(storage, state)
    _ensure_table(storage)
    n_long = sum(p["dir"] == "LONG" for p in state["positions"])
    with storage._connect() as c:  # noqa: SLF001
        ph = "%s" if getattr(storage, "is_postgres", False) else "?"
        c.execute(f"INSERT INTO signal_lab_equity (timestamp, equity, n_long, n_short, weights) "
                  f"VALUES ({ph},{ph},{ph},{ph},{ph})",
                  (now, eq, n_long, len(state["positions"]) - n_long, json.dumps(weights)))
    if action != "hold":
        logger.info("signal lab %s: equity %.2f, %d long / %d short, weights %s",
                    action, eq, n_long, len(state["positions"]) - n_long, weights)
    return {"action": action, "equity": round(eq, 2), "return_pct": round((eq / state["starting"] - 1) * 100, 3),
            "positions": len(state["positions"])}
