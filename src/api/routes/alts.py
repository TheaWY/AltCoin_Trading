"""Altcoin overview and analysis endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from src import config
from src.api.dashboard_data import build_alts_payload
from src.data.storage import get_storage
from src.engine.evaluation import evaluate_all
from src.engine.paper_trader import PaperTrader

router = APIRouter(prefix="/alts", tags=["alts"])


def _enrich_portfolio(payload: dict) -> dict:
    """Make portfolio PnL explicit even when the symbol is not in the current UI list.

    Older UI code inferred open-position PnL from `alts[].investment`. That breaks
    when the active symbol universe/ranking changes and an open trade is not in
    the first rendered list. The portfolio card should be driven by open trades
    directly, not by whether the coin card happens to be visible.
    """
    storage = get_storage()
    trader = PaperTrader(storage)
    open_trades = storage.get_open_trades()
    positions: list[dict] = []
    prices: dict[str, float] = {}

    for trade in open_trades:
        symbol = trade["symbol"]
        latest = storage.get_latest_price(symbol)
        current_price = float(latest["close"]) if latest else float(trade["entry_price"])
        prices[symbol] = current_price
        inv = trader.investment_for_symbol(symbol, current_price)
        inv["symbol"] = symbol
        inv["base"] = symbol.split("/")[0]
        positions.append(inv)

    btc = storage.get_latest_price(config.SYMBOL)
    btc_price = float(btc["close"]) if btc else None
    portfolio = trader.summary(btc_price)

    # Ensure totals are exactly the sum of current cash + open position values.
    # This makes the top portfolio card and the open position table agree.
    cash = float(portfolio.get("cash") or 0.0)
    open_value = sum(float(p.get("current_value") or 0.0) for p in positions)
    unrealized = sum(float(p.get("unrealized_pnl") or 0.0) for p in positions)
    realized = float(portfolio.get("total_realized_pnl") or 0.0)
    paper_value = cash + open_value
    starting = float(portfolio.get("starting_capital") or config.PAPER_STARTING_CAPITAL)
    portfolio.update(
        {
            "paper_value": paper_value,
            "equity": paper_value,
            "available_cash": cash,
            "open_trades": len(positions),
            "open_positions": positions,
            "total_invested_open": sum(float(p.get("invested") or 0.0) for p in positions),
            "reserved_margin": sum(float(p.get("invested") or 0.0) for p in positions),
            "total_open_value": open_value,
            "open_position_value": open_value,
            "total_unrealized_pnl": unrealized,
            "total_realized_pnl": realized,
            "pnl_vs_start": paper_value - starting,
        }
    )
    payload["portfolio"] = portfolio
    payload["open_positions"] = positions

    # Force open-position cards into evaluation/alts even if they are outside the
    # current ranked universe rendered by the dashboard.
    known_eval = {e.get("symbol") for e in payload.get("evaluation", [])}
    for pos in positions:
        if pos["symbol"] not in known_eval:
            payload.setdefault("evaluation", []).insert(
                0,
                {
                    "symbol": pos["symbol"],
                    "base": pos["base"],
                    "tradable": False,
                    "has_position": True,
                    "investment": pos,
                    "metrics": {"last_price": pos.get("current_price")},
                    "verdict": None,
                    "confidence": None,
                    "why_not": ["보유 중인 포지션 — 현재 유니버스 카드에 없어서 강제 표시"],
                },
            )
    known_alts = {a.get("symbol") for a in payload.get("alts", [])}
    for pos in positions:
        if pos["symbol"] not in known_alts:
            payload.setdefault("alts", []).insert(
                0,
                {
                    "symbol": pos["symbol"],
                    "base": pos["base"],
                    "price": pos.get("current_price"),
                    "pct_24h": None,
                    "direction": pos.get("direction"),
                    "worth_investing": False,
                    "recommended_style": None,
                    "confidence": None,
                    "has_position": True,
                    "investment": pos,
                    "total_pnl": pos.get("total_pnl"),
                },
            )
    return payload


@router.get("")
def list_alts() -> dict:
    return _enrich_portfolio(build_alts_payload())


@router.get("/evaluation")
def list_evaluation() -> dict:
    """단타/스윙 verdict + quant metrics per symbol."""
    return {"evaluation": evaluate_all()}
