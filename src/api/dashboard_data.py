"""Shared dashboard API payload for REST + WebSocket."""

from __future__ import annotations

from typing import Any

from src import config
from src.data.storage import Storage, get_storage
from src.engine.analyzer import AltAnalyzer
from src.engine.evaluation import evaluate_all
from src.engine.paper_trader import PaperTrader
from src.health import get_health
from src.symbols import trading_symbols


def build_alts_payload(storage: Storage | None = None) -> dict[str, Any]:
    storage = storage or get_storage()
    analyzer = AltAnalyzer(storage)
    trader = PaperTrader(storage)
    symbols = trading_symbols()

    alts = analyzer.analyze_all(symbols)
    holdings = trader.holdings_for_symbols(symbols)

    evaluation = evaluate_all(storage, symbols)
    for entry in evaluation:
        inv = holdings.get(entry["symbol"], {})
        entry["has_position"] = inv.get("status") == "open"
        entry["investment"] = inv if entry["has_position"] else {}

    for alt in alts:
        inv = holdings.get(alt["symbol"], {})
        alt["investment"] = inv
        alt["has_position"] = inv.get("status") == "open"
        alt["total_pnl"] = inv.get("total_pnl", 0)

    # Sort: open positions first, then by |total_pnl|, then worth investing
    alts.sort(
        key=lambda a: (
            0 if a["has_position"] else 1,
            -abs(a.get("total_pnl") or 0),
            0 if a["worth_investing"] else 1,
            -a["confidence"],
        )
    )

    holdings_list = [holdings[s] | {"symbol": s, "base": s.split("/")[0]} for s in symbols]
    holdings_list.sort(
        key=lambda h: (0 if h["status"] == "open" else 1, -abs(h.get("total_pnl") or 0))
    )

    btc = storage.get_latest_price(config.SYMBOL)
    btc_price = float(btc["close"]) if btc else None
    portfolio = trader.summary(btc_price) if btc_price else {}

    recommendations = [
        {
            "symbol": a["symbol"],
            "base": a["base"],
            "recommended_action": a["recommended_action"],
            "recommended_style": a["recommended_style"],
            "confidence": a["confidence"],
            "direction": a["direction"],
            "short_term": a["short_term"],
            "swing": a["swing"],
            "price": a["price"],
            "worth_investing": a["worth_investing"],
            "already_invested": a["has_position"],
        }
        for a in alts
        if a["worth_investing"] and not a["has_position"]
    ]

    return {
        "symbols_tracked": len(symbols),
        "evaluation": evaluation,
        "alts": alts,
        "holdings": holdings_list,
        "worth_investing_count": sum(1 for a in alts if a["worth_investing"]),
        "worth_investing": [a for a in alts if a["worth_investing"]],
        "recommendations": recommendations,
        "portfolio": portfolio,
        "open_positions": storage.get_open_trades(),
        "recent_trades": storage.get_recent_trades(20),
        "closed_trades": storage.get_recent_closed_trades(20),
        "accuracy": storage.get_signal_accuracy(config.SIGNAL_ACCURACY_ROLLING_DAYS),
        "health": get_health().get_status(),
    }
