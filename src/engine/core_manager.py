"""Core allocation, now a daily trend-following swing book.

Capital that no strategy is using is split evenly across CORE_SYMBOLS
(default BTC and ETH). Each leg is held LONG only while that coin's last
completed daily close is above its CORE_TREND_MA-day average; below it the
leg's share waits in cash. With CORE_TREND_MA=0 every leg is always held
(the old "idle cash sits in BTC" core).

Why: scripts/swing_research.py tested swing rules and holding books on
2020-2026 daily data. Single-trade swing rules (breakouts, pullbacks,
squeezes) did not survive costs and the train/test split. What did is
simple time-series trend on the majors: BTC+ETH above/below the 50-day
average earned about the same as holding BTC in 2020-2024 with higher
Sharpe, and in the Sep 2024 - Sep 2026 test window +114% vs +43% for
BTC hold, with max drawdown -28% vs -53% (after 0.15%/side costs and
0.02%/day funding).

    leg_target = (equity * CORE_PCT - committed to strategies) / n_legs   if trend up
               = 0                                                        otherwise

Legs rebalance only when they drift more than CORE_BAND of equity per leg,
so fees do not churn. The trade rows keep strategy 'core_btc' (the
per-symbol exit path skips them); they exit only here.
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
DAY = 86400
_trend_cache: dict[tuple[str, int, int], dict[str, Any]] = {}


def _symbols() -> list[str]:
    raw = str(getattr(config, "CORE_SYMBOLS", "") or getattr(config, "CORE_SYMBOL", config.SYMBOL))
    return [s.strip() for s in raw.split(",") if s.strip()]


def _symbol() -> str:  # kept for callers that expect a single core symbol
    return _symbols()[0]


def _trend_ma() -> int:
    return int(getattr(config, "CORE_TREND_MA", 0) or 0)


def _fee() -> float:
    return float(getattr(config, "FEE_PCT_PER_SIDE", 0.0005))


def core_trades(storage: Any, symbol: str | None = None) -> list[dict[str, Any]]:
    return [t for t in storage.get_open_trades() if t.get("strategy") == STRATEGY
            and (symbol is None or t.get("symbol") == symbol)]


def _price(storage: Any, symbol: str) -> float | None:
    row = storage.get_latest_price(symbol)
    return float(row["close"]) if row else None


def core_value(storage: Any, price: float | None = None) -> float:
    total = 0.0
    single = len(_symbols()) == 1
    for t in core_trades(storage):
        px = price if (price is not None and single) else _price(storage, t["symbol"])
        total += float(t["quantity"]) * float(px if px else t["entry_price"])
    return total


def trend_state(storage: Any, symbol: str, ma: int, now: int | None = None) -> dict[str, Any]:
    """Is the last *completed* UTC daily close above its `ma`-day average?
    Built from the hourly price table; cached per symbol per day. Returns
    up=None when there is not enough history (the caller then holds)."""
    now = int(now or time.time())
    today = now // DAY * DAY
    key = (symbol, ma, today)
    if key in _trend_cache:
        return _trend_cache[key]
    since = today - (ma + 5) * DAY
    with storage._connect() as c:  # noqa: SLF001
        rows = c.execute(
            "SELECT timestamp, close FROM prices WHERE symbol = ? AND timeframe = '1h' "
            "AND timestamp >= ? AND timestamp < ? ORDER BY timestamp", (symbol, since, today)).fetchall()
    closes: dict[int, float] = {}
    for r in rows:
        r = dict(r)
        closes[int(r["timestamp"]) // DAY * DAY] = float(r["close"])  # last bar of each day wins
    days = sorted(closes)
    out: dict[str, Any] = {"symbol": symbol, "ma_days": ma, "up": None, "close": None, "ma": None,
                           "as_of": days[-1] if days else None}
    if len(days) >= ma:
        last = [closes[d] for d in days[-ma:]]
        out.update(close=last[-1], ma=sum(last) / ma, up=bool(last[-1] > sum(last) / ma))
    _trend_cache[key] = out
    return out


def _open(storage: Any, symbol: str, notional: float, price: float, now: int) -> int | None:
    fee = notional * _fee()
    cash = float(storage.get_portfolio_state()["cash"])
    if notional + fee > cash:
        notional = cash / (1 + _fee())
        fee = notional * _fee()
    if notional < float(getattr(config, "CORE_MIN_NOTIONAL", 10.0)):
        return None
    tid = storage.insert_paper_trade({
        "signal_id": None, "exit_price": None, "symbol": symbol, "direction": "LONG",
        "entry_price": price, "quantity": notional / price,
        "stop_loss": 0.0, "take_profit": BIG,          # never trigger
        "status": "open", "pnl": None, "opened_at": now, "closed_at": None,
        "strategy": STRATEGY, "style": STRATEGY, "atr_pct": None,
        "trail_price": price, "exit_reason": None, "fees": fee,
    })
    storage.update_portfolio_cash(cash - notional - fee)
    logger.info("core OPEN id=%s %s notional=%.2f @ %.2f", tid, symbol, notional, price)
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
    logger.info("core CLOSE id=%s %s reason=%s pnl=%.2f", trade["id"], trade.get("symbol"), reason, pnl)
    return pnl


def committed_to_strategies(storage: Any, equity: float, cash: float, core: float) -> float:
    """Capital tied up by non-core positions (margin + their P&L)."""
    return max(0.0, equity - cash - core)


def run_core_cycle(storage: Any = None, now: int | None = None) -> dict[str, Any]:
    storage = storage or get_storage()
    now = int(now or time.time())
    legs = _symbols()
    prices = {s: _price(storage, s) for s in legs}
    if not prices[legs[0]]:
        return {"ok": False, "reason": f"no price for {legs[0]}"}

    # legs dropped from CORE_SYMBOLS are closed
    for t in core_trades(storage):
        if t["symbol"] not in legs:
            px = _price(storage, t["symbol"]) or float(t["entry_price"])
            _close(storage, t, px, now, "core_leg_removed")

    if not getattr(config, "CORE_ENABLED", False):
        closed = 0
        for t in core_trades(storage):
            _close(storage, t, prices.get(t["symbol"]) or float(t["entry_price"]), now, "core_disabled")
            closed += 1
        return {"ok": True, "enabled": False, "closed": closed}

    from src.engine.paper_trader import PaperTrader

    summary = PaperTrader(storage).summary(prices[legs[0]])
    equity = float(summary.get("equity") or 0.0)
    cash = float(storage.get_portfolio_state()["cash"])
    core = core_value(storage, prices[legs[0]] if len(legs) == 1 else None)
    committed = committed_to_strategies(storage, equity, cash, core)
    # keep the signal book's and pump rider's allocation free even before they deploy
    from src.engine import pump_rider, signal_book

    committed = max(committed, equity * (signal_book.pct() + pump_rider.reserve_pct(storage)))
    total = max(0.0, equity * float(getattr(config, "CORE_PCT", 0.97)) - committed)
    per_leg = total / len(legs)
    band = float(getattr(config, "CORE_BAND", 0.05)) * equity / len(legs)
    ma = _trend_ma()
    actions: dict[str, Any] = {}
    # exits first so the cash they free funds the entries
    order = sorted(legs, key=lambda s: 0 if ma and trend_state(storage, s, ma, now)["up"] is False else 1)
    for sym in order:
        px = prices.get(sym)
        if not px:
            actions[sym] = "no_price"
            continue
        trend = trend_state(storage, sym, ma, now) if ma else {"up": True}
        up = trend["up"] is not False  # unknown history -> hold, like the old core
        target = per_leg if up else 0.0
        trades = core_trades(storage, sym)
        value = sum(float(t["quantity"]) * px for t in trades)
        drift = target - value
        if not up and trades:
            for t in trades:
                _close(storage, t, px, now, "trend_exit")
            actions[sym] = "trend_exit"
        elif target > 0 and (drift > band or (value == 0 and target > 0)):
            _open(storage, sym, drift, px, now)
            actions[sym] = "add" if value else ("trend_entry" if ma else "add")
        elif drift < -band:
            for t in trades:
                _close(storage, t, px, now, "core_rebalance")
            _open(storage, sym, target, px, now)
            actions[sym] = "trim"
        else:
            actions[sym] = "hold" if up else "flat"
    action = next((a for a in actions.values() if a not in ("hold", "flat")), "hold")
    return {"ok": True, "enabled": True, "action": action, "legs": actions, "equity": round(equity, 2),
            "core_before": round(core, 2), "target": round(total, 2), "committed": round(committed, 2),
            "trend": {s: trend_state(storage, s, ma, now) for s in legs} if ma else None}
