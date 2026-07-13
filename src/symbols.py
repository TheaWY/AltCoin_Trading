"""Trading universe — spot symbols and ccxt futures mapping.

With SYMBOL_UNIVERSE=auto (default), the universe is every active Binance
USDT-margined crypto perpetual (`underlyingType=COIN`), discovered from
exchangeInfo and ranked by 24h dollar volume. TradFi perps such as equities,
ETFs, and commodities are intentionally excluded because they are not crypto
altcoins and have different trading sessions/risk profiles.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

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


def _exchange_info_symbols(exchange: Any) -> dict[str, dict[str, Any]]:
    """Return Binance raw exchangeInfo symbol metadata keyed by spot symbol.

    We prefer exchangeInfo over fetch_tickers() for universe definition because
    exchangeInfo exposes `underlyingType`; that lets us keep real crypto coins
    and exclude TradFi perps like SAMSUNG/USDT, NVDA/USDT, XAU/USDT, QQQ/USDT.
    """
    try:
        info = exchange.fapiPublicGetExchangeInfo()
    except AttributeError:
        # Older ccxt fallback: raw method naming changed in some versions.
        info = exchange.publicGetFapiV1ExchangeInfo()
    result: dict[str, dict[str, Any]] = {}
    for item in info.get("symbols", []):
        base = item.get("baseAsset")
        quote = item.get("quoteAsset")
        if not base or quote != "USDT":
            continue
        key = f"{base}/USDT"
        existing = result.get(key)
        if existing is None:
            result[key] = item
            continue
        # Binance lists PERPETUAL plus quarterly contracts for the same base.
        # Keep the perpetual row so majors like BTC/ETH stay in the universe.
        if item.get("contractType") == "PERPETUAL" and existing.get("contractType") != "PERPETUAL":
            result[key] = item
    return result


def _is_crypto_usdt_perp(meta: dict[str, Any]) -> bool:
    if meta.get("status") != "TRADING":
        return False
    if meta.get("contractType") != "PERPETUAL":
        return False
    if meta.get("quoteAsset") != "USDT" or meta.get("marginAsset") != "USDT":
        return False
    if meta.get("underlyingType") != "COIN":
        return False
    base = str(meta.get("baseAsset") or "")
    if base in STABLE_BASES:
        return False
    return True


def _discover_universe() -> list[str]:
    """All active Binance crypto USDT perps as spot symbols, by 24h volume desc."""
    from src.data.collectors.binance import _build_exchange

    exchange = _build_exchange(
        use_testnet=config.BINANCE_MARKET_DATA_TESTNET,
        authenticated=False,
    )
    meta_by_spot = _exchange_info_symbols(exchange)
    tickers = exchange.fetch_tickers()

    ranked: list[tuple[float, str]] = []
    for spot, meta in meta_by_spot.items():
        if not _is_crypto_usdt_perp(meta):
            continue
        market_symbol = f"{spot}:USDT"
        ticker = tickers.get(market_symbol) or tickers.get(spot) or {}
        volume = float(ticker.get("quoteVolume") or 0.0)
        ranked.append((volume, spot))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    symbols = [spot for _, spot in ranked]

    # BTC is not an alt, but the system needs it as benchmark/correlation anchor.
    if config.SYMBOL in symbols:
        symbols.remove(config.SYMBOL)
    symbols.insert(0, config.SYMBOL)

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
                logger.info("Crypto symbol universe discovered: %d USDT coin perpetuals", len(symbols))
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


def active_trading_symbols(universe: list[str] | None = None, limit: int | None = None) -> list[str]:
    """Top-N ranked symbols eligible for new-entry consideration this cycle.
    N defaults to config.ACTIVE_TRADING_SYMBOLS_LIMIT; pass `limit` to widen
    for a specific caller (e.g. orderbook collection) without changing the
    entry-eligible universe everyone else uses. Pass `universe` to avoid a
    redundant trading_symbols() call when the caller already has one (it's
    TTL-cached, so calling it again is cheap, but tests want an explicit
    list anyway)."""
    symbols = universe if universe is not None else trading_symbols()
    n = config.ACTIVE_TRADING_SYMBOLS_LIMIT if limit is None else limit
    return symbols if n <= 0 else symbols[:n]


def cycle_symbols(universe: list[str], storage: Any, limit: int | None = None) -> list[str]:
    """active_trading_symbols(universe, limit) plus any symbol with a
    currently open position -- the scope collected every cycle, and the
    scope the data-health gate checks (see health_gate_scope). A held
    position must keep receiving fresh candles for stops/trailing-stops
    even after it rotates out of the active-N universe.

    This union-with-open-positions step is exactly what
    positioning_collection_symbols() used to drop by re-slicing the result
    to a fixed N (an open position appended past that cutoff silently lost
    its OI/LSR feed). Callers that need a differently-sized scope should
    pass `limit` here, not re-slice the returned list themselves."""
    selected = list(active_trading_symbols(universe, limit=limit))
    seen = set(selected)
    for trade in storage.get_open_trades():
        symbol = trade.get("symbol")
        if symbol and symbol not in seen:
            selected.append(symbol)
            seen.add(symbol)
    return selected


BACKFILL_MIN_HOURS = 48


def symbols_in_backfill(storage: Any, symbols: list[str]) -> set[str]:
    """Symbols whose continuous 1h price collection began less than
    BACKFILL_MIN_HOURS ago (or hasn't started at all yet). Too little history
    to trust for entries, or to demand freshness from -- they may just have
    rotated into the universe for the first time and are still catching up.

    Known limitation: this uses MIN(timestamp) as a proxy for "collection
    just started." A symbol that rotates OUT and later back IN keeps its old
    MIN(timestamp), so it won't get a fresh grace period even if there's a
    gap in the middle. health_gate_scope's own scoping (excluding anything
    outside the active universe / open positions) already covers that case
    while the symbol is out; the residual risk is narrow (a stale gap right
    after rotating back in, before the next collection cycle catches up).
    """
    if not symbols:
        return set()
    first_seen = storage.get_symbols_first_seen(symbols)
    now = int(time.time())
    cutoff = BACKFILL_MIN_HOURS * 3600
    return {s for s, ts in first_seen.items() if ts is None or (now - ts) < cutoff}


def health_gate_scope(
    storage: Any, universe: list[str] | None = None
) -> tuple[set[str], set[str]]:
    """(scope, backfilling) for the data-health halt decision and the entry
    loop's backfill skip.

    scope = (active universe - backfilling) | open positions.
    An open position is ALWAYS in scope, regardless of backfill status --
    a live position's price feed staleness must always be able to halt new
    entries and must always be checkable for stops. Everything outside scope
    is still reported (freshness/data_gaps) but never halts.
    """
    active = set(active_trading_symbols(universe))
    open_positions = {
        trade.get("symbol") for trade in storage.get_open_trades() if trade.get("symbol")
    }
    backfilling = symbols_in_backfill(storage, sorted(active - open_positions))
    scope = (active - backfilling) | open_positions
    return scope, backfilling


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
