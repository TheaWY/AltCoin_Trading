"""Permanent benchmark books -- run forever, in parallel with the strategy
book, so the dashboard comparison is impossible to avoid.

Four books, same cost model, same start date (portfolio_state's
benchmark_started_at, stamped when paper trading began):

  strategy  -- the actual paper (later: live) book; snapshotted here so all
               series share one clock.
  btc_hold  -- PAPER_STARTING_CAPITAL into BTC at the recorded start price,
               held. Pays entry fee + entry slippage once, via the same
               execution_cost model the strategy pays.
  alt_hold  -- equal-weight basket of the top 20 alts (by 24h dollar volume
               at start), held. Basket composition is persisted in
               benchmark_meta at first initialization and NEVER recomputed --
               otherwise the basket would silently drift.
  random    -- N_RANDOM_SEEDS parallel books running the ACTUAL PaperTrader
               class (identical sizing, stops, targets, trailing, cooldowns,
               slots, fees, slippage -- the same code, not a mirror of it),
               but entries are a RANDOM eligible symbol at a random eligible
               time, frequency-matched to the strategy book. Each seed's
               trades live in data/benchmarks/random_<seed>.db via a storage
               proxy (reads -> main DB, book writes -> seed DB), so rule #7
               (never write live paper tables) holds by construction.
               Dashboard reports the seed MEDIAN and the 25-75 band: a
               strategy inside that band is getting its returns from the
               risk management, not the signals.

Determinism: every random draw comes from random.Random(seed, cycle bucket)
-- re-running a cycle reproduces the same decisions. No wall-clock entropy.
"""

from __future__ import annotations

import json
import logging
import random as _random
import time
from typing import Any

from src import config
from src.data.storage import Storage
from src.engine import execution_cost

logger = logging.getLogger(__name__)

N_RANDOM_SEEDS = int(config.__dict__.get("BENCHMARK_RANDOM_SEEDS", 20))
ALT_BASKET_SIZE = 20
BENCHMARK_DIR = config.DATA_DIR / "benchmarks"

# Book-state tables that must live in the seed DB; everything else (prices,
# orderbook, funding, candles) reads from the main DB so every seed sees the
# exact same market.
_BOOK_METHODS = {
    "insert_paper_trade", "update_paper_trade", "get_open_trades",
    "get_open_trade_for_symbol", "count_open_trades", "get_recent_closed_trades",
    "get_all_trades_for_symbol", "get_recent_trades",
    "get_portfolio_state", "init_portfolio_state", "update_portfolio_cash",
    "set_benchmark_price", "insert_signal", "get_signal",
}


class BookStorage:
    """Routes book-state calls to the seed DB and market-data reads to the
    main DB. The random books run the real PaperTrader against this."""

    def __init__(self, market: Storage, book: Storage) -> None:
        self._market = market
        self._book = book

    def __getattr__(self, name: str) -> Any:
        target = self._book if name in _BOOK_METHODS else self._market
        return getattr(target, name)


