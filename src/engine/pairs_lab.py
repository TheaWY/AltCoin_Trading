"""Strategy LAB — run several pairs config variants in PARALLEL, each with its own
isolated book (cash + positions + equity curve), so their live track records
accumulate SEPARATELY and can be compared honestly (instead of resetting one book
every config change). A quant 'shadow book' horse-race.

All books share the SAME gate-passing signal core (src/engine/pairs.py) and the
same top-60 highvol selection (computed once/cycle); they differ only in the
capital knobs being compared: K (concentration), leverage (gross cap + margin),
and per-pair size. Fully isolated from the live pairs book (paper_trades /
portfolio_state) — uses its own lab_* tables.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import numpy as np

from src import config
from src.data.storage import get_storage
from src.engine import pairs, pairs_trader as ptr

logger = logging.getLogger(__name__)
HOUR = 3600
STARTING = float(getattr(config, "PAIRS_LAB_STARTING", config.PAPER_STARTING_CAPITAL))

# The variants to race. Shared universe/selection; differ on K / leverage / size.
BOOKS: dict[str, dict[str, Any]] = {
    "A_top60_2x":  {"k": 60, "gross": 2.0, "margin": 0.5, "per_pair": 0.09},
    "B_top60_1x":  {"k": 60, "gross": 1.0, "margin": 1.0, "per_pair": 0.09},
    "C_top10_2x":  {"k": 10, "gross": 2.0, "margin": 0.5, "per_pair": 0.09},
    "D_top30_2x":  {"k": 30, "gross": 2.0, "margin": 0.5, "per_pair": 0.09},
}


def ensure_schema(storage) -> None:
    pg = storage.is_postgres
    dp = "DOUBLE PRECISION" if pg else "REAL"
    bi = "BIGINT" if pg else "INTEGER"
    with storage._connect() as c:  # noqa: SLF001
        c.execute(f"CREATE TABLE IF NOT EXISTS lab_books (name TEXT PRIMARY KEY, "
                  f"config TEXT, cash {dp}, started_at {bi})")
        c.execute(
            "CREATE TABLE IF NOT EXISTS lab_positions ("
            f"id {'SERIAL PRIMARY KEY' if pg else 'INTEGER PRIMARY KEY AUTOINCREMENT'}, "
            f"book TEXT, symbol TEXT, hedge_symbol TEXT, direction TEXT, hedge_direction TEXT, "
            f"entry_price {dp}, hedge_entry_price {dp}, quantity {dp}, hedge_quantity {dp}, "
            f"hedge_beta {dp}, opened_at {bi}, closed_at {bi}, status TEXT, pnl {dp}, exit_reason TEXT)")
        c.execute(f"CREATE TABLE IF NOT EXISTS lab_equity (book TEXT, timestamp {bi}, "
                  f"equity {dp}, deployed_pct {dp}, open_n {bi})")


def _seed(storage, now: int) -> None:
    with storage._connect() as c:  # noqa: SLF001
        have = {dict(r)["name"] for r in c.execute("SELECT name FROM lab_books").fetchall()}
        ph = "%s" if storage.is_postgres else "?"
        for name, cfg in BOOKS.items():
            if name not in have:
                c.execute(f"INSERT INTO lab_books (name, config, cash, started_at) "
                          f"VALUES ({ph},{ph},{ph},{ph})", (name, json.dumps(cfg), STARTING, now))


def _open_positions(storage, book: str) -> list[dict[str, Any]]:
    with storage._connect() as c:  # noqa: SLF001
        rows = c.execute("SELECT * FROM lab_positions WHERE book = %s AND status='open'"
                         if storage.is_postgres else
                         "SELECT * FROM lab_positions WHERE book = ? AND status='open'",
                         (book,)).fetchall()
    return [dict(r) for r in rows]


def _gross(t: dict[str, Any]) -> float:
    return float(t["quantity"]) * float(t["entry_price"]) + \
        float(t["hedge_quantity"]) * float(t["hedge_entry_price"])


def _unrealized(storage, t: dict[str, Any]) -> float:
    pa = storage.get_latest_price(t["symbol"]); pb = storage.get_latest_price(t["hedge_symbol"])
    price_a = float(pa["close"]) if pa else float(t["entry_price"])
    price_b = float(pb["close"]) if pb else float(t["hedge_entry_price"])
    qa, ea = float(t["quantity"]), float(t["entry_price"])
    qb, eb = float(t["hedge_quantity"]), float(t["hedge_entry_price"])
    pnl = (price_a - ea) * qa if t["direction"] == "LONG" else (ea - price_a) * qa
    pnl += (price_b - eb) * qb if t["hedge_direction"] == "LONG" else (eb - price_b) * qb
    return pnl


def _equity(storage, book: str, cash: float, margin_frac: float) -> tuple[float, float, int]:
    op = _open_positions(storage, book)
    eq = cash; gross = 0.0
    for t in op:
        g = _gross(t); gross += g
        eq += g * margin_frac + _unrealized(storage, t)
    return eq, gross, len(op)


def run_lab_cycle(storage=None) -> dict[str, Any]:
    if not getattr(config, "PAIRS_LAB_ENABLED", False):
        return {"enabled": False}
    storage = storage or get_storage()
    now = int(time.time())
    ensure_schema(storage); _seed(storage, now)
    # shared selection: top-60 highvol pairs (same core the live book uses)
    selection = ptr.refresh_selection(storage, now)  # cached; K=config.MAX_CONCURRENT (>=60)
    sel_keys = {(p["a"], p["b"]) for p in selection}
    ph = "%s" if storage.is_postgres else "?"
    summary = {}
    for name, cfg in BOOKS.items():
        with storage._connect() as c:  # noqa: SLF001
            row = c.execute(f"SELECT cash FROM lab_books WHERE name={ph}", (name,)).fetchone()
        cash = float(dict(row)["cash"]) if row else STARTING
        margin_frac = cfg["margin"]
        opened = closed = 0
        # exits
        for t in _open_positions(storage, name):
            z = ptr._pair_z(storage, t["symbol"], t["hedge_symbol"], float(t["hedge_beta"]), now)
            hold_h = (now - int(t["opened_at"])) / HOUR
            key = (t["symbol"], t["hedge_symbol"])
            reason = None
            if pairs.should_close(z):
                reason = "z_revert"
            elif key not in sel_keys and hold_h >= pairs.TRADE_HOURS:
                reason = "deselected"
            elif hold_h >= pairs.TRADE_HOURS * 2:
                reason = "max_hold"
            if reason:
                cash = _close_lab(storage, t, now, reason, margin_frac, cash); closed += 1
        # entries: top-K of the shared selection, |z|>=2, gross cap
        open_keys = {(t["symbol"], t["hedge_symbol"]) for t in _open_positions(storage, name)}
        for p in selection[:cfg["k"]]:
            key = (p["a"], p["b"])
            if key in open_keys:
                continue
            eq, gross, _ = _equity(storage, name, cash, margin_frac)
            z = ptr._pair_z(storage, p["a"], p["b"], p["beta"], now)
            if not pairs.should_open(z):
                continue
            notional = eq * cfg["per_pair"]; hedge_notional = p["beta"] * notional
            margin = (notional + hedge_notional) * margin_frac
            if cash < margin or gross + notional + hedge_notional > cfg["gross"] * eq:
                continue
            cash = _open_lab(storage, name, p, z, now, notional, hedge_notional, margin, cash)
            opened += 1
        with storage._connect() as c:  # noqa: SLF001
            c.execute(f"UPDATE lab_books SET cash={ph} WHERE name={ph}", (cash, name))
            eq, gross, n = _equity(storage, name, cash, margin_frac)
            c.execute(f"INSERT INTO lab_equity (book,timestamp,equity,deployed_pct,open_n) "
                      f"VALUES ({ph},{ph},{ph},{ph},{ph})",
                      (name, now, eq, gross / eq * 100 if eq else 0, n))
        summary[name] = {"equity": round(eq, 2), "open": n, "opened": opened, "closed": closed}
    return {"enabled": True, "books": summary}


def _open_lab(storage, book, p, z, now, notional, hedge_notional, margin, cash) -> float:
    pa = storage.get_latest_price(p["a"]); pb = storage.get_latest_price(p["b"])
    if not pa or not pb:
        return cash
    price_a, price_b = float(pa["close"]), float(pb["close"])
    if price_a <= 0 or price_b <= 0:
        return cash
    pos = pairs.entry_side(z)
    dir_a = "LONG" if pos > 0 else "SHORT"; dir_b = "SHORT" if pos > 0 else "LONG"
    ph = "%s" if storage.is_postgres else "?"
    with storage._connect() as c:  # noqa: SLF001
        c.execute(f"INSERT INTO lab_positions (book,symbol,hedge_symbol,direction,hedge_direction,"
                  f"entry_price,hedge_entry_price,quantity,hedge_quantity,hedge_beta,opened_at,"
                  f"status) VALUES ({','.join([ph]*12)})",
                  (book, p["a"], p["b"], dir_a, dir_b, price_a, price_b,
                   notional / price_a, hedge_notional / price_b, p["beta"], now, "open"))
    return cash - margin


def _close_lab(storage, t, now, reason, margin_frac, cash) -> float:
    pa = storage.get_latest_price(t["symbol"]); pb = storage.get_latest_price(t["hedge_symbol"])
    price_a = float(pa["close"]) if pa else float(t["entry_price"])
    price_b = float(pb["close"]) if pb else float(t["hedge_entry_price"])
    qa, ea = float(t["quantity"]), float(t["entry_price"])
    qb, eb = float(t["hedge_quantity"]), float(t["hedge_entry_price"])
    pnl_a = (price_a - ea) * qa if t["direction"] == "LONG" else (ea - price_a) * qa
    pnl_b = (price_b - eb) * qb if t["hedge_direction"] == "LONG" else (eb - price_b) * qb
    hold_h = (now - int(t["opened_at"])) / HOUR
    short_notional = eb * qb if t["hedge_direction"] == "SHORT" else ea * qa
    pnl = pnl_a + pnl_b - hold_h * pairs.FUND_HR * short_notional - 4 * pairs.COST_LEG * (ea * qa)
    released = (ea * qa + eb * qb) * margin_frac
    ph = "%s" if storage.is_postgres else "?"
    with storage._connect() as c:  # noqa: SLF001
        c.execute(f"UPDATE lab_positions SET status='closed', closed_at={ph}, pnl={ph}, "
                  f"exit_reason={ph} WHERE id={ph}", (now, pnl, reason, t["id"]))
    return cash + released + pnl
