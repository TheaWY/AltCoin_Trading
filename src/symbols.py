"""Trading universe — spot symbols and ccxt futures mapping."""

from __future__ import annotations

from src import config

# Spot symbol -> Binance USDT perpetual (ccxt)
DEFAULT_SYMBOLS: dict[str, str] = {
    "BTC/USDT": "BTC/USDT:USDT",
    "ETH/USDT": "ETH/USDT:USDT",
    "SOL/USDT": "SOL/USDT:USDT",
    "BNB/USDT": "BNB/USDT:USDT",
    "XRP/USDT": "XRP/USDT:USDT",
    "DOGE/USDT": "DOGE/USDT:USDT",
    "ADA/USDT": "ADA/USDT:USDT",
    "AVAX/USDT": "AVAX/USDT:USDT",
    "LINK/USDT": "LINK/USDT:USDT",
    "DOT/USDT": "DOT/USDT:USDT",
}


def trading_symbols() -> list[str]:
    """Return ordered list of spot symbols to track and trade."""
    if config.TRADING_SYMBOLS:
        return list(config.TRADING_SYMBOLS)
    return list(DEFAULT_SYMBOLS.keys())


def ccxt_symbol(spot_symbol: str) -> str:
    """Map spot symbol (BTC/USDT) to ccxt perpetual symbol."""
    if spot_symbol in config.SYMBOL_CCXT_MAP:
        return config.SYMBOL_CCXT_MAP[spot_symbol]
    if spot_symbol in DEFAULT_SYMBOLS:
        return DEFAULT_SYMBOLS[spot_symbol]
    base = spot_symbol.split("/")[0]
    return f"{base}/USDT:USDT"


def base_asset(spot_symbol: str) -> str:
    return spot_symbol.split("/")[0]
