"""Storage layer — SQLite by default, Postgres when DATABASE_URL is set.

All queries are written once in SQLite style; `_translate_sql` rewrites the
placeholders and the handful of dialect-specific expressions for Postgres so
the rest of the codebase never needs to care which backend is active.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
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
    timeframe TEXT NOT NULL DEFAULT '1h',
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(symbol, timestamp, timeframe)
);

CREATE TABLE IF NOT EXISTS funding_rates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    funding_rate REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(symbol, timestamp)
);

CREATE TABLE IF NOT EXISTS long_short_ratio (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    ratio REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(symbol, timestamp)
);

CREATE TABLE IF NOT EXISTS open_interest (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    open_interest REAL NOT NULL,
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
    strategy TEXT,
    style TEXT,
    atr_pct REAL,
    trail_price REAL,
    exit_reason TEXT,
    fees REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (signal_id) REFERENCES signals(id)
);

CREATE TABLE IF NOT EXISTS market_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    open_interest REAL,
    open_interest_usd REAL,
    long_short_ratio REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(symbol, timestamp)
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

PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timestamp BIGINT NOT NULL,
    timeframe TEXT NOT NULL DEFAULT '1h',
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(symbol, timestamp, timeframe)
);

CREATE TABLE IF NOT EXISTS funding_rates (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timestamp BIGINT NOT NULL,
    funding_rate DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(symbol, timestamp)
);

CREATE TABLE IF NOT EXISTS long_short_ratio (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timestamp BIGINT NOT NULL,
    ratio DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(symbol, timestamp)
);

CREATE TABLE IF NOT EXISTS open_interest (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timestamp BIGINT NOT NULL,
    open_interest DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(symbol, timestamp)
);

CREATE TABLE IF NOT EXISTS signals (
    id BIGSERIAL PRIMARY KEY,
    strategy TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timestamp BIGINT NOT NULL,
    direction TEXT NOT NULL,
    reason TEXT,
    entry_price DOUBLE PRECISION,
    funding_rate DOUBLE PRECISION,
    metadata TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id BIGSERIAL PRIMARY KEY,
    signal_id BIGINT REFERENCES signals(id),
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    exit_price DOUBLE PRECISION,
    quantity DOUBLE PRECISION NOT NULL,
    stop_loss DOUBLE PRECISION,
    take_profit DOUBLE PRECISION,
    status TEXT NOT NULL DEFAULT 'open',
    pnl DOUBLE PRECISION,
    opened_at BIGINT NOT NULL,
    closed_at BIGINT,
    strategy TEXT,
    style TEXT,
    atr_pct DOUBLE PRECISION,
    trail_price DOUBLE PRECISION,
    exit_reason TEXT,
    fees DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS strategy TEXT;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS style TEXT;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS atr_pct DOUBLE PRECISION;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS trail_price DOUBLE PRECISION;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS exit_reason TEXT;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS fees DOUBLE PRECISION;

CREATE TABLE IF NOT EXISTS market_metrics (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timestamp BIGINT NOT NULL,
    open_interest DOUBLE PRECISION,
    open_interest_usd DOUBLE PRECISION,
    long_short_ratio DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(symbol, timestamp)
);

CREATE TABLE IF NOT EXISTS market_outcomes (
    id BIGSERIAL PRIMARY KEY,
    signal_id BIGINT NOT NULL REFERENCES signals(id),
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    price_1h DOUBLE PRECISION,
    price_4h DOUBLE PRECISION,
    price_24h DOUBLE PRECISION,
    correct_1h INTEGER,
    correct_4h INTEGER,
    correct_24h INTEGER,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_prices_symbol_timeframe_ts ON prices(symbol, timeframe, timestamp);
CREATE INDEX IF NOT EXISTS idx_funding_symbol_ts ON funding_rates(symbol, timestamp);
CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(timestamp);
CREATE INDEX IF NOT EXISTS idx_paper_trades_status ON paper_trades(status);

CREATE TABLE IF NOT EXISTS portfolio_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cash DOUBLE PRECISION NOT NULL,
    benchmark_btc_price DOUBLE PRECISION,
    benchmark_started_at BIGINT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_NAMED_PARAM_RE = re.compile(r"(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)")


def _translate_sql(sql: str) -> str:
    """Rewrite a SQLite-style statement for Postgres/psycopg."""
    add_on_conflict = "INSERT OR IGNORE INTO" in sql
    sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    sql = sql.replace("REAL", "DOUBLE PRECISION")
    sql = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO")
    sql = sql.replace("datetime('now', ?)", "(now() + (?)::interval)")
    sql = sql.replace("datetime('now')", "now()")
    sql = _NAMED_PARAM_RE.sub(r"%(\1)s", sql)
    sql = sql.replace("?", "%s")
    if add_on_conflict:
        sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return sql


class _ExecManyResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _Connection:
    """Uniform execute/executemany over sqlite3 and psycopg connections."""

    def __init__(self, raw: Any, is_postgres: bool) -> None:
        self.raw = raw
        self.is_postgres = is_postgres

    def _sql(self, sql: str) -> str:
        return _translate_sql(sql) if self.is_postgres else sql

    def execute(self, sql: str, params: Any = ()) -> Any:
        return self.raw.execute(self._sql(sql), params)

    def executemany(self, sql: str, rows: Any) -> Any:
        if self.is_postgres:
            # psycopg's executemany does not report affected rows reliably;
            # loop with execute (auto-prepared after a few runs) and sum.
            translated = self._sql(sql)
            affected = 0
            cursor = None
            for row in rows:
                cursor = self.raw.execute(translated, row)
                affected += max(cursor.rowcount, 0)
            return _ExecManyResult(affected)
        return self.raw.executemany(sql, rows)

    def insert_returning_id(self, sql: str, params: Any) -> int:
        if self.is_postgres:
            cursor = self.execute(sql.rstrip().rstrip(";") + " RETURNING id", params)
            return int(cursor.fetchone()["id"])
        return int(self.execute(sql, params).lastrowid)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Storage:
    """Connection-per-operation storage over SQLite or Postgres."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        database_url: str | None = None,
    ) -> None:
        self.database_url = (
            database_url if database_url is not None else config.DATABASE_URL
        )
        self.is_postgres = bool(self.database_url)
        self._pg_pool: Any = None
        if not self.is_postgres:
            self.db_path = Path(db_path or config.DATABASE_PATH)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _pool(self) -> Any:
        """Lazily created psycopg connection pool (per-op connects are too slow)."""
        if self._pg_pool is None:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool

            self._pg_pool = ConnectionPool(
                self.database_url,
                min_size=1,
                max_size=5,
                kwargs={"row_factory": dict_row},
                open=True,
            )
        return self._pg_pool

    @contextmanager
    def _connect(self) -> Generator[_Connection, None, None]:
        if self.is_postgres:
            # the pool's context manager commits on success / rolls back on error
            with self._pool().connection() as conn:
                yield _Connection(conn, True)
            return

        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # WAL: one writer + many readers concurrently. Without this, a bulk
        # backfill locks the DB and blocks the live worker's writes (stale
        # prices -> dashboard Offline -> stalled position management).
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA synchronous = NORMAL")
        try:
            yield _Connection(conn, False)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        if self.is_postgres:
            with self._connect() as conn:
                for statement in PG_SCHEMA.split(";"):
                    if statement.strip():
                        conn.raw.execute(statement)
            return

        with self._connect() as conn:
            conn.raw.executescript(SCHEMA)
            self._migrate_prices_timeframe(conn.raw)
            self._migrate_paper_trades_columns(conn.raw)
            conn.raw.execute("DROP INDEX IF EXISTS idx_prices_symbol_ts")
            conn.raw.execute(
                "CREATE INDEX IF NOT EXISTS idx_prices_symbol_timeframe_ts "
                "ON prices(symbol, timeframe, timestamp)"
            )

    _PAPER_TRADE_NEW_COLUMNS = {
        "strategy": "TEXT",
        "style": "TEXT",
        "atr_pct": "REAL",
        "trail_price": "REAL",
        "exit_reason": "TEXT",
        "fees": "REAL",
    }

    def _migrate_paper_trades_columns(self, conn: sqlite3.Connection) -> None:
        existing = {
            row["name"] for row in conn.execute("PRAGMA table_info(paper_trades)").fetchall()
        }
        for column, column_type in self._PAPER_TRADE_NEW_COLUMNS.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE paper_trades ADD COLUMN {column} {column_type}")

    def _migrate_prices_timeframe(self, conn: sqlite3.Connection) -> None:
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(prices)").fetchall()]
        unique_columns: list[str] | None = None
        for index in conn.execute("PRAGMA index_list(prices)").fetchall():
            if not index["unique"]:
                continue
            info = conn.execute(f"PRAGMA index_info({index['name']})").fetchall()
            names = [row["name"] for row in info]
            if names in (["symbol", "timestamp"], ["symbol", "timestamp", "timeframe"]):
                unique_columns = names
                break

        if "timeframe" in columns and unique_columns == ["symbol", "timestamp", "timeframe"]:
            return

        timeframe_expr = "COALESCE(timeframe, '1h')" if "timeframe" in columns else "'1h'"
        conn.execute("ALTER TABLE prices RENAME TO prices_old")
        conn.execute(
            """
            CREATE TABLE prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                timeframe TEXT NOT NULL DEFAULT '1h',
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(symbol, timestamp, timeframe)
            )
            """
        )
        conn.execute(
            f"""
            INSERT OR IGNORE INTO prices
                (id, symbol, timestamp, timeframe, open, high, low, close, volume, created_at)
            SELECT
                id, symbol, timestamp, {timeframe_expr}, open, high, low, close, volume, created_at
            FROM prices_old
            """
        )
        conn.execute("DROP TABLE prices_old")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_prices_symbol_timeframe_ts "
            "ON prices(symbol, timeframe, timestamp)"
        )

    # --- prices ---

    def insert_prices(self, rows: Iterable[dict[str, Any]], timeframe: str = "1h") -> int:
        prepared_rows = []
        for row in rows:
            prepared = dict(row)
            prepared.setdefault("timeframe", timeframe)
            prepared_rows.append(prepared)
        if not prepared_rows:
            return 0
        sql = """
            INSERT INTO prices
                (symbol, timestamp, timeframe, open, high, low, close, volume)
            VALUES
                (:symbol, :timestamp, :timeframe, :open, :high, :low, :close, :volume)
            ON CONFLICT(symbol, timestamp, timeframe) DO UPDATE SET
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume = excluded.volume,
                created_at = datetime('now')
        """
        with self._connect() as conn:
            cursor = conn.executemany(sql, prepared_rows)
            return cursor.rowcount

    def get_latest_price(self, symbol: str, timeframe: str = "1h") -> dict[str, Any] | None:
        sql = """
            SELECT * FROM prices
            WHERE symbol = ? AND timeframe = ?
            ORDER BY timestamp DESC
            LIMIT 1
        """
        with self._connect() as conn:
            row = conn.execute(sql, (symbol, timeframe)).fetchone()
            return dict(row) if row else None

    def get_prices(
        self,
        symbol: str,
        limit: int = 100,
        since: int | None = None,
        before: int | None = None,
        timeframe: str = "1h",
    ) -> list[dict[str, Any]]:
        clauses = ["symbol = ?", "timeframe = ?"]
        params: list[Any] = [symbol, timeframe]
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since)
        if before is not None:
            clauses.append("timestamp <= ?")
            params.append(before)

        if since is not None:
            order = "ASC"
            reverse_result = False
        else:
            order = "DESC"
            reverse_result = True

        sql = f"""
            SELECT * FROM prices
            WHERE {' AND '.join(clauses)}
            ORDER BY timestamp {order}
            LIMIT ?
        """
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            result = [dict(r) for r in rows]
            if reverse_result:
                result.reverse()
            return result

    def cleanup_old_prices(self) -> dict[str, int]:
        now_ts = int(datetime.now(timezone.utc).timestamp())
        policies = {
            "15m": 14 * 24 * 60 * 60,
            # Research walk-forward/backfill needs multi-year 1h history.
            "1h": 6 * 365 * 24 * 60 * 60,
        }
        deleted: dict[str, int] = {}
        with self._connect() as conn:
            for timeframe, max_age_seconds in policies.items():
                cutoff = now_ts - max_age_seconds
                cursor = conn.execute(
                    "DELETE FROM prices WHERE timeframe = ? AND timestamp < ?",
                    (timeframe, cutoff),
                )
                deleted[timeframe] = cursor.rowcount
        return deleted

    def get_volume_stats(
        self, symbol: str, timeframe: str = "1h", lookback: int = 24
    ) -> dict[str, float | int | None]:
        rows = self.get_prices(symbol, limit=lookback, timeframe=timeframe)
        volumes = [float(row["volume"]) for row in rows]
        if not volumes:
            return {"avg": None, "stddev": None, "count": 0}

        avg = sum(volumes) / len(volumes)
        variance = sum((volume - avg) ** 2 for volume in volumes) / len(volumes)
        return {
            "avg": avg,
            "stddev": math.sqrt(variance),
            "count": len(volumes),
        }

    # --- funding rates ---

    def set_system_status(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS system_status ("
                "key TEXT PRIMARY KEY, value TEXT, updated_at BIGINT)"
            )
            if self.is_postgres:
                conn.execute(
                    "INSERT INTO system_status (key, value, updated_at) "
                    "VALUES (%s, %s, %s) ON CONFLICT (key) DO UPDATE SET "
                    "value = EXCLUDED.value, updated_at = EXCLUDED.updated_at",
                    (key, value, int(time.time())),
                )
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO system_status (key, value, updated_at) "
                    "VALUES (?, ?, ?)",
                    (key, value, int(time.time())),
                )

    def get_system_status(self, key: str) -> dict[str, Any] | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT value, updated_at FROM system_status WHERE key = ?"
                    if not self.is_postgres
                    else "SELECT value, updated_at FROM system_status WHERE key = %s",
                    (key,),
                ).fetchone()
                return dict(row) if row else None
        except Exception:
            return None  # table may not exist yet on first boot

    def insert_funding_rates(self, rows: Iterable[dict[str, Any]]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        sql = """
            INSERT OR IGNORE INTO funding_rates
                (symbol, timestamp, funding_rate)
            VALUES
                (:symbol, :timestamp, :funding_rate)
        """
        with self._connect() as conn:
            cursor = conn.executemany(sql, rows)
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
        self,
        symbol: str,
        limit: int = 100,
        since: int | None = None,
        before: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["symbol = ?"]
        params: list[Any] = [symbol]
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since)
        if before is not None:
            clauses.append("timestamp <= ?")
            params.append(before)

        if since is not None:
            order = "ASC"
            reverse_result = False
        else:
            order = "DESC"
            reverse_result = True

        sql = f"""
            SELECT * FROM funding_rates
            WHERE {' AND '.join(clauses)}
            ORDER BY timestamp {order}
            LIMIT ?
        """
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            result = [dict(r) for r in rows]
            if reverse_result:
                result.reverse()
            return result

    # --- positioning data (long/short ratio, open interest) ---

    def insert_ls_ratios(self, rows: Iterable[dict[str, Any]]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        sql = """
            INSERT OR IGNORE INTO long_short_ratio
                (symbol, timestamp, ratio)
            VALUES
                (:symbol, :timestamp, :ratio)
        """
        with self._connect() as conn:
            cursor = conn.executemany(sql, rows)
            return cursor.rowcount

    def get_ls_ratio_history(
        self,
        symbol: str,
        limit: int = 2160,
        since: int | None = None,
        before: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["symbol = ?"]
        params: list[Any] = [symbol]
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since)
        if before is not None:
            clauses.append("timestamp <= ?")
            params.append(before)

        sql = f"""
            SELECT * FROM long_short_ratio
            WHERE {' AND '.join(clauses)}
            ORDER BY timestamp DESC
            LIMIT ?
        """
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            result = [dict(r) for r in rows]
            result.reverse()
            return result

    def insert_open_interest(self, rows: Iterable[dict[str, Any]]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        sql = """
            INSERT OR IGNORE INTO open_interest
                (symbol, timestamp, open_interest)
            VALUES
                (:symbol, :timestamp, :open_interest)
        """
        with self._connect() as conn:
            cursor = conn.executemany(sql, rows)
            return cursor.rowcount

    def get_open_interest_history(
        self,
        symbol: str,
        limit: int = 720,
        since: int | None = None,
        before: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["symbol = ?"]
        params: list[Any] = [symbol]
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since)
        if before is not None:
            clauses.append("timestamp <= ?")
            params.append(before)

        sql = f"""
            SELECT * FROM open_interest
            WHERE {' AND '.join(clauses)}
            ORDER BY timestamp DESC
            LIMIT ?
        """
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            result = [dict(r) for r in rows]
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
            return conn.insert_returning_id(sql, row)

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
        row = dict(row)
        for optional in ("strategy", "style", "atr_pct", "trail_price", "exit_reason", "fees"):
            row.setdefault(optional, None)
        sql = """
            INSERT INTO paper_trades
                (signal_id, symbol, direction, entry_price, exit_price,
                 quantity, stop_loss, take_profit, status, pnl,
                 opened_at, closed_at, strategy, style, atr_pct,
                 trail_price, exit_reason, fees)
            VALUES
                (:signal_id, :symbol, :direction, :entry_price, :exit_price,
                 :quantity, :stop_loss, :take_profit, :status, :pnl,
                 :opened_at, :closed_at, :strategy, :style, :atr_pct,
                 :trail_price, :exit_reason, :fees)
        """
        with self._connect() as conn:
            return conn.insert_returning_id(sql, row)

    def get_open_trades(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if symbol:
            sql = """
                SELECT * FROM paper_trades
                WHERE status = 'open' AND symbol = ?
                ORDER BY opened_at DESC
            """
            with self._connect() as conn:
                return [dict(r) for r in conn.execute(sql, (symbol,)).fetchall()]
        sql = "SELECT * FROM paper_trades WHERE status = 'open' ORDER BY opened_at DESC"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql).fetchall()]

    def get_open_trade_for_symbol(self, symbol: str) -> dict[str, Any] | None:
        trades = self.get_open_trades(symbol)
        return trades[0] if trades else None

    def count_open_trades(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM paper_trades WHERE status = 'open'"
            ).fetchone()
            return int(row["n"])

    def get_recent_trades(self, limit: int = 10) -> list[dict[str, Any]]:
        sql = """
            SELECT * FROM paper_trades
            ORDER BY opened_at DESC
            LIMIT ?
        """
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, (limit,)).fetchall()]

    def get_recent_closed_trades(self, limit: int = 20) -> list[dict[str, Any]]:
        sql = """
            SELECT * FROM paper_trades
            WHERE status = 'closed'
            ORDER BY closed_at DESC
            LIMIT ?
        """
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, (limit,)).fetchall()]

    def get_closed_trades_for_symbol(self, symbol: str) -> list[dict[str, Any]]:
        sql = """
            SELECT * FROM paper_trades
            WHERE symbol = ? AND status = 'closed'
            ORDER BY closed_at DESC
        """
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, (symbol,)).fetchall()]

    def get_all_trades_for_symbol(self, symbol: str) -> list[dict[str, Any]]:
        sql = """
            SELECT * FROM paper_trades
            WHERE symbol = ?
            ORDER BY opened_at DESC
        """
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, (symbol,)).fetchall()]

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
            return conn.insert_returning_id(sql, row)

    def get_market_outcome_by_signal(self, signal_id: int) -> dict[str, Any] | None:
        sql = "SELECT * FROM market_outcomes WHERE signal_id = ? LIMIT 1"
        with self._connect() as conn:
            row = conn.execute(sql, (signal_id,)).fetchone()
            return dict(row) if row else None

    def update_market_outcome(self, outcome_id: int, fields: dict[str, Any]) -> None:
        if not fields:
            return
        columns = ", ".join(f"{key} = ?" for key in fields)
        sql = f"UPDATE market_outcomes SET {columns} WHERE id = ?"
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

    def get_price_at_or_after(
        self, symbol: str, timestamp: int, timeframe: str = "1h"
    ) -> dict[str, Any] | None:
        sql = """
            SELECT * FROM prices
            WHERE symbol = ? AND timeframe = ? AND timestamp >= ?
            ORDER BY timestamp ASC
            LIMIT 1
        """
        with self._connect() as conn:
            row = conn.execute(sql, (symbol, timeframe, timestamp)).fetchone()
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

    # --- market metrics (open interest / long-short ratio) ---

    def insert_market_metrics(self, rows: Iterable[dict[str, Any]]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        sql = """
            INSERT OR IGNORE INTO market_metrics
                (symbol, timestamp, open_interest, open_interest_usd, long_short_ratio)
            VALUES
                (:symbol, :timestamp, :open_interest, :open_interest_usd, :long_short_ratio)
        """
        with self._connect() as conn:
            cursor = conn.executemany(sql, rows)
            return cursor.rowcount

    def get_latest_market_metrics(self, symbol: str) -> dict[str, Any] | None:
        sql = """
            SELECT * FROM market_metrics
            WHERE symbol = ?
            ORDER BY timestamp DESC
            LIMIT 1
        """
        with self._connect() as conn:
            row = conn.execute(sql, (symbol,)).fetchone()
            return dict(row) if row else None

    def get_market_metrics(self, symbol: str, limit: int = 48) -> list[dict[str, Any]]:
        sql = """
            SELECT * FROM market_metrics
            WHERE symbol = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """
        with self._connect() as conn:
            rows = conn.execute(sql, (symbol, limit)).fetchall()
            result = [dict(r) for r in rows]
            result.reverse()
            return result

    def cleanup_old_market_metrics(self, max_age_days: int = 30) -> int:
        cutoff = int(datetime.now(timezone.utc).timestamp()) - max_age_days * 24 * 3600
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM market_metrics WHERE timestamp < ?", (cutoff,)
            )
            return cursor.rowcount

    # --- strategy scorecard ---

    def get_strategy_stats(self) -> list[dict[str, Any]]:
        """Aggregate closed-trade performance per (strategy, direction)."""
        sql = """
            SELECT
                COALESCE(strategy, 'unknown') AS strategy,
                direction,
                COUNT(*) AS trades,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                SUM(pnl) AS total_pnl,
                AVG(pnl / (quantity * entry_price) * 100.0) AS avg_return_pct,
                AVG(CASE WHEN pnl > 0 THEN pnl / (quantity * entry_price) * 100.0 END)
                    AS avg_win_pct,
                AVG(CASE WHEN pnl <= 0 THEN pnl / (quantity * entry_price) * 100.0 END)
                    AS avg_loss_pct,
                AVG((closed_at - opened_at) / 3600.0) AS avg_hold_hours
            FROM paper_trades
            WHERE status = 'closed' AND pnl IS NOT NULL
              AND quantity > 0 AND entry_price > 0
            GROUP BY COALESCE(strategy, 'unknown'), direction
            ORDER BY trades DESC
        """
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute(sql).fetchall()]
        for row in rows:
            trades = int(row["trades"] or 0)
            wins = int(row["wins"] or 0)
            row["trades"] = trades
            row["wins"] = wins
            row["win_rate_pct"] = round(wins / trades * 100.0, 1) if trades else None
            avg_win = row.get("avg_win_pct")
            avg_loss = row.get("avg_loss_pct")
            row["payoff_ratio"] = (
                round(abs(float(avg_win) / float(avg_loss)), 2)
                if avg_win is not None and avg_loss not in (None, 0)
                else None
            )
            for key in ("total_pnl", "avg_return_pct", "avg_win_pct", "avg_loss_pct", "avg_hold_hours"):
                if row.get(key) is not None:
                    row[key] = round(float(row[key]), 3)
        return rows

    def get_strategy_win_rate(self, strategy: str, direction: str) -> dict[str, Any]:
        """Win rate for one strategy+direction (for confidence calibration)."""
        sql = """
            SELECT
                COUNT(*) AS trades,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins
            FROM paper_trades
            WHERE status = 'closed' AND pnl IS NOT NULL
              AND COALESCE(strategy, 'unknown') = ? AND direction = ?
        """
        with self._connect() as conn:
            row = conn.execute(sql, (strategy, direction)).fetchone()
        trades = int(row["trades"] or 0)
        wins = int(row["wins"] or 0)
        return {
            "trades": trades,
            "wins": wins,
            "win_rate": (wins / trades) if trades else None,
        }

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
