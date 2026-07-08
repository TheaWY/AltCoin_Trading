"""Live price relay — the server subscribes to the exchange once and pushes
compact price diffs to dashboard clients over the app's own /ws.

Browsers often cannot reach Binance directly (corporate networks, blocked
regions), so no exchange connection ever happens client-side.

Two upstream layers on one websocket connection:
- !miniTicker@arr — every symbol, 1s cadence (baseline + 24h % reference)
- <sym>@aggTrade — real per-trade ticks, but only for symbols dashboard
  clients report as visible via {"type":"watch"} messages (Upbit-style
  tick-by-tick updates without subscribing to the whole firehose)

A REST poll fallback covers environments where the websocket is silent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

STREAM_URL = "wss://fstream.binance.com/stream?streams=!miniTicker@arr"
REST_URL = "https://fapi.binance.com/fapi/v1/ticker/24hr"
BROADCAST_INTERVAL_SECONDS = 0.25
IDLE_SLEEP_SECONDS = 3.0
REST_POLL_SECONDS = 5.0
WS_RETRY_AFTER_SECONDS = 120.0
MAX_WATCHED_SYMBOLS = 400
SUBSCRIBE_CHUNK = 100


class PriceRelay:
    def __init__(self) -> None:
        # symbol -> [last_price, pct_24h]
        self.prices: dict[str, list[Any]] = {}
        self.open_24h: dict[str, float] = {}
        self.dirty: set[str] = set()
        # /ws client -> symbols it currently displays (drives aggTrade subs)
        self.watchers: dict[Any, set[str]] = {}

    # ----- client watch lists -----

    def set_watch(self, client: Any, symbols: list[Any]) -> None:
        cleaned = {
            s.upper().replace("/", "")
            for s in symbols
            if isinstance(s, str) and s.upper().endswith("USDT")
        }
        self.watchers[client] = set(sorted(cleaned)[:MAX_WATCHED_SYMBOLS])

    def remove_watch(self, client: Any) -> None:
        self.watchers.pop(client, None)

    def watch_union(self) -> set[str]:
        union: set[str] = set()
        for symbols in self.watchers.values():
            union |= symbols
        return set(sorted(union)[:MAX_WATCHED_SYMBOLS])

    # ----- price ingestion -----

    def _set_price(self, symbol: str, close: float) -> None:
        open_24h = self.open_24h.get(symbol)
        pct = round((close - open_24h) / open_24h * 100, 2) if open_24h else None
        previous = self.prices.get(symbol)
        if previous and previous[0] == close and previous[1] == pct:
            return
        self.prices[symbol] = [close, pct]
        self.dirty.add(symbol)

    def ingest(self, tickers: list[dict[str, Any]]) -> None:
        """miniTicker array (websocket) or 24hr ticker list (REST)."""
        for ticker in tickers:
            symbol = ticker.get("s") or ticker.get("symbol")
            if not symbol or not symbol.endswith("USDT"):
                continue
            try:
                close = float(ticker.get("c") or ticker.get("lastPrice"))
                open_24h = float(ticker.get("o") or ticker.get("openPrice"))
            except (TypeError, ValueError):
                continue
            if open_24h:
                self.open_24h[symbol] = open_24h
            self._set_price(symbol, close)

    def ingest_trade(self, trade: dict[str, Any]) -> None:
        symbol = trade.get("s")
        try:
            price = float(trade.get("p"))
        except (TypeError, ValueError):
            return
        if symbol:
            self._set_price(symbol, price)

    # ----- upstream websocket -----

    async def _sync_subscriptions(self, ws: Any, subscribed: set[str], next_id: list[int]) -> None:
        desired = self.watch_union()
        to_add = sorted(desired - subscribed)
        to_remove = sorted(subscribed - desired)
        for method, symbols in (("SUBSCRIBE", to_add), ("UNSUBSCRIBE", to_remove)):
            for i in range(0, len(symbols), SUBSCRIBE_CHUNK):
                chunk = symbols[i : i + SUBSCRIBE_CHUNK]
                await ws.send(
                    json.dumps(
                        {
                            "method": method,
                            "params": [f"{s.lower()}@aggTrade" for s in chunk],
                            "id": next_id[0],
                        }
                    )
                )
                next_id[0] += 1
        subscribed -= set(to_remove)
        subscribed |= set(to_add)

    async def _stream_websocket(self, manager: Any) -> None:
        import websockets

        async with websockets.connect(
            STREAM_URL, ping_interval=20, max_size=2**22
        ) as ws:
            logger.info("Price relay: connected to upstream stream")
            subscribed: set[str] = set()
            next_id = [1]
            while True:
                if not manager.active:
                    return  # nobody watching — drop the upstream connection
                await self._sync_subscriptions(ws, subscribed, next_id)
                # The all-market stream pushes every second; a silent socket
                # means a half-open/blocked connection, so treat it as dead.
                raw = await asyncio.wait_for(ws.recv(), timeout=30)
                message = json.loads(raw)
                if not isinstance(message, dict):
                    continue
                data = message.get("data")
                if isinstance(data, list):
                    self.ingest(data)
                elif isinstance(data, dict) and data.get("e") == "aggTrade":
                    self.ingest_trade(data)

    # ----- REST fallback -----

    def _rest_poll(self) -> list[dict[str, Any]]:
        import urllib.request

        request = urllib.request.Request(REST_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())

    # ----- main loops -----

    async def run(self, manager: Any) -> None:
        """Long-running task: keep prices flowing while dashboard clients exist."""
        broadcast_task = asyncio.create_task(self._broadcast_loop(manager))
        next_ws_attempt = 0.0
        try:
            while True:
                if not manager.active:
                    await asyncio.sleep(IDLE_SLEEP_SECONDS)
                    continue
                if time.monotonic() >= next_ws_attempt:
                    try:
                        await self._stream_websocket(manager)
                        continue
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.warning(
                            "Price relay websocket unavailable (%r) — REST fallback", exc
                        )
                        next_ws_attempt = time.monotonic() + WS_RETRY_AFTER_SECONDS
                try:
                    self.ingest(await asyncio.to_thread(self._rest_poll))
                except Exception as exc:
                    logger.warning("Price relay REST fallback failed: %s", exc)
                await asyncio.sleep(REST_POLL_SECONDS)
        except asyncio.CancelledError:
            pass
        finally:
            broadcast_task.cancel()

    async def _broadcast_loop(self, manager: Any) -> None:
        while True:
            await asyncio.sleep(BROADCAST_INTERVAL_SECONDS)
            if not manager.active or not self.dirty:
                continue
            watched = self.watch_union()
            symbols = (self.dirty & watched) if watched else self.dirty
            self.dirty = set()
            if not symbols:
                continue
            batch = {symbol: self.prices[symbol] for symbol in symbols}
            await manager.broadcast(
                {"type": "prices", "t": int(time.time()), "data": batch}
            )


relay = PriceRelay()
