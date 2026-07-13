"""Generate the random-entry walk-forward ensemble behind BENCHMARK_PERCENTILE.

Runs N_SEEDS random-entry books (src/strategies/random_entry.py) through the
IDENTICAL walk-forward harness the nightly runner uses -- same windows, same
symbols, same costs -- and stores the seed-level total_pnl distribution in
benchmark_meta. Long-running (~1-2h at 20 seeds x 40 windows); intended for
overnight/idle execution:

    .venv/bin/python ops/generate_random_ensemble.py

The ensemble's activity level is calibrated once (a single probe seed) toward
TARGET_TRADES_PER_WINDOW so random books trade at a frequency comparable to
the real strategies; the realized trades/window is stored alongside the
distribution so any mismatch with a scored strategy is visible, not silent.
Re-run after changing windows/symbols config -- the stored windows_hash
gates percentile computation, so a stale ensemble simply stops matching
rather than silently mis-scoring.
"""

from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.storage import get_storage  # noqa: E402
from src.research import decisions  # noqa: E402
from src.research.benchmark_percentile import save_ensemble, windows_config_hash  # noqa: E402
from src.research.runner import SYMBOLS, _aggregate, _run_experiment_windows, _windows  # noqa: E402

N_SEEDS = int(os.getenv("ENSEMBLE_SEEDS", "20"))
TARGET_TRADES_PER_WINDOW = float(os.getenv("ENSEMBLE_TARGET_TRADES_PER_WINDOW", "5.0"))
PROBE_PROB = 0.002


def _run_seed(seed: int, prob: float, windows: list) -> dict | None:
    overrides = {
        "ACTIVE_STRATEGY": "random_entry",
        "RANDOM_ENTRY_SEED": seed,
        "RANDOM_ENTRY_PROB_PER_BAR": prob,
        "STOP_SLIPPAGE_MULT": 1.0,
    }
    exp = {"config_json": json.dumps(overrides)}
    _, ok, window_results = _run_experiment_windows(exp, windows)
    if not ok or not window_results:
        return None
    return _aggregate(window_results)


def main() -> int:
    windows = _windows()
    print(f"windows={len(windows)} symbols={SYMBOLS} seeds={N_SEEDS}", flush=True)

    # Calibration probe: one seed at PROBE_PROB, then scale the probability
    # linearly toward the target activity (entry attempts scale ~linearly
    # in prob until slots/cooldowns saturate).
    probe = _run_seed(9999, PROBE_PROB, windows)
    if probe is None or not probe["trade_count"]:
        print("ERROR: calibration probe produced no trades", flush=True)
        return 1
    probe_tpw = probe["trade_count"] / len(windows)
    prob = max(1e-5, min(0.05, PROBE_PROB * TARGET_TRADES_PER_WINDOW / probe_tpw))
    print(f"probe trades/window={probe_tpw:.2f} -> calibrated prob={prob:.5f}", flush=True)

    results: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_run_seed, s, prob, windows): s for s in range(N_SEEDS)}
        for fut in as_completed(futures):
            seed = futures[fut]
            agg = fut.result()
            if agg:
                results[seed] = agg
                print(f"DONE seed={seed} trades={agg['trade_count']} pnl={agg['total_pnl']}", flush=True)
            else:
                print(f"FAILED seed={seed}", flush=True)

    if len(results) < N_SEEDS * 0.8:
        print(f"ERROR: only {len(results)}/{N_SEEDS} seeds completed; not saving", flush=True)
        return 1

    seed_pnls = [results[s]["total_pnl"] for s in sorted(results)]
    trades_per_window = sum(r["trade_count"] for r in results.values()) / len(results) / len(windows)
    ensemble = {
        "windows_hash": windows_config_hash(windows, SYMBOLS),
        "seed_pnls": seed_pnls,
        "seeds": sorted(results),
        "prob_per_bar": prob,
        "trades_per_window": round(trades_per_window, 2),
        "windows": len(windows),
        "symbols": SYMBOLS,
    }
    storage = get_storage()
    save_ensemble(storage, ensemble)
    decisions.log("research", "benchmark_ensemble_generated", "random_walkforward_ensemble", {
        "seeds": len(results), "trades_per_window": ensemble["trades_per_window"],
        "median_pnl": sorted(seed_pnls)[len(seed_pnls) // 2],
        "windows_hash": ensemble["windows_hash"],
    })
    print(f"SAVED ensemble: {len(seed_pnls)} seeds, median pnl "
          f"{sorted(seed_pnls)[len(seed_pnls) // 2]:.2f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
