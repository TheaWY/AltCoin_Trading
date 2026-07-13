"""BENCHMARK_PERCENTILE -- where a strategy's walk-forward result falls in
the random-entry distribution over the same windows.

The ensemble (N seeds of src/strategies/random_entry.py run through the
identical walk-forward harness) is generated offline by
ops/generate_random_ensemble.py and stored in benchmark_meta under
ENSEMBLE_KEY. The runner then stamps each finished experiment's
aggregate with its percentile against that distribution: percentile 95
means the strategy beat 95% of random-entry books with identical risk
management. A strategy that can't beat random entries isn't a strategy.

The stored ensemble records its window config hash and activity level
(trades/window); the percentile is only computed when the window config
matches, and the activity level is stamped alongside so a mismatch (e.g. a
1500-trade strategy scored against a 200-trade ensemble) is visible, not
silent.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

ENSEMBLE_KEY = "random_walkforward_ensemble"


def windows_config_hash(windows: list[tuple[str, str]], symbols: str) -> str:
    canonical = json.dumps({"windows": windows, "symbols": symbols}, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def load_ensemble(storage: Any) -> dict[str, Any] | None:
    with storage._connect() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT config_json FROM benchmark_meta WHERE book = ?", (ENSEMBLE_KEY,)
        ).fetchone()
    if not row:
        return None
    return json.loads(dict(row)["config_json"])


def save_ensemble(storage: Any, ensemble: dict[str, Any]) -> None:
    import time
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute("DELETE FROM benchmark_meta WHERE book = ?", (ENSEMBLE_KEY,))
        conn.execute(
            "INSERT INTO benchmark_meta (book, config_json, created_at) VALUES (?, ?, ?)",
            (ENSEMBLE_KEY, json.dumps(ensemble), int(time.time())),
        )


def benchmark_percentile(
    storage: Any,
    strategy_total_pnl: float,
    current_windows_hash: str,
) -> dict[str, Any] | None:
    """Percentile of strategy_total_pnl in the stored random-entry
    distribution, or None when no matching ensemble exists (missing, or
    generated for a different window config)."""
    ensemble = load_ensemble(storage)
    if not ensemble:
        return None
    if ensemble.get("windows_hash") != current_windows_hash:
        return None
    pnls = sorted(float(p) for p in ensemble.get("seed_pnls", []))
    if not pnls:
        return None
    below = sum(1 for p in pnls if p < strategy_total_pnl)
    ties = sum(1 for p in pnls if p == strategy_total_pnl)
    percentile = (below + 0.5 * ties) / len(pnls) * 100.0
    return {
        "benchmark_percentile": round(percentile, 1),
        "benchmark_ensemble_n": len(pnls),
        "benchmark_ensemble_trades_per_window": ensemble.get("trades_per_window"),
        "benchmark_ensemble_median_pnl": round(pnls[len(pnls) // 2], 2),
    }
