"""Core allocation: idle capital sits in BTC instead of cash.

The strategy book lost to plain BTC hold mostly because (a) the trades had no
edge and (b) on average under half the capital was deployed, so the book
missed the market's drift. The core fixes (b): every cycle, capital that no
strategy is using is held as a LONG BTC/USDT spot position (strategy
'core_btc'). Strategies still take priority -- the target is

    core_target = equity * CORE_PCT - capital already committed to strategies

and the core only rebalances when it drifts more than CORE_BAND of equity
from that target, so it does not churn fees.

The core trade has no stop or target (non-triggering sentinels, and the
per-symbol exit path skips it like pairs). It exits only here: trimmed when
strategies need the cash, or fully closed when CORE_ENABLED is turned off.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src import config
from src.data.storage import get_storage

logger = logging.getLogger(__name__)

STRATEGY = "core_btc"
BIG = 1e18


def _symbol() -> str:
    return str(getattr(config, "CORE_SYMBOL", config.SYMBOL))


def _fee() -> float:
    return float(getattr(config, "FEE_PCT_PER_SIDE", 0.0005))


def core_trades(storage: Any) -> list[dict[str, Any]]:
    return [t for t in storage.get_open_trades() if t.get("strategy") == STRATEGY]


def core_value(storage: Any, price: float | None = None) -> float:
    trades = core_trades(storage)
    if not trades:
        return 0.0
    if price is None:
        row = storage.get_latest_price(_symbol())
        price = float(row["close"]) if row else None
    return sum(float(t["quantity"]) * float(price if price else t["entry_price"]) for t in trades)


def _open(storage: Any, notional: float, price: float, now: int) -> int | None:
    fee = notional * _fee()
    cash = float(storage.get_portfolio_state()["cash"])
    if notional + fee > cash:
        notional = cash / (1 + _fee())
        fee = notional * _fee()
    if notional < float(getattr(config, "CORE_MIN_NOTIONAL", 10.0)):
        return None
    tid = storage.insert_paper_trade({
        "signal_id": None, "exit_price": None, "symbol": _symbol(), "direction": "LONG",
        "entry_price": price, "quantity": notional / price,
        "stop_loss": 0.0, "take_profit": BIG,          # never trigger
        "status": "open", "pnl": None, "opened_at": now, "closed_at": None,
        "strategy": STRATEGY, "style": STRATEGY, "atr_pct": None,
        "trail_price": price, "exit_reason": None, "fees": fee,
    })
    storage.update_portfolio_cash(cash - notional - fee)
    logger.info("core OPEN id=%s %s notional=%.2f @ %.2f", tid, _symbol(), notional, price)
    return tid


def _close(storage: Any, trade: dict[str, Any], price: float, now: int, reason: str) -> float:
    qty, entry = float(trade["quantity"]), float(trade["entry_price"])
    exit_fee = qty * price * _fee()
    gross = (price - entry) * qty
    pnl = gross - exit_fee - float(trade.get("fees") or 0.0)
    storage.update_paper_trade(trade["id"], {
        "status": "closed", "exit_price": price, "closed_at": now,
        "pnl": pnl, "exit_reason": reason, "fees": float(trade.get("fees") or 0.0) + exit_fee,
    })
    cash = float(storage.get_portfolio_state()["cash"])
    storage.update_portfolio_cash(cash + entry * qty + gross - exit_fee)
    logger.info("core CLOSE id=%s reason=%s pnl=%.2f", trade["id"], reason, pnl)
    return pnl


def committed_to_strategies(storage: Any, equity: float, cash: float, core: float) -> float:
    """Capital tied up by non-core positions (margin + their P&L)."""
    return max(0.0, equity - cash - core)


def run_core_cycle(storage: Any = None, now: int | None = None) -> dict[str, Any]:
    storage = storage or get_storage()
    now = int(now or time.time())
    trades = core_trades(storage)
    row = storage.get_latest_price(_symbol())
    if not row:
        return {"ok": False, "reason": f"no price for {_symbol()}"}
    price = float(row["close"])

    if not getattr(config, "CORE_ENABLED", False):
        for t in trades:
            _close(storage, t, price, now, "core_disabled")
        return {"ok": True, "enabled": False, "closed": len(trades)}

    from src.engine.paper_trader import PaperTrader

    summary = PaperTrader(storage).summary(price)
    equity = float(summary.get("equity") or 0.0)
    cash = float(storage.get_portfolio_state()["cash"])
    core = core_value(storage, price)
    committed = committed_to_strategies(storage, equity, cash, core)
    target = max(0.0, equity * float(getattr(config, "CORE_PCT", 0.97)) - committed)
    band = float(getattr(config, "CORE_BAND", 0.05)) * equity
    drift = target - core
    action = "hold"
    if drift > band or (core == 0 and target > 0):
        _open(storage, drift, price, now)
        action = "add"
    elif drift < -band:
        # trim: close the core and reopen at the smaller target
        for t in trades:
            _close(storage, t, price, now, "core_rebalance")
        _open(storage, target, price, now)
        action = "trim"
    return {"ok": True, "enabled": True, "action": action, "equity": round(equity, 2),
            "core_before": round(core, 2), "target": round(target, 2), "committed": round(committed, 2)}
