"""Forced-liquidation collector -- the biggest data gap (research_decisions,
subject='liquidation_data'). A liquidation cascade is FORCED selling: price
falls because leverage is being closed out mechanically, not because anyone
revalued the asset -- the purest non-informational weakness, and we proved
weakness mean-reverts (+0.72%/72h, n=5,971). capitulation_bar is a crude
candle PROXY for this; here we collect the real thing.

CRITICAL: Binance REMOVED the historical liquidation REST endpoint
(fapi/v1/allForceOrders and futures/data/allForceOrders both return 404 as of
2026-07-15). Liquidations are FORWARD-ONLY via the !forceOrder@arr websocket
-- there is NO backfill. Collection starts the clock; like the orderbook,
this becomes testable in ~90 days. Every day not collecting is permanent loss.

Design mirrors the orderbook collector's contract (fault-isolated, never
raises into the caller) but the transport is a persistent websocket, not a
per-cycle poll -- a stream can't be sampled every 5 min without dropping the
events between samples. Run it as its own long-lived process
(ops/collect_liquidations.py); it buffers events, flushes raw rows, and rolls
up 1h aggregates. A crash/disconnect loses only the reconnect gap, never the
trading cycle.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

BINANCE_FORCE_ORDER_STREAM = "wss://fstream.binance.com/ws/!forceOrder@arr"

# Point-in-time guard: an event stamped meaningfully in the FUTURE (exchange or
# local clock skew, or a malformed message) would land in an hour bucket that
# has not closed yet and silently corrupt a feature computed as-of t. We reject
# anything past now + this skew rather than trust the wire timestamp.
_FUTURE_SKEW_S = 120


def ensure_schema(storage: Any) -> None:
    """Self-contained schema (same pattern as benchmarks/event_study) so this
    lands without touching the core migration."""
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute("""
            CREATE TABLE IF NOT EXISTS liquidations (
                symbol TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                qty REAL NOT NULL,
                notional REAL NOT NULL,
                UNIQUE(symbol, timestamp, side, price, qty)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS liquidation_agg_1h (
                symbol TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                long_liq_notional REAL NOT NULL DEFAULT 0,
                short_liq_notional REAL NOT NULL DEFAULT 0,
                liq_count INTEGER NOT NULL DEFAULT 0,
                largest_single_liq REAL NOT NULL DEFAULT 0,
                UNIQUE(symbol, timestamp)
            )
        """)


def parse_force_order(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Parse one !forceOrder@arr event into a liquidation row.

    Binance semantics (the subtle part): the event's order side is the side of
    the LIQUIDATION ORDER, which is OPPOSITE the position being liquidated. A
    forced SELL order liquidates a LONG position (long got liquidated =
    forced selling); a forced BUY liquidates a SHORT. We store `side` as the
    LIQUIDATED POSITION side ('long'/'short') -- what the signal cares about
    (forced selling = long liquidations) -- not the order side, to avoid the
    inversion that would flip every liq_imbalance signal.
    """
    o = msg.get("o") or msg
    try:
        symbol = o["s"]
        order_side = o["S"]  # SELL or BUY (the liquidation order)
        price = float(o.get("ap") or o.get("p") or 0.0)  # avg fill price preferred
        qty = float(o.get("q") or 0.0)
        ts = int(o.get("T") or msg.get("E") or 0) // 1000
    except (KeyError, TypeError, ValueError):
        return None
    if price <= 0 or qty <= 0 or ts <= 0:
        return None
    liquidated_side = "long" if order_side == "SELL" else "short"
    return {
        "symbol": _to_pair(symbol),
        "timestamp": ts,
        "side": liquidated_side,
        "price": price,
        "qty": qty,
        "notional": price * qty,
    }


def _to_pair(binance_symbol: str) -> str:
    """BTCUSDT -> BTC/USDT (match the rest of the schema's symbol format)."""
    if binance_symbol.endswith("USDT"):
        return f"{binance_symbol[:-4]}/USDT"
    return binance_symbol


def aggregate_1h(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Roll raw liquidation events into per-(symbol, hour-bucket) aggregates:
    long_liq_notional, short_liq_notional, liq_count, largest_single_liq.
    Pure function -- unit-testable without a websocket."""
    buckets: dict[tuple[str, int], dict[str, Any]] = {}
    for e in events:
        key = (e["symbol"], (e["timestamp"] // 3600) * 3600)
        b = buckets.setdefault(key, {
            "symbol": e["symbol"], "timestamp": key[1],
            "long_liq_notional": 0.0, "short_liq_notional": 0.0,
            "liq_count": 0, "largest_single_liq": 0.0,
        })
        if e["side"] == "long":
            b["long_liq_notional"] += e["notional"]
        else:
            b["short_liq_notional"] += e["notional"]
        b["liq_count"] += 1
        b["largest_single_liq"] = max(b["largest_single_liq"], e["notional"])
    return list(buckets.values())


def _reject_future(events: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
    """Point-in-time assertion for the stream: drop events stamped past
    now + skew. Returns the retained events; logs the count dropped."""
    cutoff = now + _FUTURE_SKEW_S
    kept = [e for e in events if e["timestamp"] <= cutoff]
    dropped = len(events) - len(kept)
    if dropped:
        logger.warning("liquidations: dropped %d future-dated event(s) (PIT guard)", dropped)
    return kept


def store_events(
    storage: Any,
    events: list[dict[str, Any]],
    now_fn: Callable[[], float] = time.time,
) -> int:
    """Persist raw events + upsert the 1h aggregates. INSERT OR IGNORE on the
    raw table dedupes exact repeats; aggregates are recomputed additively.
    A point-in-time guard drops any future-dated event first."""
    events = _reject_future(events, now_fn())
    if not events:
        return 0
    ensure_schema(storage)  # idempotent; makes store_events self-sufficient
    with storage._connect() as conn:  # noqa: SLF001
        conn.executemany(
            "INSERT OR IGNORE INTO liquidations "
            "(symbol,timestamp,side,price,qty,notional) VALUES "
            "(:symbol,:timestamp,:side,:price,:qty,:notional)",
            events,
        )
    for agg in aggregate_1h(events):
        with storage._connect() as conn:  # noqa: SLF001
            # additive upsert: fetch-existing + add, so a mid-hour flush and a
            # later flush in the same bucket accumulate rather than overwrite.
            row = conn.execute(
                "SELECT long_liq_notional,short_liq_notional,liq_count,largest_single_liq "
                "FROM liquidation_agg_1h WHERE symbol=? AND timestamp=?",
                (agg["symbol"], agg["timestamp"]),
            ).fetchone()
            if row:
                d = dict(row)
                agg["long_liq_notional"] += d["long_liq_notional"]
                agg["short_liq_notional"] += d["short_liq_notional"]
                agg["liq_count"] += d["liq_count"]
                agg["largest_single_liq"] = max(agg["largest_single_liq"], d["largest_single_liq"])
                conn.execute(
                    "UPDATE liquidation_agg_1h SET long_liq_notional=?,short_liq_notional=?,"
                    "liq_count=?,largest_single_liq=? WHERE symbol=? AND timestamp=?",
                    (agg["long_liq_notional"], agg["short_liq_notional"], agg["liq_count"],
                     agg["largest_single_liq"], agg["symbol"], agg["timestamp"]),
                )
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO liquidation_agg_1h "
                    "(symbol,timestamp,long_liq_notional,short_liq_notional,liq_count,largest_single_liq) "
                    "VALUES (?,?,?,?,?,?)",
                    (agg["symbol"], agg["timestamp"], agg["long_liq_notional"],
                     agg["short_liq_notional"], agg["liq_count"], agg["largest_single_liq"]),
                )
    return len(events)


async def run_liquidation_stream(storage: Any, flush_every: int = 50, stop_check=None) -> None:
    """Persistent consumer of !forceOrder@arr. Buffers events and flushes in
    batches. Reconnects on drop. Fault-isolated: any error is logged and the
    loop retries; it never raises into a caller. `stop_check()` (optional)
    returning True ends the loop cleanly (for tests / shutdown)."""
    import asyncio

    import websockets  # available (v16); imported lazily so import of this
    # module never requires the ws lib (keeps unit tests dependency-free).

    ensure_schema(storage)
    buffer: list[dict[str, Any]] = []
    while stop_check is None or not stop_check():
        try:
            async with websockets.connect(BINANCE_FORCE_ORDER_STREAM, ping_interval=20) as ws:
                logger.info("liquidation stream connected")
                async for raw in ws:
                    if stop_check is not None and stop_check():
                        break
                    try:
                        row = parse_force_order(json.loads(raw))
                    except Exception:
                        continue
                    if row:
                        buffer.append(row)
                    if len(buffer) >= flush_every:
                        store_events(storage, buffer)
                        buffer = []
        except Exception:
            logger.exception("liquidation stream error; reconnecting in 5s")
            await asyncio.sleep(5)
        finally:
            if buffer:
                store_events(storage, buffer)
                buffer = []
