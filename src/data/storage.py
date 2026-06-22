"""SQLite read/write abstraction — swap this module to migrate to Postgres later."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Iterable

from src import config


SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(symbol, timestamp)
);

CREATE TABLE IF NOT EXISTS funding_rates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    funding_rate REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(symbol, timestamp)
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    direction TEXT NOT NULL,
    reason TEXT,
    entry_price REAL,
    funding_rate REAL,
    metadata TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL,
    quantity REAL NOT NULL,
    stop_loss REAL,
    take_profit REAL,
    status TEXT NOT NULL DEFAULT 'open',
    pnl REAL,
    opened_at INTEGER NOT NULL,
    closed_at INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (signal_id) REFERENCES signals(id)
);

CREATE TABLE IF NOT EXISTS market_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_price REAL NOT NULL,
    price_1h REAL,
    price_4h REAL,
    price_24h REAL,
    correct_1h INTEGER,
    correct_4h INTEGER,
    correct_24h INTEGER,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (signal_id) REFERENCES signals(id)
);

CREATE INDEX IF NOT EXISTS idx_prices_symbol_ts ON prices(symbol, timestamp);
CREATE INDEX IF NOT EXISTS idx_funding_symbol_ts ON funding_rates(symbol, timestamp);
CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(timestamp);
CREATE INDEX IF NOT EXISTS idx_paper_trades_status ON paper_trades(status);

CREATE TABLE IF NOT EXISTS portfolio_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cash REAL NOT NULL,
    benchmark_btc_price REAL,
    benchmark_started_at INTEGER,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Storage:
    """SQLite storage layer with a simple connection-per-operation pattern."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path or config.DATABASE_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    # --- prices ---

    def insert_prices(self, rows: Iterable[dict[str, Any]]) -> int:
        sql = """
            INSERT OR IGNORE INTO prices
                (symbol, timestamp, open, high, low, close, volume)
            VALUES
                (:symbol, :timestamp, :open, :high, :low, :close, :volume)
        """
        with self._connect() as conn:
            cursor = conn.executemany(sql, list(rows))
            return cursor.rowcount

    def get_latest_price(self, symbol: str) -> dict[str, Any] | None:
        sql = """
            SELECT * FROM prices
            WHERE symbol = ?
            ORDER BY timestamp DESC
            LIMIT 1
        """
        with self._connect() as conn:
            row = conn.execute(sql, (symbol,)).fetchone()
            return dict(row) if row else None

    def get_prices(
        self, symbol: str, limit: int = 100, since: int | None = None
    ) -> list[dict[str, Any]]:
        if since is not None:
            sql = """
                SELECT * FROM prices
                WHERE symbol = ? AND timestamp >= ?
                ORDER BY timestamp ASC
                LIMIT ?
            """
            params: tuple[Any, ...] = (symbol, since, limit)
        else:
            sql = """
                SELECT * FROM prices
                WHERE symbol = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """
            params = (symbol, limit)

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            result = [dict(r) for r in rows]
            if since is None:
                result.reverse()
            return result

    # --- funding rates ---

    def insert_funding_rates(self, rows: Iterable[dict[str, Any]]) -> int:
        sql = """
            INSERT OR IGNORE INTO funding_rates
                (symbol, timestamp, funding_rate)
            VALUES
                (:symbol, :timestamp, :funding_rate)
        """
        with self._connect() as conn:
            cursor = conn.executemany(sql, list(rows))
            return cursor.rowcount

    def get_latest_funding_rate(self, symbol: str) -> dict[str, Any] | None:
        sql = """
            SELECT * FROM funding_rates
            WHERE symbol = ?
            ORDER BY timestamp DESC
            LIMIT 1
        """
        with self._connect() as conn:
            row = conn.execute(sql, (symbol,)).fetchone()
            return dict(row) if row else None

    def get_funding_rates(
        self, symbol: str, limit: int = 100, since: int | None = None
    ) -> list[dict[str, Any]]:
        if since is not None:
            sql = """
                SELECT * FROM funding_rates
                WHERE symbol = ? AND timestamp >= ?
                ORDER BY timestamp ASC
                LIMIT ?
            """
            params: tuple[Any, ...] = (symbol, since, limit)
        else:
            sql = """
                SELECT * FROM funding_rates
                WHERE symbol = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """
            params = (symbol, limit)

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            result = [dict(r) for r in rows]
            if since is None:
                result.reverse()
            return result

    # --- signals ---

    def insert_signal(self, row: dict[str, Any]) -> int:
        sql = """
            INSERT INTO signals
                (strategy, symbol, timestamp, direction, reason,
                 entry_price, funding_rate, metadata)
            VALUES
                (:strategy, :symbol, :timestamp, :direction, :reason,
                 :entry_price, :funding_rate, :metadata)
        """
        with self._connect() as conn:
            cursor = conn.execute(sql, row)
            return int(cursor.lastrowid)

    def get_recent_signals(self, limit: int = 50) -> list[dict[str, Any]]:
        sql = """
            SELECT * FROM signals
            ORDER BY timestamp DESC
            LIMIT ?
        """
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, (limit,)).fetchall()]

    def get_latest_signal(
        self, symbol: str | None = None, strategy: str | None = None
    ) -> dict[str, Any] | None:
        clauses = []
        params: list[Any] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol)
        if strategy:
            clauses.append("strategy = ?")
            params.append(strategy)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT * FROM signals
            {where}
            ORDER BY timestamp DESC
            LIMIT 1
        """
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def get_signal(self, signal_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
            return dict(row) if row else None

    # --- paper trades ---

    def insert_paper_trade(self, row: dict[str, Any]) -> int:
        sql = """
            INSERT INTO paper_trades
                (signal_id, symbol, direction, entry_price, exit_price,
                 quantity, stop_loss, take_profit, status, pnl,
                 opened_at, closed_at)
            VALUES
                (:signal_id, :symbol, :direction, :entry_price, :exit_price,
                 :quantity, :stop_loss, :take_profit, :status, :pnl,
                 :opened_at, :closed_at)
        """
        with self._connect() as conn:
            cursor = conn.execute(sql, row)
            return int(cursor.lastrowid)

    def get_open_trades(self) -> list[dict[str, Any]]:
        sql = "SELECT * FROM paper_trades WHERE status = 'open' ORDER BY opened_at DESC"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql).fetchall()]

    def get_recent_trades(self, limit: int = 10) -> list[dict[str, Any]]:
        sql = """
            SELECT * FROM paper_trades
            ORDER BY opened_at DESC
            LIMIT ?
        """
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, (limit,)).fetchall()]

    def update_paper_trade(self, trade_id: int, fields: dict[str, Any]) -> None:
        if not fields:
            return
        columns = ", ".join(f"{key} = ?" for key in fields)
        sql = f"UPDATE paper_trades SET {columns} WHERE id = ?"
        with self._connect() as conn:
            conn.execute(sql, (*fields.values(), trade_id))

    # --- market outcomes ---

    def insert_market_outcome(self, row: dict[str, Any]) -> int:
        sql = """
            INSERT INTO market_outcomes
                (signal_id, symbol, direction, entry_price,
                 price_1h, price_4h, price_24h,
                 correct_1h, correct_4h, correct_24h)
            VALUES
                (:signal_id, :symbol, :direction, :entry_price,
                 :price_1h, :price_4h, :price_24h,
                 :correct_1h, :correct_4h, :correct_24h)
        """
        with self._connect() as conn:
            cursor = conn.execute(sql, row)
            return int(cursor.lastrowid)

    def get_market_outcome_by_signal(self, signal_id: int) -> dict[str, Any] | None:
        sql = "SELECT * FROM market_outcomes WHERE signal_id = ? LIMIT 1"
        with self._connect() as conn:
            row = conn.execute(sql, (signal_id,)).fetchone()
            return dict(row) if row else None

    def update_market_outcome(self, outcome_id: int, fields: dict[str, Any]) -> None:
        if not fields:
            return
        columns = ", ".join(f"{key} = ?" for key in fields)
        sql = f"UPDATE market_outcomes SET {fields and columns} WHERE id = ?"
        with self._connect() as conn:
            conn.execute(sql, (*fields.values(), outcome_id))

    def get_actionable_signals_without_outcomes(self) -> list[dict[str, Any]]:
        sql = """
            SELECT s.*
            FROM signals s
            LEFT JOIN market_outcomes mo ON mo.signal_id = s.id
            WHERE s.direction IN ('LONG', 'SHORT')
              AND mo.id IS NULL
            ORDER BY s.timestamp ASC
        """
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql).fetchall()]

    def get_incomplete_market_outcomes(self) -> list[dict[str, Any]]:
        sql = """
            SELECT mo.*, s.timestamp AS signal_timestamp
            FROM market_outcomes mo
            JOIN signals s ON s.id = mo.signal_id
            WHERE mo.price_24h IS NULL
               OR mo.price_4h IS NULL
               OR mo.price_1h IS NULL
            ORDER BY s.timestamp ASC
        """
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql).fetchall()]

    def get_price_at_or_after(self, symbol: str, timestamp: int) -> dict[str, Any] | None:
        sql = """
            SELECT * FROM prices
            WHERE symbol = ? AND timestamp >= ?
            ORDER BY timestamp ASC
            LIMIT 1
        """
        with self._connect() as conn:
            row = conn.execute(sql, (symbol, timestamp)).fetchone()
            return dict(row) if row else None

    # --- portfolio ---

    def init_portfolio_state(
        self, cash: float, benchmark_btc_price: float | None = None
    ) -> dict[str, Any]:
        sql = """
            INSERT OR IGNORE INTO portfolio_state
                (id, cash, benchmark_btc_price, benchmark_started_at, updated_at)
            VALUES (1, ?, ?, ?, datetime('now'))
        """
        started_at = int(datetime.now(timezone.utc).timestamp()) if benchmark_btc_price else None
        with self._connect() as conn:
            conn.execute(sql, (cash, benchmark_btc_price, started_at))
        state = self.get_portfolio_state()
        assert state is not None
        return state

    def get_portfolio_state(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM portfolio_state WHERE id = 1").fetchone()
            return dict(row) if row else None

    def update_portfolio_cash(self, cash: float) -> None:
        sql = """
            UPDATE portfolio_state
            SET cash = ?, updated_at = datetime('now')
            WHERE id = 1
        """
        with self._connect() as conn:
            conn.execute(sql, (cash,))

    def set_benchmark_price(self, price: float) -> None:
        sql = """
            UPDATE portfolio_state
            SET benchmark_btc_price = ?,
                benchmark_started_at = ?,
                updated_at = datetime('now')
            WHERE id = 1
        """
        ts = int(datetime.now(timezone.utc).timestamp())
        with self._connect() as conn:
            conn.execute(sql, (price, ts))

    def get_signal_accuracy(self, days: int = 7) -> dict[str, Any]:
        """Rolling accuracy over the given window (uses 24h correctness when available)."""
        sql = """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN correct_24h = 1 THEN 1 ELSE 0 END) AS correct
            FROM market_outcomes
            WHERE recorded_at >= datetime('now', ?)
              AND correct_24h IS NOT NULL
        """
        with self._connect() as conn:
            row = conn.execute(sql, (f"-{days} days",)).fetchone()
            total = int(row["total"] or 0)
            correct = int(row["correct"] or 0)
            accuracy = (correct / total * 100.0) if total > 0 else None
            return {"total": total, "correct": correct, "accuracy_pct": accuracy, "days": days}


def signal_row_from_result(
    strategy_name: str, signal: Any, timestamp: int, funding_rate: float | None = None
) -> dict[str, Any]:
    """Build a DB row dict from a Signal dataclass."""
    return {
        "strategy": strategy_name,
        "symbol": signal.symbol,
        "timestamp": timestamp,
        "direction": signal.direction.value,
        "reason": signal.reason,
        "entry_price": signal.entry_price,
        "funding_rate": funding_rate,
        "metadata": json.dumps(signal.metadata),
    }


# Module-level singleton for convenience
_default_storage: Storage | None = None


def get_storage() -> Storage:
    global _default_storage
    if _default_storage is None:
        _default_storage = Storage()
    return _default_storage
