"""Trading universe — spot symbols and ccxt futures mapping.

With SYMBOL_UNIVERSE=auto (default) the universe is every active Binance
USDT perpetual, discovered via one fetch_tickers() call and ranked by 24h
dollar volume. The list is cached for UNIVERSE_TTL_SECONDS and falls back to
the static TRADING_SYMBOLS list whenever discovery fails, so the app never
breaks because of a discovery hiccup.
"""

from __future__ import annotations

import logging
import threading
import time

from src import config

logger = logging.getLogger(__name__)

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

# Stablecoin bases have no directional trade — excluded from discovery.
STABLE_BASES = {"USDC", "FDUSD", "TUSD", "DAI", "BUSD", "USDP", "EURI", "USD1", "AEUR"}

UNIVERSE_TTL_SECONDS = 6 * 3600

_universe_lock = threading.Lock()
_universe_cache: dict[str, object] = {"symbols": None, "fetched_at": 0.0}


def _discover_universe() -> list[str]:
    """All active Binance USDT perps as spot symbols, by 24h volume desc."""
    from src.data.collectors.binance import _build_exchange

    exchange = _build_exchange(
        use_testnet=config.BINANCE_MARKET_DATA_TESTNET,
        authenticated=False,
    )
    tickers = exchange.fetch_tickers()

    ranked: list[tuple[float, str]] = []
    for market_symbol, ticker in tickers.items():
        # unified perpetual symbols look like 'ETH/USDT:USDT'
        # (dated futures carry a '-YYMMDD' suffix and are skipped)
        if not market_symbol.endswith("/USDT:USDT"):
            continue
        spot = market_symbol.split(":")[0]
        base = spot.split("/")[0]
        if base in STABLE_BASES:
            continue
        volume = float(ticker.get("quoteVolume") or 0.0)
        ranked.append((volume, spot))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    symbols = [spot for _, spot in ranked]

    if config.SYMBOL in symbols:
        symbols.remove(config.SYMBOL)
    symbols.insert(0, config.SYMBOL)  # BTC always tracked (benchmark + correlation)

    if config.TRADING_SYMBOLS_LIMIT > 0:
        symbols = symbols[: config.TRADING_SYMBOLS_LIMIT]
    return symbols


def trading_symbols() -> list[str]:
    """Return ordered list of spot symbols to track and trade."""
    if config.SYMBOL_UNIVERSE != "auto":
        return list(config.TRADING_SYMBOLS) if config.TRADING_SYMBOLS else list(DEFAULT_SYMBOLS)

    now = time.monotonic()
    cached = _universe_cache["symbols"]
    if cached and now - float(_universe_cache["fetched_at"]) < UNIVERSE_TTL_SECONDS:
        return list(cached)

    with _universe_lock:
        cached = _universe_cache["symbols"]
        if cached and now - float(_universe_cache["fetched_at"]) < UNIVERSE_TTL_SECONDS:
            return list(cached)
        try:
            symbols = _discover_universe()
            if symbols:
                _universe_cache["symbols"] = symbols
                _universe_cache["fetched_at"] = now
                logger.info("Symbol universe discovered: %d USDT perpetuals", len(symbols))
                return list(symbols)
        except Exception:
            logger.warning(
                "Symbol discovery failed; falling back to static list", exc_info=True
            )
        if cached:  # serve stale universe rather than shrinking to the fallback
            return list(cached)
    return list(config.TRADING_SYMBOLS) if config.TRADING_SYMBOLS else list(DEFAULT_SYMBOLS)


def core_symbols() -> list[str]:
    """Symbols that get full multi-timeframe collection (the static list)."""
    return list(config.TRADING_SYMBOLS) if config.TRADING_SYMBOLS else list(DEFAULT_SYMBOLS)


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
