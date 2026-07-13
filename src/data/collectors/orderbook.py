"""Orderbook snapshot collector — derived depth/imbalance/spread features.

Binance does not serve historical order-book snapshots the way it serves
candles or funding. This data cannot be backfilled: every cycle this
collector skips is a data point permanently lost, not a data point deferred.
Stores DERIVED features only (bid/ask depth within 1% of mid, imbalance,
spread) -- raw order books are large and nothing downstream needs them.

Mirrors src/data/collectors/positioning.py's shape (per-symbol collector
class + a run_*_collection(symbols, storage) entrypoint with per-symbol
fault isolation), but with no hourly dedup: unlike positioning (which
backfills up to ~30 days of REST history per call), an order-book snapshot
IS the current instant -- there is nothing to skip forward to.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src import config
from src.data.storage import Storage, get_storage

logger = logging.getLogger(__name__)

_DEPTH_PCT = 0.01  # within 1% of mid price
_ORDER_BOOK_LIMIT = 100

# 2026-07-13: measured 6.08s/21 symbols (0.29s/symbol) against the real
# exchange with zero rate-limit errors -- widened from 21 to 50 (est. ~15s,
# still comfortably inside the 5-min cycle budget) so the orderbook study
# has real breadth from day one. Do not widen further without measuring
# rate-limit headroom over a full day at this size first.
ORDERBOOK_SYMBOL_LIMIT = 50


def orderbook_collection_symbols(symbols: list[str]) -> list[str]:
    """No additional cap here, deliberately: the caller (cycle.py) passes
    src.symbols.cycle_symbols(universe, storage, limit=ORDERBOOK_SYMBOL_LIMIT)'s
    result -- top-50 union open positions. positioning.py's old pattern
    re-sliced the already-scoped list to a fixed N and could silently drop
    an open position that only made the list via the union-with-open-
    positions branch (appended past the slice point). Orderbook data can
    never be backfilled, so that gap matters more here: don't reproduce it.
    This function exists for API-shape parity with positioning.py and as
    the one place to widen scope later, deliberately, after measuring
    rate-limit headroom -- not to silently narrow what the caller already
    scoped correctly."""
    return symbols


def _derive_features(symbol: str, timestamp: int, book: dict[str, Any]) -> dict[str, Any] | None:
    """Pure: raw ccxt order book -> derived feature row, or None if the book
    is unusable. No I/O, so this is what the unit tests exercise directly."""
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    if not bids or not asks:
        return None

    top_bid = float(bids[0][0])
    top_ask = float(asks[0][0])
    if top_bid <= 0 or top_ask <= 0 or top_ask < top_bid:
        return None
    mid = (top_bid + top_ask) / 2.0

    bid_floor = mid * (1 - _DEPTH_PCT)
    ask_ceiling = mid * (1 + _DEPTH_PCT)
    bid_depth = sum(float(qty) for price, qty in bids if float(price) >= bid_floor)
    ask_depth = sum(float(qty) for price, qty in asks if float(price) <= ask_ceiling)

    total_depth = bid_depth + ask_depth
    imbalance = (bid_depth - ask_depth) / total_depth if total_depth > 0 else 0.0
    spread_bps = (top_ask - top_bid) / mid * 10_000 if mid > 0 else 0.0

    return {
        "symbol": symbol,
        "timestamp": timestamp,
        "bid_depth_1pct": round(bid_depth, 8),
        "ask_depth_1pct": round(ask_depth, 8),
        "imbalance_ratio": round(imbalance, 6),
        "spread_bps": round(spread_bps, 4),
        "top_bid": top_bid,
        "top_ask": top_ask,
        "mid_price": mid,
    }


class OrderbookCollector:
    """Fetches one order-book snapshot and stores its derived features."""

    def __init__(self, symbol: str, exchange: Any, storage: Storage | None = None) -> None:
        self.symbol = symbol
        self.exchange = exchange
        self.storage = storage or get_storage()

    def collect(self) -> int:
        from src.symbols import ccxt_symbol

        market_symbol = ccxt_symbol(self.symbol)
        book = self.exchange.fetch_order_book(market_symbol, limit=_ORDER_BOOK_LIMIT)
        # Our own capture time, in seconds -- deliberately not book["timestamp"]
        # (ccxt returns that in milliseconds; every other table in this
        # codebase stores seconds, and mixing units here was the exact bug
        # class that bit an earlier loader).
        timestamp = int(time.time())
        row = _derive_features(self.symbol, timestamp, book)
        if row is None:
            logger.warning("Unusable order book for %s -- skipped", self.symbol)
            return 0
        return self.storage.insert_orderbook_snapshots([row])


def run_orderbook_collection(
    symbols: list[str], storage: Storage | None = None, exchange: Any = None
) -> dict[str, int]:
    """Collect one order-book snapshot per active symbol this cycle.

    Failures are logged per symbol, never raised -- a collector error must
    never halt the trading cycle. Started at the active-union-open scope
    (~21 symbols); do not widen without re-measuring the per-cycle API cost
    against Binance's weight limits (fetch_order_book is heavier than
    fetch_ohlcv per call).
    """
    storage = storage or get_storage()
    if exchange is None:
        from src.data.collectors.binance import _build_exchange

        exchange = _build_exchange(use_testnet=config.BINANCE_MARKET_DATA_TESTNET, authenticated=False)

    target_symbols = orderbook_collection_symbols(symbols)
    results: dict[str, int] = {}
    for symbol in target_symbols:
        try:
            results[symbol] = OrderbookCollector(symbol, exchange, storage).collect()
        except Exception:
            logger.exception("Orderbook collection failed for %s", symbol)
            results[symbol] = 0
        time.sleep(0.1)  # stay well under exchange rate limits, matches positioning.py's margin
    return results
