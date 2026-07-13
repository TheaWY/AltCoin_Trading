"""Event study runner — does a signal actually predict forward returns?

The cheapest, cleanest test in quant research: mark every timestamp where a
signal fires, measure returns 4h/24h/72h later, compare against the symbol's
baseline forward-return distribution. No stops, no sizing, no fees — pure
"is there information here?". Signals that fail this never become setups,
which shrinks the strategy search space by an order of magnitude.

Guards built in:
- De-clustering: at most one event per signal/symbol per 24h, so overlapping
  events don't inflate the sample.
- Bootstrap CI on the mean difference vs baseline (1000 resamples).
- Regime split: BTC 7d return sign at event time (up / down / flat).
- Pass verdict requires: n >= MIN_EVENTS, |effect| > EFFECT_FLOOR (round-trip
  fee), CI excluding zero, and directional consistency in >= 2 regimes.

Results are written to the event_study_results table (for the dashboard) and
printed as a report.

    python -m src.research.event_study --symbols BTC/USDT,ETH/USDT --days 365
"""

from __future__ import annotations

import argparse
import json
import random
import time
from typing import Any, Callable

from src.data.storage import get_storage

HORIZONS_H = (4, 24, 72)
MIN_EVENTS = 200
EFFECT_FLOOR = 0.0015          # 0.15% — must beat a round trip of fees
DECLUSTER_SECONDS = 24 * 3600
BOOTSTRAP_N = 1000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_study_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at INTEGER NOT NULL,
    signal TEXT NOT NULL,
    horizon_h INTEGER NOT NULL,
    regime TEXT NOT NULL,
    n_events INTEGER NOT NULL,
    mean_fwd_return REAL NOT NULL,
    baseline_fwd_return REAL NOT NULL,
    effect REAL NOT NULL,
    ci_low REAL,
    ci_high REAL,
    verdict TEXT NOT NULL
);
"""


def _ensure_schema(conn: Any) -> None:
    for stmt in _SCHEMA.split(";"):
        if stmt.strip():
            conn.execute(stmt)
    # p_value added 2026-07-13 for multiple-comparisons correction across
    # the full (signal, horizon) grid -- Postgres supports IF NOT EXISTS
    # natively; SQLite's ALTER TABLE ADD COLUMN does not (verified, not
    # assumed), hence the PRAGMA existence check on that path.
    if conn.is_postgres:
        conn.raw.execute(
            "ALTER TABLE event_study_results ADD COLUMN IF NOT EXISTS p_value DOUBLE PRECISION"
        )
    else:
        columns = {
            row["name"] for row in conn.raw.execute("PRAGMA table_info(event_study_results)").fetchall()
        }
        if "p_value" not in columns:
            conn.raw.execute("ALTER TABLE event_study_results ADD COLUMN p_value REAL")


# ---------------------------------------------------------------------------
# Data access (point-in-time by construction: only past rows used per event)
# ---------------------------------------------------------------------------

def _load_prices(symbol: str, since: int, storage: Any = None) -> list[dict[str, Any]]:
    storage = storage or get_storage()
    return storage.get_prices(symbol, limit=1_000_000, since=since, timeframe="1h")


def _load_funding(symbol: str, since: int) -> list[dict[str, Any]]:
    return get_storage().get_funding_rates(symbol, limit=1_000_000, since=since)


def _load_metrics(symbol: str, since: int) -> list[dict[str, Any]]:
    with get_storage()._connect() as conn:  # noqa: SLF001
        rows = conn.execute(
            "SELECT timestamp, open_interest, long_short_ratio FROM market_metrics "
            "WHERE symbol = ? AND timestamp >= ? ORDER BY timestamp",
            (symbol, since),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Signal definitions — each returns event timestamps (bar close times).
# Every signal computes its threshold from PAST data only (expanding window).
# ---------------------------------------------------------------------------

def _rolling_pctl_events(
    series: list[tuple[int, float]], pctl: float, min_history: int, high: bool
) -> list[int]:
    events = []
    values: list[float] = []
    for ts, value in series:
        if len(values) >= min_history:
            rank = sum(1 for v in values if v < value) / len(values)
            if (high and rank >= pctl) or (not high and rank <= 1 - pctl):
                events.append(ts)
        values.append(value)
    return events


def sig_funding_high(symbol: str, since: int) -> list[int]:
    rows = _load_funding(symbol, since)
    series = [(int(r["timestamp"]), float(r["funding_rate"])) for r in rows]
    return _rolling_pctl_events(series, 0.95, 100, high=True)


def sig_lsr_extreme_long(symbol: str, since: int) -> list[int]:
    rows = [r for r in _load_metrics(symbol, since) if r["long_short_ratio"] is not None]
    series = [(int(r["timestamp"]), float(r["long_short_ratio"])) for r in rows]
    return _rolling_pctl_events(series, 0.95, 200, high=True)


def sig_oi_surge(symbol: str, since: int) -> list[int]:
    rows = [r for r in _load_metrics(symbol, since) if r["open_interest"] is not None]
    events = []
    window: list[float] = []
    for r in rows:
        oi = float(r["open_interest"])
        if len(window) >= 24:
            base = window[-24]
            if base > 0 and (oi - base) / base >= 0.10:  # +10% OI in 24 rows
                events.append(int(r["timestamp"]))
        window.append(oi)
    return events


def sig_rsi_overbought_bb(symbol: str, since: int) -> list[int]:
    prices = _load_prices(symbol, since)
    closes = [float(p["close"]) for p in prices]
    ts_list = [int(p["timestamp"]) for p in prices]
    events = []
    for i in range(30, len(closes)):
        window = closes[i - 20:i + 1]
        mid = sum(window) / len(window)
        var = sum((c - mid) ** 2 for c in window) / len(window)
        upper = mid + 2 * (var ** 0.5)
        deltas = [closes[j + 1] - closes[j] for j in range(i - 14, i)]
        gains = sum(d for d in deltas if d > 0)
        losses = -sum(d for d in deltas if d < 0)
        rsi = 100.0 if losses == 0 else 100 - 100 / (1 + gains / losses)
        if rsi > 70 and closes[i] > upper:
            events.append(ts_list[i])
    return events


VOLUME_ZSCORE_BASELINE_HOURS = 168  # one week of hourly baseline


def sig_volume_spike(symbol: str, since: int, storage: Any = None) -> list[int]:
    prices = _load_prices(symbol, since, storage)
    events = []
    vols: list[float] = []
    for p in prices:
        v = float(p.get("volume") or 0.0)
        if len(vols) >= VOLUME_ZSCORE_BASELINE_HOURS:
            recent = vols[-VOLUME_ZSCORE_BASELINE_HOURS:]
            avg = sum(recent) / len(recent)
            std = (sum((x - avg) ** 2 for x in recent) / len(recent)) ** 0.5
            if std > 0 and (v - avg) / std >= 3.0:
                events.append(int(p["timestamp"]))
        vols.append(v)
    return events


SIGNALS: dict[str, Callable[[str, int], list[int]]] = {
    "funding_pctl95_high": sig_funding_high,
    "lsr_pctl95_crowded_long": sig_lsr_extreme_long,
    "oi_surge_10pct_24h": sig_oi_surge,
    "rsi70_above_upper_bb": sig_rsi_overbought_bb,
    "volume_zscore_3plus": sig_volume_spike,
}

try:
    from src.research.candle_signals import EXTRA_SIGNALS
    SIGNALS.update(EXTRA_SIGNALS)
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Forward returns, baseline, regimes, bootstrap
# ---------------------------------------------------------------------------

def _decluster(events: list[int]) -> list[int]:
    out: list[int] = []
    for ts in sorted(events):
        if not out or ts - out[-1] >= DECLUSTER_SECONDS:
            out.append(ts)
    return out


def _price_index(prices: list[dict[str, Any]]) -> dict[int, float]:
    return {int(p["timestamp"]): float(p["close"]) for p in prices}


def _forward_return(idx: dict[int, float], ts: int, horizon_h: int) -> float | None:
    p0 = idx.get(ts)
    p1 = idx.get(ts + horizon_h * 3600)
    if p0 and p1 and p0 > 0:
        return (p1 - p0) / p0
    return None


def _btc_regime(btc_idx: dict[int, float], ts: int) -> str:
    p_now, p_then = btc_idx.get(ts), btc_idx.get(ts - 7 * 86400)
    if not p_now or not p_then:
        return "unknown"
    change = (p_now - p_then) / p_then
    if change > 0.03:
        return "up"
    if change < -0.03:
        return "down"
    return "flat"


def _bootstrap_ci(diffs: list[float]) -> tuple[float, float]:
    ci_low, ci_high, _ = _bootstrap_ci_and_pvalue(diffs)
    return ci_low, ci_high


def _bootstrap_ci_and_pvalue(diffs: list[float]) -> tuple[float, float, float]:
    """95% CI on the mean difference, plus a two-tailed bootstrap p-value
    (2 x the smaller tail fraction crossing zero, capped at 1.0) -- used for
    multiple-comparisons correction across the full (signal, horizon) grid,
    not just eyeballing whether one cell's CI happens to exclude zero."""
    rng = random.Random(42)  # deterministic
    means = []
    n = len(diffs)
    for _ in range(BOOTSTRAP_N):
        sample = [diffs[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    frac_le_zero = sum(1 for m in means if m <= 0) / len(means)
    frac_ge_zero = sum(1 for m in means if m >= 0) / len(means)
    p_value = min(1.0, 2 * min(frac_le_zero, frac_ge_zero))
    return means[int(0.025 * BOOTSTRAP_N)], means[int(0.975 * BOOTSTRAP_N)], p_value


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run(symbols: list[str], days: int) -> list[dict[str, Any]]:
    since = int(time.time()) - days * 86400
    storage = get_storage()
    with storage._connect() as conn:  # noqa: SLF001
        _ensure_schema(conn)

    price_idx = {s: _price_index(_load_prices(s, since - 8 * 86400)) for s in symbols}
    btc_idx = price_idx.get("BTC/USDT") or next(iter(price_idx.values()))

    # baseline forward returns per horizon (all hourly timestamps, all symbols)
    baselines: dict[int, list[float]] = {h: [] for h in HORIZONS_H}
    for s in symbols:
        for ts in price_idx[s]:
            for h in HORIZONS_H:
                r = _forward_return(price_idx[s], ts, h)
                if r is not None:
                    baselines[h].append(r)

    results: list[dict[str, Any]] = []
    run_at = int(time.time())

    for name, fn in SIGNALS.items():
        # events pooled across symbols, tagged with their symbol
        tagged: list[tuple[str, int]] = []
        for s in symbols:
            for ts in _decluster(fn(s, since)):
                tagged.append((s, ts))

        for h in HORIZONS_H:
            by_regime: dict[str, list[float]] = {"all": []}
            for s, ts in tagged:
                r = _forward_return(price_idx[s], ts, h)
                if r is None:
                    continue
                by_regime["all"].append(r)
                by_regime.setdefault(_btc_regime(btc_idx, ts), []).append(r)

            base_mean = sum(baselines[h]) / len(baselines[h]) if baselines[h] else 0.0
            all_rets = by_regime.get("all", [])
            regime_signs = {
                reg: (sum(v) / len(v)) - base_mean
                for reg, v in by_regime.items()
                if reg not in ("all", "unknown") and len(v) >= 30
            }

            for regime, rets in sorted(by_regime.items()):
                if not rets and regime != "all":
                    continue
                mean = sum(rets) / len(rets) if rets else 0.0
                effect = mean - base_mean
                diffs = [r - base_mean for r in rets]
                if len(diffs) >= 30:
                    ci_low, ci_high, p_value = _bootstrap_ci_and_pvalue(diffs)
                else:
                    ci_low, ci_high, p_value = None, None, None

                verdict = "insufficient_n"
                if regime == "all" and len(all_rets) >= MIN_EVENTS:
                    ci_excludes_zero = (
                        ci_low is not None and (ci_low > 0 or ci_high < 0)
                    )
                    consistent = (
                        len(regime_signs) >= 2
                        and len({e > 0 for e in regime_signs.values()}) == 1
                    )
                    if abs(effect) > EFFECT_FLOOR and ci_excludes_zero and consistent:
                        verdict = "PASS"
                    else:
                        verdict = "fail"
                elif regime != "all":
                    verdict = "context"

                row = {
                    "run_at": run_at, "signal": name, "horizon_h": h,
                    "regime": regime, "n_events": len(rets),
                    "mean_fwd_return": round(mean, 6),
                    "baseline_fwd_return": round(base_mean, 6),
                    "effect": round(effect, 6),
                    "ci_low": round(ci_low, 6) if ci_low is not None else None,
                    "ci_high": round(ci_high, 6) if ci_high is not None else None,
                    "p_value": round(p_value, 6) if p_value is not None else None,
                    "verdict": verdict,
                }
                results.append(row)
                with storage._connect() as conn:  # noqa: SLF001
                    conn.execute(
                        "INSERT INTO event_study_results (run_at, signal, horizon_h, "
                        "regime, n_events, mean_fwd_return, baseline_fwd_return, "
                        "effect, ci_low, ci_high, p_value, verdict) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        tuple(row.values()),
                    )
    return results


def report(results: list[dict[str, Any]]) -> str:
    lines = [f"{'signal':<26}{'h':>4}{'regime':>8}{'n':>6}{'effect':>10}{'CI':>20}{'verdict':>14}"]
    for r in results:
        ci = (
            f"[{r['ci_low']:+.4f},{r['ci_high']:+.4f}]"
            if r["ci_low"] is not None else "-"
        )
        lines.append(
            f"{r['signal']:<26}{r['horizon_h']:>4}{r['regime']:>8}{r['n_events']:>6}"
            f"{r['effect']:>+10.4f}{ci:>20}{r['verdict']:>14}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="BTC/USDT,ETH/USDT")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    results = run([s.strip() for s in args.symbols.split(",")], args.days)
    print(json.dumps(results, indent=2) if args.json else report(results))
    n_pass = sum(1 for r in results if r["verdict"] == "PASS")
    print(f"\nPASS signals: {n_pass} — only these graduate to setup implementation.")


if __name__ == "__main__":
    main()
