#!/usr/bin/env python3
"""Live 1-minute kline stream for every USDT perp (launchd: com.altcoin.stream1m).

Subscribes to kline_1m on Binance's combined futures stream (200 symbols per
connection), stores each bar the moment it closes, and after every minute
hands control to the pump rider (src/engine/pump_rider.py) so detection and
exits run on fresh data within seconds of the bar close. Reconnects on any
error; refreshes the symbol list every 6h; prunes old bars daily.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.collectors import klines_1m as k1  # noqa: E402
from src.data.storage import get_storage  # noqa: E402

# Binance moved USD-M market-data streams under /market; the old /stream and
# /ws paths still accept the connection but deliver nothing.
WS = "wss://fstream.binance.com/market/stream?streams="
PER_CONN = 200
REFRESH_S = 6 * 3600

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("stream_1m")


class Buffer:
    def __init__(self) -> None:
        self.rows: list[tuple] = []
        self.last_minute = 0


async def consume(symbols: list[str], buf: Buffer, deadline: float) -> None:
    import websockets

    by_code = {k1.code(s).lower(): s for s in symbols}
    url = WS + "/".join(f"{c}@kline_1m" for c in by_code)
    while time.time() < deadline:
        try:
            async with websockets.connect(url, ping_interval=20, max_size=2 ** 22) as ws:
                log.info("connected %d symbols", len(symbols))
                async for raw in ws:
                    msg = json.loads(raw)
                    k = (msg.get("data") or {}).get("k") or {}
                    if k.get("x"):
                        sym = by_code.get(str(k.get("s", "")).lower())
                        if sym:
                            buf.rows.append(k1.row_from_ws(sym, k))
                    if time.time() >= deadline:
                        return
        except Exception as exc:  # noqa: BLE001
            log.warning("stream error (%s); reconnecting in 5s", exc)
            await asyncio.sleep(5)


async def flusher(storage, buf: Buffer, deadline: float) -> None:
    last_prune = 0.0
    while time.time() < deadline:
        await asyncio.sleep(2)
        minute = int(time.time()) // 60
        # bars for minute m-1 arrive within ~1-2s of the boundary; flush
        # a few seconds in, then run the rider once per minute
        if buf.rows and int(time.time()) % 60 >= 5:
            rows, buf.rows = buf.rows, []
            try:
                k1.insert_rows(storage, rows)
            except Exception:  # noqa: BLE001
                log.exception("insert failed; %d bars dropped", len(rows))
        if minute != buf.last_minute and int(time.time()) % 60 >= 6:
            buf.last_minute = minute
            try:
                from src.engine.pump_rider import run_minute

                await asyncio.to_thread(run_minute, storage)
            except Exception:  # noqa: BLE001
                log.exception("pump rider minute failed")
        if time.time() - last_prune > 86400:
            last_prune = time.time()
            try:
                await asyncio.to_thread(k1.prune, storage)
            except Exception:  # noqa: BLE001
                log.exception("prune failed")


async def main() -> None:
    storage = get_storage()
    k1.ensure_schema(storage)
    while True:
        symbols = k1.usdt_perps()
        deadline = time.time() + REFRESH_S
        buf = Buffer()
        chunks = [symbols[i:i + PER_CONN] for i in range(0, len(symbols), PER_CONN)]
        await asyncio.gather(flusher(storage, buf, deadline),
                             *(consume(c, buf, deadline) for c in chunks))


if __name__ == "__main__":
    asyncio.run(main())
