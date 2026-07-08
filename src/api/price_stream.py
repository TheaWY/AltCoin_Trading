"""Live price relay — the server subscribes to the exchange once and pushes
compact price diffs to dashboard clients over the app's own /ws.

Browsers often cannot reach Binance directly (corporate networks, blocked
regions), so no exchange connection ever happens client-side. The upstream
source is the futures !miniTicker@arr websocket (all symbols, 1s updates)
with a REST poll fallback when the websocket is unreachable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

STREAM_URL = "wss://fstream.binance.com/ws/!miniTicker@arr"
REST_URL = "https://fapi.binance.com/fapi/v1/ticker/24hr"
BROADCAST_INTERVAL_SECONDS = 2.0
IDLE_SLEEP_SECONDS = 3.0
REST_POLL_SECONDS = 5.0
WS_RETRY_AFTER_SECONDS = 120.0


class PriceRelay:
    def __init__(self) -> None:
        # symbol -> [last_price, pct_24h]
        self.prices: dict[str, list[Any]] = {}
        self.dirty: set[str] = set()

    def ingest(self, tickers: list[dict[str, Any]]) -> None:
        for ticker in tickers:
            symbol = ticker.get("s") or ticker.get("symbol")
            if not symbol or not symbol.endswith("USDT"):
                continue
            try:
                close = float(ticker.get("c") or ticker.get("lastPrice"))
                open_24h = float(ticker.get("o") or ticker.get("openPrice"))
            except (TypeError, ValueError):
                continue
            pct = round((close - open_24h) / open_24h * 100, 2) if open_24h else None
            self.prices[symbol] = [close, pct]
            self.dirty.add(symbol)

    async def _stream_websocket(self, manager: Any) -> None:
        import websockets

        async with websockets.connect(
            STREAM_URL, ping_interval=20, max_size=2**22
        ) as ws:
            logger.info("Price relay: connected to upstream miniTicker stream")
            while True:
                if not manager.active:
                    return  # nobody watching — drop the upstream connection
                # The all-market stream pushes every second; a silent socket
                # means a half-open/blocked connection, so treat it as dead.
                raw = await asyncio.wait_for(ws.recv(), timeout=30)
                data = json.loads(raw)
                if isinstance(data, list):
                    self.ingest(data)

    def _rest_poll(self) -> list[dict[str, Any]]:
        import urllib.request

        request = urllib.request.Request(REST_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())

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
            batch = {symbol: self.prices[symbol] for symbol in self.dirty}
            self.dirty.clear()
            await manager.broadcast(
                {"type": "prices", "t": int(time.time()), "data": batch}
            )


relay = PriceRelay()
