"""Dashboard summary endpoints."""

from fastapi import APIRouter

from src import config
from src.data.storage import get_storage
from src.health import get_health
from src.engine.paper_trader import PaperTrader

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard")
def get_dashboard() -> dict:
    storage = get_storage()
    latest_price = storage.get_latest_price(config.SYMBOL)
    latest_funding = storage.get_latest_funding_rate(config.SYMBOL)
    latest_signal = storage.get_latest_signal(symbol=config.SYMBOL)
    accuracy = storage.get_signal_accuracy(config.SIGNAL_ACCURACY_ROLLING_DAYS)

    current_price = float(latest_price["close"]) if latest_price else 0.0
    trader = PaperTrader(storage)
    portfolio = trader.summary(current_price) if current_price else {}

    prices = storage.get_prices(config.SYMBOL, limit=48)
    trades = storage.get_recent_trades(10)

    return {
        "symbol": config.SYMBOL,
        "price": current_price,
        "funding_rate": latest_funding["funding_rate"] if latest_funding else None,
        "funding_rate_pct": (
            float(latest_funding["funding_rate"]) * 100 if latest_funding else None
        ),
        "signal": latest_signal,
        "portfolio": portfolio,
        "accuracy": accuracy,
        "prices": prices,
        "trades": trades,
        "health": get_health().get_status(),
    }
