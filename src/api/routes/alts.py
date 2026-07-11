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

    Portfolio cards must be driven by open trades directly, not by whether the
    relevant coin card happens to be visible in the current ranked universe.

    Important accounting rule for the UI/API:
    - `equity` / `paper_value` means starting capital + realized PnL + current
      unrealized PnL, using latest prices.
    - `reserved_margin` / `total_invested_open` means entry notional currently
      allocated to open paper positions.
    - `available_cash` is a display-safe estimate and should not make the top
      equity card negative just because the internal cash ledger was distorted by
      older short-accounting logic.
    """
    storage = get_storage()
    trader = PaperTrader(storage)
    open_trades = storage.get_open_trades()
    positions: list[dict] = []

    for trade in open_trades:
        symbol = trade["symbol"]
        latest = storage.get_latest_price(symbol)
        current_price = float(latest["close"]) if latest else float(trade["entry_price"])
        inv = trader.investment_for_symbol(symbol, current_price)
        inv["symbol"] = symbol
        inv["base"] = symbol.split("/")[0]
        positions.append(inv)

    btc = storage.get_latest_price(config.SYMBOL)
    btc_price = float(btc["close"]) if btc else None
    portfolio = trader.summary(btc_price)

    reserved_margin = sum(float(p.get("invested") or 0.0) for p in positions)
    open_value = sum(float(p.get("current_value") or 0.0) for p in positions)
    unrealized = sum(float(p.get("unrealized_pnl") or 0.0) for p in positions)
    realized = float(portfolio.get("total_realized_pnl") or 0.0)
    starting = float(portfolio.get("starting_capital") or config.PAPER_STARTING_CAPITAL)

    # Equity is the reliable paper-account value. Do not derive it from raw cash
    # if older accounting has allowed cash to become negative for short trades.
    equity = starting + realized + unrealized
    available_cash = max(0.0, equity - reserved_margin)
    raw_cash = float(portfolio.get("cash") or 0.0)

    portfolio.update(
        {
            "paper_value": equity,
            "equity": equity,
            "cash": available_cash,
            "available_cash": available_cash,
            "raw_cash": raw_cash,
            "open_trades": len(positions),
            "open_positions": positions,
            "total_invested_open": reserved_margin,
            "reserved_margin": reserved_margin,
            "total_open_value": open_value,
            "open_position_value": open_value,
            "total_unrealized_pnl": unrealized,
            "total_realized_pnl": realized,
            "pnl_vs_start": equity - starting,
            "accounting_note": (
                "equity=starting_capital+realized_pnl+unrealized_pnl; "
                "raw_cash retained separately for audit"
            ),
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
