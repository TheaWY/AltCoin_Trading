"""Signal book: the MAIN book's allocation to the research composite.

Same idea as the signal lab (long the top of the composite ranking, short
the bottom, market-neutral, 24h rebalance, surviving legs kept), but with
real paper money from portfolio_state and every leg recorded in paper_trades
as strategy 'signal_xs', so it shows on the dashboard and in P&L.

Sizing: gross = SIGNAL_BOOK_PCT x equity, split evenly long/short. Every leg
must clear the exchange's minimum order size (Binance USDT-M: $5 for most
perps, $20-$50 for BTC/ETH and a few majors), so the number of names per side
is cut until each leg is at least MIN_LEG_USD and any symbol whose own minimum
is above the leg size is skipped. At $100 gross that is about 8 names a side.

Legs have no stop/target (sentinels; the per-symbol exit path skips them):
they exit only on rebalance or when SIGNAL_BOOK_ENABLED is turned off.
The core (core_manager) keeps SIGNAL_BOOK_PCT of equity free for this book.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from src import config

logger = logging.getLogger(__name__)

STRATEGY = "signal_xs"
STATE_KEY = "signal_book_state"
HOUR = 3600
BIG = 1e18
REBALANCE_HOURS = 24
MIN_LEG_USD = 6.0            # a little above Binance's $5 minimum
RESIZE_TOLERANCE = 0.25
MAX_PER_SIDE = 20
_MIN_NOTIONAL_CACHE: dict[str, Any] = {"at": 0, "map": {}}


def enabled() -> bool:
    return bool(getattr(config, "SIGNAL_BOOK_ENABLED", False))


def pct() -> float:
    return float(getattr(config, "SIGNAL_BOOK_PCT", 0.10)) if enabled() else 0.0


def _fee() -> float:
    return float(getattr(config, "FEE_PCT_PER_SIDE", 0.0005))


def min_notional_map() -> dict[str, float]:
    """Binance USDT-M minimum order notional per symbol (public endpoint,
    cached 12h). Falls back to $5 when unreachable."""
    now = int(time.time())
    if now - _MIN_NOTIONAL_CACHE["at"] < 12 * HOUR and _MIN_NOTIONAL_CACHE["map"]:
        return _MIN_NOTIONAL_CACHE["map"]
    out: dict[str, float] = {}
    try:
        import requests

        data = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=15).json()
        for s in data.get("symbols", []):
            if s.get("quoteAsset") != "USDT" or s.get("contractType") != "PERPETUAL":
                continue
            for f in s.get("filters", []):
                if f.get("filterType") == "MIN_NOTIONAL":
                    out[f"{s['baseAsset']}/USDT"] = float(f["notional"])
    except Exception:  # noqa: BLE001
        logger.warning("exchangeInfo unavailable; assuming $5 minimum order size")
    _MIN_NOTIONAL_CACHE.update(at=now, map=out)
    return out


def target_book(scores: dict[str, float], gross: float,
                min_notional: dict[str, float]) -> dict[str, tuple[str, float]]:
    """symbol -> (direction, notional). Balanced long/short, every leg
    tradeable at the exchange minimum."""
    per_side = gross / 2.0
    k = min(MAX_PER_SIDE, int(per_side // MIN_LEG_USD))
    if k < 1:
        return {}
    leg = per_side / k
    tradeable = [s for s in scores if min_notional.get(s, 5.0) <= leg]
    if len(tradeable) < 2 * k:
        k = len(tradeable) // 2
        if k < 1:
            return {}
        leg = per_side / k
    ranked = sorted(tradeable, key=lambda s: scores[s])
    out = {s: ("LONG", leg) for s in ranked[-k:]}
    out.update({s: ("SHORT", leg) for s in ranked[:k]})
    return out


def book_trades(storage: Any) -> list[dict[str, Any]]:
    return [t for t in storage.get_open_trades() if t.get("strategy") == STRATEGY]


def _value(t: dict[str, Any], price: float) -> float:
    qty, entry = float(t["quantity"]), float(t["entry_price"])
    return qty * price if t["direction"] == "LONG" else qty * entry + (entry - price) * qty


def _close(storage: Any, t: dict[str, Any], price: float, now: int, reason: str) -> None:
    qty, entry = float(t["quantity"]), float(t["entry_price"])
    exit_fee = qty * price * _fee()
    gross = (price - entry) * qty if t["direction"] == "LONG" else (entry - price) * qty
    open_fee = float(t.get("fees") or 0.0)
    storage.update_paper_trade(t["id"], {
        "status": "closed", "exit_price": price, "closed_at": now,
        "pnl": gross - exit_fee - open_fee, "exit_reason": reason, "fees": open_fee + exit_fee,
    })
    cash = float(storage.get_portfolio_state()["cash"])
    storage.update_portfolio_cash(cash + entry * qty + gross - exit_fee)


def _open(storage: Any, sym: str, direction: str, notional: float, price: float, now: int) -> bool:
    fee = notional * _fee()
    cash = float(storage.get_portfolio_state()["cash"])
    if notional + fee > cash:
        return False
    storage.insert_paper_trade({
        "signal_id": None, "exit_price": None, "symbol": sym, "direction": direction,
        "entry_price": price, "quantity": notional / price,
        "stop_loss": 0.0 if direction == "LONG" else BIG,
        "take_profit": BIG if direction == "LONG" else 0.0,
        "status": "open", "pnl": None, "opened_at": now, "closed_at": None,
        "strategy": STRATEGY, "style": STRATEGY, "atr_pct": None,
        "trail_price": price, "exit_reason": None, "fees": fee,
    })
    storage.update_portfolio_cash(cash - notional - fee)
    return True


def _state(storage: Any) -> dict[str, Any]:
    row = storage.get_system_status(STATE_KEY)
    try:
        return json.loads(row["value"]) if row and row.get("value") else {}
    except (TypeError, ValueError):
        return {}


def run_signal_book_cycle(storage: Any = None, now: int | None = None,
                          scores: dict[str, float] | None = None) -> dict[str, Any]:
    from src.data.storage import get_storage

    storage = storage or get_storage()
    now = int(now or time.time())
    trades = book_trades(storage)

    def price(sym: str) -> float | None:
        row = storage.get_latest_price(sym)
        return float(row["close"]) if row else None

    if not enabled():
        for t in trades:
            _close(storage, t, price(t["symbol"]) or float(t["entry_price"]), now, "signal_book_disabled")
        return {"enabled": False, "closed": len(trades)}

    state = _state(storage)
    if now - int(state.get("last_rebalance") or 0) < REBALANCE_HOURS * HOUR:
        return {"enabled": True, "action": "hold", "legs": len(trades)}

    if scores is None:
        from src.engine import sentiment_gate as gate

        gstate = gate.load_state(storage)
        scores = gate.scores(storage, gstate) if gstate.get("weights") else {}

    from src.engine.paper_trader import PaperTrader

    btc = price(config.SYMBOL)
    equity = float(PaperTrader(storage).summary(btc).get("equity") or 0.0)
    target = target_book(scores, pct() * equity, min_notional_map()) if scores else {}

    kept = set()
    for t in trades:
        px = price(t["symbol"]) or float(t["entry_price"])
        tgt = target.get(t["symbol"])
        if tgt and tgt[0] == t["direction"] and abs(_value(t, px) - tgt[1]) <= RESIZE_TOLERANCE * tgt[1]:
            kept.add(t["symbol"])
        else:
            _close(storage, t, px, now, "signal_rebalance")
    opened = 0
    for sym, (direction, notional) in target.items():
        if sym in kept:
            continue
        px = price(sym)
        if px and _open(storage, sym, direction, notional, px, now):
            opened += 1
    storage.set_system_status(STATE_KEY, json.dumps({"last_rebalance": now, "legs": len(kept) + opened}))
    logger.info("signal book rebalance: kept %d, opened %d, target %d legs, gross %.2f",
                len(kept), opened, len(target), pct() * equity)
    return {"enabled": True, "action": "rebalance" if target else "flat", "kept": len(kept),
            "opened": opened, "target_legs": len(target)}
