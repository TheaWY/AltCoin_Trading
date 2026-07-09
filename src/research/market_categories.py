"""Dynamic market categories for crypto universe research.

The goal is not to let a clustering label directly trade. The category layer:

1. groups symbols by current behaviour every ~30 minutes;
2. records category transitions for research/backtests;
3. filters paper entries away from categories where this strategy should not work;
4. gives the dashboard a visible "what kind of coin is this right now?" tag.

Implementation deliberately avoids sklearn so the Mac mini worker can run with the
current lightweight dependency set. It uses deterministic k-means over robustly
scaled features, then maps cluster centroids to semantic labels.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import time
from dataclasses import dataclass
from typing import Any

from src import config
from src.data.storage import Storage, get_storage
from src.engine import indicators


CATEGORY_VERSION = "v1_kmeans_behavior"
DEFAULT_K = int(os.getenv("MARKET_CATEGORY_K", "6"))
DEFAULT_MIN_TRADE_VOLUME = float(os.getenv("CATEGORY_MIN_TRADE_VOLUME_USD", "5000000"))
DEFAULT_MAX_ATR_PCT = float(os.getenv("CATEGORY_MAX_TRADE_ATR_PCT", "12"))
DEFAULT_MIN_ATR_PCT = float(os.getenv("CATEGORY_MIN_TRADE_ATR_PCT", "0.20"))
DEFAULT_TICK_BUCKET_SECONDS = int(os.getenv("CATEGORY_TICK_BUCKET_SECONDS", "1"))
CATEGORY_TTL_SECONDS = int(os.getenv("CATEGORY_TTL_SECONDS", str(2 * 3600)))

# Categories where the current 1h short-biased strategy is allowed to trade.
# Scalping can later use a different allowlist.
TRADE_ALLOWED_CATEGORIES = set(
    part.strip()
    for part in os.getenv(
        "CATEGORY_TRADE_ALLOWLIST",
        "large_beta,liquid_trend,crowded_funding,volume_surge,range_meanrev",
    ).split(",")
    if part.strip()
)

BLOCKED_BASES = {
    "PAXG",
    "XAUT",
    "USDC",
    "FDUSD",
    "TUSD",
    "DAI",
    "BUSD",
    "USDP",
    "EURI",
    "USD1",
    "AEUR",
}


@dataclass
class CategorySnapshot:
    symbol: str
    category: str
    cluster_id: int
    trade_allowed: bool
    confidence: float
    reason: str
    features: dict[str, float | int | None]

    def row(self, ts: int) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": ts,
            "version": CATEGORY_VERSION,
            "cluster_id": self.cluster_id,
            "category": self.category,
            "trade_allowed": 1 if self.trade_allowed else 0,
            "confidence": self.confidence,
            "reason": self.reason,
            "features_json": json.dumps(self.features, separators=(",", ":"), ensure_ascii=False),
        }


def ensure_schema(storage: Storage | None = None) -> None:
    storage = storage or get_storage()
    if storage.is_postgres:
        schema = """
        CREATE TABLE IF NOT EXISTS market_categories (
            id BIGSERIAL PRIMARY KEY,
            symbol TEXT NOT NULL,
            timestamp BIGINT NOT NULL,
            version TEXT NOT NULL,
            cluster_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            trade_allowed INTEGER NOT NULL,
            confidence DOUBLE PRECISION NOT NULL,
            reason TEXT,
            features_json TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(symbol, timestamp, version)
        );
        CREATE INDEX IF NOT EXISTS idx_market_categories_symbol_ts
            ON market_categories(symbol, timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_market_categories_category_ts
            ON market_categories(category, timestamp DESC);

        CREATE TABLE IF NOT EXISTS market_category_transitions (
            id BIGSERIAL PRIMARY KEY,
            symbol TEXT NOT NULL,
            from_category TEXT,
            to_category TEXT NOT NULL,
            from_ts BIGINT,
            to_ts BIGINT NOT NULL,
            reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(symbol, to_ts)
        );
        CREATE INDEX IF NOT EXISTS idx_market_category_transitions_ts
            ON market_category_transitions(to_ts DESC);
        """
        with storage._connect() as conn:  # noqa: SLF001
            for statement in schema.split(";"):
                if statement.strip():
                    conn.raw.execute(statement)
        return

    schema = """
    CREATE TABLE IF NOT EXISTS market_categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        timestamp INTEGER NOT NULL,
        version TEXT NOT NULL,
        cluster_id INTEGER NOT NULL,
        category TEXT NOT NULL,
        trade_allowed INTEGER NOT NULL,
        confidence REAL NOT NULL,
        reason TEXT,
        features_json TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(symbol, timestamp, version)
    );
    CREATE INDEX IF NOT EXISTS idx_market_categories_symbol_ts
        ON market_categories(symbol, timestamp DESC);
    CREATE INDEX IF NOT EXISTS idx_market_categories_category_ts
        ON market_categories(category, timestamp DESC);

    CREATE TABLE IF NOT EXISTS market_category_transitions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        from_category TEXT,
        to_category TEXT NOT NULL,
        from_ts INTEGER,
        to_ts INTEGER NOT NULL,
        reason TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(symbol, to_ts)
    );
    CREATE INDEX IF NOT EXISTS idx_market_category_transitions_ts
        ON market_category_transitions(to_ts DESC);
    """
    with storage._connect() as conn:  # noqa: SLF001
        conn.raw.executescript(schema)


def _base(symbol: str) -> str:
    return symbol.split("/")[0]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        v = float(value)
        if not math.isfinite(v):
            return default
        return v
    except Exception:
        return default


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def _mad(values: list[float], med: float) -> float:
    deviations = [abs(v - med) for v in values]
    return statistics.median(deviations) if deviations else 1.0


def _scale_feature_matrix(feature_rows: list[dict[str, float]], keys: list[str]) -> list[list[float]]:
    columns: dict[str, list[float]] = {k: [r.get(k, 0.0) for r in feature_rows] for k in keys}
    centers = {k: _median(v) for k, v in columns.items()}
    scales = {k: max(_mad(v, centers[k]) * 1.4826, 1e-9) for k, v in columns.items()}
    matrix = []
    for row in feature_rows:
        matrix.append([
            max(-6.0, min(6.0, (row.get(k, 0.0) - centers[k]) / scales[k]))
            for k in keys
        ])
    return matrix


def _sqdist(a: list[float], b: list[float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b))


def _kmeans(matrix: list[list[float]], k: int, iterations: int = 25) -> list[int]:
    if not matrix:
        return []
    k = max(1, min(k, len(matrix)))
    # Deterministic seed centers spread through the sorted matrix by vector norm.
    ordered = sorted(range(len(matrix)), key=lambda i: sum(x * x for x in matrix[i]))
    centers = [matrix[ordered[round(i * (len(ordered) - 1) / max(1, k - 1))]].copy() for i in range(k)]
    assignments = [0] * len(matrix)
    for _ in range(iterations):
        changed = False
        for i, row in enumerate(matrix):
            best = min(range(k), key=lambda c: _sqdist(row, centers[c]))
            if assignments[i] != best:
                assignments[i] = best
                changed = True
        sums = [[0.0 for _ in centers[0]] for _ in range(k)]
        counts = [0] * k
        for row, cluster in zip(matrix, assignments):
            counts[cluster] += 1
            for j, value in enumerate(row):
                sums[cluster][j] += value
        for c in range(k):
            if counts[c]:
                centers[c] = [value / counts[c] for value in sums[c]]
        if not changed:
            break
    return assignments


def _tick_burst_map(storage: Storage, now_ts: int) -> dict[str, dict[str, float]]:
    """Return 30m tick-bar surge metrics when tick_bars exists.

    If the tick table is missing or empty, return {} and rely on OHLCV volume.
    """
    current_start = now_ts - 30 * 60
    previous_start = now_ts - 60 * 60
    try:
        with storage._connect() as conn:  # noqa: SLF001
            current = conn.execute(
                "SELECT symbol, SUM(notional) AS notional, SUM(trade_count) AS trades "
                "FROM tick_bars WHERE bucket_seconds = ? AND timestamp >= ? "
                "GROUP BY symbol",
                (DEFAULT_TICK_BUCKET_SECONDS, current_start),
            ).fetchall()
            previous = conn.execute(
                "SELECT symbol, SUM(notional) AS notional, SUM(trade_count) AS trades "
                "FROM tick_bars WHERE bucket_seconds = ? AND timestamp >= ? AND timestamp < ? "
                "GROUP BY symbol",
                (DEFAULT_TICK_BUCKET_SECONDS, previous_start, current_start),
            ).fetchall()
    except Exception:
        return {}
    prev = {r["symbol"]: dict(r) for r in previous}
    out: dict[str, dict[str, float]] = {}
    for row in current:
        sym = row["symbol"]
        p = prev.get(sym, {})
        cur_notional = _safe_float(row.get("notional"))
        prev_notional = _safe_float(p.get("notional"))
        cur_trades = _safe_float(row.get("trades"))
        prev_trades = _safe_float(p.get("trades"))
        out[sym] = {
            "tick_notional_30m": cur_notional,
            "tick_trades_30m": cur_trades,
            "tick_notional_ratio": cur_notional / prev_notional if prev_notional > 0 else 0.0,
            "tick_trade_ratio": cur_trades / prev_trades if prev_trades > 0 else 0.0,
        }
    return out


def build_feature_rows(storage: Storage, symbols: list[str], now_ts: int | None = None) -> list[dict[str, Any]]:
    now_ts = now_ts or int(time.time())
    btc_rows = storage.get_prices(config.SYMBOL, limit=720, timeframe="1h")
    tick_bursts = _tick_burst_map(storage, now_ts)
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        candles = storage.get_prices(symbol, limit=720, timeframe="1h")
        if len(candles) < 48:
            continue
        metrics = indicators.compute_all(candles, btc_rows if symbol != config.SYMBOL else None)
        funding = storage.get_latest_funding_rate(symbol) or {}
        features = {
            "symbol": symbol,
            "base": _base(symbol),
            "candles": len(candles),
            "log_dollar_volume_24h": math.log1p(max(0.0, _safe_float(metrics.get("dollar_volume_24h")))),
            "dollar_volume_24h": _safe_float(metrics.get("dollar_volume_24h")),
            "atr_pct": _safe_float(metrics.get("atr_pct")),
            "realized_vol_7d": _safe_float(metrics.get("realized_vol_7d")),
            "pct_24h": _safe_float(metrics.get("pct_24h")),
            "pct_7d": _safe_float(metrics.get("pct_7d")),
            "pct_30d": _safe_float(metrics.get("pct_30d")),
            "btc_correlation": _safe_float(metrics.get("btc_correlation")),
            "btc_beta": _safe_float(metrics.get("btc_beta")),
            "volume_ratio": _safe_float(metrics.get("volume_ratio")),
            "funding_rate": _safe_float(funding.get("funding_rate")),
            "funding_abs": abs(_safe_float(funding.get("funding_rate"))),
            "from_high_30d_pct": _safe_float(metrics.get("from_high_30d_pct")),
            "from_low_30d_pct": _safe_float(metrics.get("from_low_30d_pct")),
        }
        features.update(tick_bursts.get(symbol, {}))
        features.setdefault("tick_notional_30m", 0.0)
        features.setdefault("tick_trades_30m", 0.0)
        features.setdefault("tick_notional_ratio", 0.0)
        features.setdefault("tick_trade_ratio", 0.0)
        rows.append(features)
    return rows


def _label_from_features(features: dict[str, float | int | str | None], cluster_id: int) -> tuple[str, bool, float, str]:
    base = str(features.get("base") or "")
    dollar_volume = _safe_float(features.get("dollar_volume_24h"))
    atr = _safe_float(features.get("atr_pct"))
    vol_ratio = _safe_float(features.get("volume_ratio"))
    tick_ratio = max(_safe_float(features.get("tick_notional_ratio")), _safe_float(features.get("tick_trade_ratio")))
    funding_abs = _safe_float(features.get("funding_abs"))
    corr = _safe_float(features.get("btc_correlation"))
    beta = _safe_float(features.get("btc_beta"))
    pct_7d = _safe_float(features.get("pct_7d"))
    pct_30d = _safe_float(features.get("pct_30d"))

    if base in BLOCKED_BASES:
        return "non_alt_asset", False, 0.95, "tokenized gold/stable/non-alt excluded"
    if dollar_volume < DEFAULT_MIN_TRADE_VOLUME or atr < DEFAULT_MIN_ATR_PCT:
        return "low_liquidity_noise", False, 0.85, "below volume/volatility floor"
    if atr > DEFAULT_MAX_ATR_PCT and dollar_volume < DEFAULT_MIN_TRADE_VOLUME * 4:
        return "volatile_meme", False, 0.75, "too volatile for current 1h strategy; scalp research only"
    if vol_ratio >= 3.0 or tick_ratio >= 3.0:
        allowed = dollar_volume >= DEFAULT_MIN_TRADE_VOLUME and atr <= DEFAULT_MAX_ATR_PCT
        return "volume_surge", allowed, 0.82, "30m/1h activity surge; newly tradable candidate"
    if funding_abs >= 0.001:
        return "crowded_funding", True, 0.78, "funding crowding extreme"
    if dollar_volume >= DEFAULT_MIN_TRADE_VOLUME * 10 and corr >= 0.55:
        return "large_beta", True, 0.78, "liquid BTC-correlated beta coin"
    if abs(pct_7d) >= 8 or abs(pct_30d) >= 18 or abs(beta) >= 1.5:
        return "liquid_trend", True, 0.72, "liquid trend/beta behavior"
    if atr >= 1.0 and abs(pct_7d) < 8 and corr < 0.55:
        return "range_meanrev", True, 0.68, "range-like independent mover"
    return f"cluster_{cluster_id}_watch", False, 0.55, "unproven behavior cluster; collect only"


def categorize_symbols(
    storage: Storage | None = None,
    symbols: list[str] | None = None,
    now_ts: int | None = None,
    k: int = DEFAULT_K,
) -> list[CategorySnapshot]:
    storage = storage or get_storage()
    now_ts = now_ts or int(time.time())
    if symbols is None:
        from src.symbols import trading_symbols

        symbols = trading_symbols()
    features = build_feature_rows(storage, symbols, now_ts)
    if not features:
        return []
    keys = [
        "log_dollar_volume_24h",
        "atr_pct",
        "realized_vol_7d",
        "pct_24h",
        "pct_7d",
        "pct_30d",
        "btc_correlation",
        "btc_beta",
        "volume_ratio",
        "funding_abs",
        "tick_notional_ratio",
        "tick_trade_ratio",
    ]
    matrix = _scale_feature_matrix(features, keys)
    assignments = _kmeans(matrix, k=k)
    snapshots: list[CategorySnapshot] = []
    for row, cluster in zip(features, assignments):
        category, allowed, confidence, reason = _label_from_features(row, cluster)
        snapshots.append(
            CategorySnapshot(
                symbol=str(row["symbol"]),
                category=category,
                cluster_id=int(cluster),
                trade_allowed=allowed and category in TRADE_ALLOWED_CATEGORIES,
                confidence=round(float(confidence), 3),
                reason=reason,
                features={
                    key: row.get(key) for key in row.keys() if key not in {"symbol", "base"}
                },
            )
        )
    return snapshots


def latest_category_map(storage: Storage | None = None, max_age_seconds: int | None = CATEGORY_TTL_SECONDS) -> dict[str, dict[str, Any]]:
    storage = storage or get_storage()
    ensure_schema(storage)
    cutoff = int(time.time()) - max_age_seconds if max_age_seconds else 0
    try:
        with storage._connect() as conn:  # noqa: SLF001
            rows = conn.execute(
                """
                SELECT mc.* FROM market_categories mc
                JOIN (
                    SELECT symbol, MAX(timestamp) AS timestamp
                    FROM market_categories
                    WHERE version = ? AND timestamp >= ?
                    GROUP BY symbol
                ) latest
                ON mc.symbol = latest.symbol AND mc.timestamp = latest.timestamp
                WHERE mc.version = ?
                """,
                (CATEGORY_VERSION, cutoff, CATEGORY_VERSION),
            ).fetchall()
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        try:
            item["features"] = json.loads(item.get("features_json") or "{}")
        except Exception:
            item["features"] = {}
        item["trade_allowed"] = bool(item.get("trade_allowed"))
        out[item["symbol"]] = item
    return out


def is_symbol_trade_allowed(storage: Storage, symbol: str) -> tuple[bool, str, dict[str, Any] | None]:
    """Return category filter decision.

    If no category snapshots exist yet, do not block the legacy bot. Once the
    category job has started, missing/stale symbols are blocked because the user
    explicitly wants category-aware filtering.
    """
    ensure_schema(storage)
    latest = latest_category_map(storage)
    if not latest:
        return True, "category filter not initialized", None
    item = latest.get(symbol)
    if not item:
        return False, "no fresh market category", None
    if not item.get("trade_allowed"):
        return False, f"category blocked: {item.get('category')}", item
    return True, f"category allowed: {item.get('category')}", item


def insert_category_snapshots(storage: Storage, snapshots: list[CategorySnapshot], timestamp: int | None = None) -> dict[str, Any]:
    ensure_schema(storage)
    ts = timestamp or int(time.time())
    previous = latest_category_map(storage, max_age_seconds=None)
    rows = [s.row(ts) for s in snapshots]
    transitions: list[dict[str, Any]] = []
    for snap in snapshots:
        prev = previous.get(snap.symbol)
        if prev and prev.get("category") != snap.category:
            transitions.append(
                {
                    "symbol": snap.symbol,
                    "from_category": prev.get("category"),
                    "to_category": snap.category,
                    "from_ts": int(prev.get("timestamp") or 0),
                    "to_ts": ts,
                    "reason": snap.reason,
                }
            )
    with storage._connect() as conn:  # noqa: SLF001
        conn.executemany(
            """
            INSERT OR IGNORE INTO market_categories
                (symbol, timestamp, version, cluster_id, category, trade_allowed,
                 confidence, reason, features_json)
            VALUES
                (:symbol, :timestamp, :version, :cluster_id, :category,
                 :trade_allowed, :confidence, :reason, :features_json)
            """,
            rows,
        )
        conn.executemany(
            """
            INSERT OR IGNORE INTO market_category_transitions
                (symbol, from_category, to_category, from_ts, to_ts, reason)
            VALUES
                (:symbol, :from_category, :to_category, :from_ts, :to_ts, :reason)
            """,
            transitions,
        )
    return {
        "timestamp": ts,
        "snapshots": len(rows),
        "transitions": len(transitions),
        "categories": category_counts(snapshots),
    }


def update_categories(storage: Storage | None = None, symbols: list[str] | None = None) -> dict[str, Any]:
    storage = storage or get_storage()
    ts = int(time.time())
    snapshots = categorize_symbols(storage, symbols=symbols, now_ts=ts)
    result = insert_category_snapshots(storage, snapshots, timestamp=ts)
    return result


def category_counts(snapshots: list[CategorySnapshot]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for snap in snapshots:
        counts[snap.category] = counts.get(snap.category, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def summary(storage: Storage | None = None) -> dict[str, Any]:
    storage = storage or get_storage()
    ensure_schema(storage)
    latest = latest_category_map(storage, max_age_seconds=None)
    now = int(time.time())
    counts: dict[str, int] = {}
    allowed = 0
    latest_ts = 0
    for item in latest.values():
        cat = str(item.get("category") or "unknown")
        counts[cat] = counts.get(cat, 0) + 1
        allowed += 1 if item.get("trade_allowed") else 0
        latest_ts = max(latest_ts, int(item.get("timestamp") or 0))
    try:
        with storage._connect() as conn:  # noqa: SLF001
            transitions = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM market_category_transitions ORDER BY to_ts DESC LIMIT 50"
                ).fetchall()
            ]
    except Exception:
        transitions = []
    return {
        "version": CATEGORY_VERSION,
        "latest_ts": latest_ts or None,
        "age_seconds": now - latest_ts if latest_ts else None,
        "symbols": len(latest),
        "trade_allowed": allowed,
        "blocked": max(0, len(latest) - allowed),
        "counts": dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))),
        "allowlist": sorted(TRADE_ALLOWED_CATEGORIES),
        "recent_transitions": transitions,
    }