def _ensure_schema(storage: Storage) -> None:
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute("""
            CREATE TABLE IF NOT EXISTS benchmark_equity (
                book TEXT NOT NULL,
                seed INTEGER NOT NULL DEFAULT -1,
                timestamp INTEGER NOT NULL,
                equity REAL NOT NULL,
                return_pct REAL NOT NULL,
                UNIQUE(book, seed, timestamp)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS benchmark_meta (
                book TEXT PRIMARY KEY,
                config_json TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)


def _get_meta(storage: Storage, book: str) -> dict[str, Any] | None:
    with storage._connect() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT config_json FROM benchmark_meta WHERE book = ?", (book,)
        ).fetchone()
    if not row:
        return None
    return json.loads(dict(row)["config_json"])


def _set_meta(storage: Storage, book: str, meta: dict[str, Any]) -> None:
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute(
            "INSERT OR IGNORE INTO benchmark_meta (book, config_json, created_at) VALUES (?, ?, ?)",
            (book, json.dumps(meta), int(time.time())),
        )


def _entry_cost_adjusted_qty(storage: Storage, symbol: str, price: float, notional: float) -> float:
    """Quantity bought with `notional` after paying one taker fee leg and
    entry slippage -- the SAME cost model the strategy book pays on entry."""
    fallback = execution_cost.symbol_fallback_stats(storage, symbol)
    spread_bps, depth_usd, _ = execution_cost.resolve_spread_and_depth(
        "buy", None, fallback["spread_bps"], fallback["depth_usd"]
    )
    slip = execution_cost.slippage_pct(notional, spread_bps, depth_usd)
    fill = execution_cost.adjusted_fill_price(price, "buy", slip)
    fee = notional * config.fee_pct_per_side()
    return (notional - fee) / fill


def _init_btc_hold(storage: Storage) -> dict[str, Any] | None:
    state = storage.get_portfolio_state()
    if not state or not state.get("benchmark_btc_price") or not state.get("benchmark_started_at"):
        return None
    start_price = float(state["benchmark_btc_price"])
    start_ts = int(state["benchmark_started_at"])
    capital = config.PAPER_STARTING_CAPITAL
    qty = _entry_cost_adjusted_qty(storage, config.SYMBOL, start_price, capital)
    meta = {"symbol": config.SYMBOL, "qty": qty, "start_price": start_price,
            "start_ts": start_ts, "capital": capital}
    _set_meta(storage, "btc_hold", meta)
    return meta


def _top_alts_at(storage: Storage, ts: int, n: int) -> list[str]:
    """Top-n alts by 24h dollar volume as of ts (deterministic, computed once
    at initialization and persisted -- never re-ranked)."""
    with storage._connect() as conn:  # noqa: SLF001
        rows = conn.execute(
            """
            SELECT symbol, SUM(close * volume) AS dv
            FROM prices
            WHERE timeframe = '1h' AND timestamp >= ? AND timestamp < ?
              AND symbol != ?
            GROUP BY symbol
            ORDER BY dv DESC
            LIMIT ?
            """,
            (ts - 86400, ts, config.SYMBOL, n),
        ).fetchall()
    return [dict(r)["symbol"] for r in rows]


def _price_at(storage: Storage, symbol: str, ts: int) -> float | None:
    rows = storage.get_prices(symbol, limit=1, before=ts, timeframe="1h")
    if not rows:
        return None
    return float(rows[-1]["close"])


def _init_alt_hold(storage: Storage) -> dict[str, Any] | None:
    state = storage.get_portfolio_state()
    if not state or not state.get("benchmark_started_at"):
        return None
    start_ts = int(state["benchmark_started_at"])
    symbols = _top_alts_at(storage, start_ts, ALT_BASKET_SIZE)
    if not symbols:
        return None
    capital = config.PAPER_STARTING_CAPITAL
    per_leg = capital / len(symbols)
    legs = {}
    for sym in symbols:
        price = _price_at(storage, sym, start_ts)
        if not price:
            continue
        legs[sym] = {"qty": _entry_cost_adjusted_qty(storage, sym, price, per_leg),
                     "start_price": price}
    if not legs:
        return None
    meta = {"legs": legs, "start_ts": start_ts, "capital": capital,
            "unallocated": capital - per_leg * len(legs)}
    _set_meta(storage, "alt_hold", meta)
    return meta


def _snapshot(storage: Storage, book: str, seed: int, ts: int, equity: float, capital: float) -> None:
    return_pct = (equity / capital - 1.0) * 100.0 if capital else 0.0
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute(
            "INSERT OR IGNORE INTO benchmark_equity (book, seed, timestamp, equity, return_pct) "
            "VALUES (?, ?, ?, ?, ?)",
            (book, seed, ts, equity, return_pct),
        )


def _hold_book_equity(storage: Storage, meta: dict[str, Any]) -> float:
    if "legs" in meta:
        total = float(meta.get("unallocated") or 0.0)
        for sym, leg in meta["legs"].items():
            row = storage.get_latest_price(sym)
            price = float(row["close"]) if row else leg["start_price"]
            total += leg["qty"] * price
        return total
    row = storage.get_latest_price(meta["symbol"])
    price = float(row["close"]) if row else meta["start_price"]
    return meta["qty"] * price


def _strategy_open_rate_per_cycle(storage: Storage, now_ts: int) -> float:
    """Strategy opens per cycle over the trailing 14 days -- the frequency
    the random books are matched to."""
    since = now_ts - 14 * 86400
    with storage._connect() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT COUNT(*) AS n, MIN(opened_at) AS first FROM paper_trades WHERE opened_at >= ?",
            (since,),
        ).fetchone()
    d = dict(row)
    n = int(d["n"] or 0)
    if n == 0 or not d["first"]:
        return 0.0
    span_s = max(now_ts - int(d["first"]), 3600)
    cycles = span_s / (config.COLLECTION_INTERVAL_MINUTES * 60)
    return n / max(cycles, 1.0)


def _strategy_direction_mix(storage: Storage) -> float:
    """P(LONG) from the strategy's recent trades; 0.5 default."""
    trades = storage.get_recent_trades(50)
    dirs = [t["direction"] for t in trades if t.get("direction") in ("LONG", "SHORT")]
    if not dirs:
        return 0.5
    return dirs.count("LONG") / len(dirs)


def _seed_storage(seed: int) -> Storage:
    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    return Storage(BENCHMARK_DIR / f"random_{seed}.db", database_url="")


def _run_random_book(
    main: Storage, seed: int, symbols: list[str], now_ts: int,
    open_rate: float, p_long: float, prices: dict[str, float],
) -> float:
    """One cycle of one random book through the REAL PaperTrader. Returns
    the book's current equity."""
    from src.engine.paper_trader import PaperTrader  # noqa: PLC0415 -- avoid import cycle

    book = _seed_storage(seed)
    storage = BookStorage(main, book)
    trader = PaperTrader(storage)
    trader.ensure_portfolio(prices.get(config.SYMBOL))

    # Exits first, same order as the live cycle: every open position gets
    # checked against the current price through the identical exit machinery.
    for trade in book.get_open_trades():
        price = prices.get(trade["symbol"])
        if price:
            trader.check_open_trades_for_symbol(trade["symbol"], price)

    # Entry draw: deterministic per (seed, cycle bucket).
    bucket = now_ts // (config.COLLECTION_INTERVAL_MINUTES * 60)
    rng = _random.Random(f"benchmark:{seed}:{bucket}")
    if open_rate > 0 and rng.random() < min(open_rate, 1.0):
        candidates = [s for s in symbols if prices.get(s)]
        if candidates:
            symbol = rng.choice(candidates)
            direction = "LONG" if rng.random() < p_long else "SHORT"
            if config.direction_allowed(direction):
                trader.process_signal(
                    {"symbol": symbol, "strategy": "random_benchmark",
                     "direction": direction, "reason": f"random seed={seed}",
                     "style": "scalp"},
                    prices[symbol],
                )

    summary = trader.summary(prices.get(config.SYMBOL))
    return float(summary.get("equity") or 0.0)


def run_benchmark_cycle(
    main: Storage,
    symbols: list[str],
    strategy_equity: float | None,
    now_ts: int | None = None,
) -> dict[str, Any]:
    """Called once per trading cycle, AFTER the strategy book updates.
    Snapshots all four books into benchmark_equity. Never raises -- a
    benchmark failure must not take the trading cycle down with it."""
    now_ts = now_ts or int(time.time())
    result: dict[str, Any] = {"snapshots": 0}
    try:
        _ensure_schema(main)
        capital = config.PAPER_STARTING_CAPITAL

        if strategy_equity is not None:
            _snapshot(main, "strategy", -1, now_ts, strategy_equity, capital)
            result["snapshots"] += 1

        btc_meta = _get_meta(main, "btc_hold") or _init_btc_hold(main)
        if btc_meta:
            _snapshot(main, "btc_hold", -1, now_ts, _hold_book_equity(main, btc_meta), capital)
            result["snapshots"] += 1

        alt_meta = _get_meta(main, "alt_hold") or _init_alt_hold(main)
        if alt_meta:
            _snapshot(main, "alt_hold", -1, now_ts, _hold_book_equity(main, alt_meta), capital)
            result["snapshots"] += 1

        prices: dict[str, float] = {}
        for sym in set(symbols) | {config.SYMBOL}:
            row = main.get_latest_price(sym)
            if row:
                prices[sym] = float(row["close"])

        open_rate = _strategy_open_rate_per_cycle(main, now_ts)
        p_long = _strategy_direction_mix(main)
        for seed in range(N_RANDOM_SEEDS):
            try:
                equity = _run_random_book(main, seed, symbols, now_ts, open_rate, p_long, prices)
                _snapshot(main, "random", seed, now_ts, equity, capital)
                result["snapshots"] += 1
            except Exception:  # noqa: BLE001 -- one bad seed must not stop the rest
                logger.exception("random benchmark seed %s failed", seed)
        result["open_rate"] = round(open_rate, 4)
    except Exception:  # noqa: BLE001
        logger.exception("benchmark cycle failed")
        result["error"] = True
    return result


def _percentile(sorted_vals: list[float], q: float) -> float | None:
    if not sorted_vals:
        return None
    idx = q * (len(sorted_vals) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def benchmark_summary(main: Storage, points: int = 200) -> dict[str, Any] | None:
    """Everything the dashboard needs: downsampled series per book, the
    random 25/50/75 band, latest returns, and the verdict chip."""
    try:
        _ensure_schema(main)
        with main._connect() as conn:  # noqa: SLF001
            rows = conn.execute(
                "SELECT book, seed, timestamp, return_pct FROM benchmark_equity ORDER BY timestamp ASC"
            ).fetchall()
        if not rows:
            return None
        rows = [dict(r) for r in rows]

        by_ts_random: dict[int, list[float]] = {}
        series: dict[str, list[tuple[int, float]]] = {"strategy": [], "btc_hold": [], "alt_hold": []}
        for r in rows:
            if r["book"] == "random":
                by_ts_random.setdefault(r["timestamp"], []).append(r["return_pct"])
            elif r["book"] in series:
                series[r["book"]].append((r["timestamp"], r["return_pct"]))

        band = []
        for ts in sorted(by_ts_random):
            vals = sorted(by_ts_random[ts])
            band.append((ts, _percentile(vals, 0.25), _percentile(vals, 0.50), _percentile(vals, 0.75)))

        def _downsample(seq: list) -> list:
            if len(seq) <= points:
                return seq
            step = len(seq) / points
            return [seq[int(i * step)] for i in range(points)] + [seq[-1]]

        latest = {k: (v[-1][1] if v else None) for k, v in series.items()}
        latest_band = band[-1] if band else (None, None, None, None)
        random_p25, random_p50, random_p75 = latest_band[1], latest_band[2], latest_band[3]

        strat, btc = latest.get("strategy"), latest.get("btc_hold")
        verdict, verdict_color = "데이터 수집 중", "muted"
        if strat is not None:
            below_btc = btc is not None and strat < btc
            in_band = (random_p25 is not None and random_p75 is not None
                       and random_p25 <= strat <= random_p75)
            if below_btc:
                verdict, verdict_color = "시장 대비 열위", "red"
            elif in_band:
                verdict, verdict_color = "랜덤과 구분 불가", "red"
            else:
                verdict, verdict_color = "시장 대비 우위", "green"

        return {
            "series": {k: _downsample(v) for k, v in series.items()},
            "random_band": _downsample(band),
            "latest": {**latest, "random_p25": random_p25, "random_p50": random_p50,
                       "random_p75": random_p75},
            "verdict": verdict,
            "verdict_color": verdict_color,
        }
    except Exception:  # noqa: BLE001
        logger.exception("benchmark summary failed")
        return None
