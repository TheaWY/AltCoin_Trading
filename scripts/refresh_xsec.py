"""Refresh cross-sectional features (dispersion + breadth) to current.

The live dispersion GATE (mean_reversion_long) reads xsec_dispersion but nothing
in the worker cycle writes it -- it was a research-only backfill and went stale
(frozen the gate on old flags => book stopped trading, 2026-07-18). This is the
missing writer: run on a short interval via com.altcoin.xsec so the gate always
reads current data. One-shot (compute + exit) -- launchd StartInterval re-runs it.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    from src.data.storage import get_storage
    from src.research import xsec_features as xf
    from src.symbols import trading_symbols

    import time
    storage = get_storage()
    # self-report staleness: if the data was already stale when we ran, the job
    # had been down -- surface it (the freeze-the-gate bug's early warning).
    try:
        with storage._connect() as conn:
            row = conn.execute("SELECT MAX(timestamp) mx FROM xsec_dispersion").fetchone()
        prev = int(dict(row)["mx"]) if row and dict(row).get("mx") else 0
        if prev and (time.time() - prev) > 3 * 3600:
            print(f"WARN: xsec_dispersion was STALE ({(time.time()-prev)/3600:.0f}h) before this "
                  f"refresh -- the refresh job may have been down.", flush=True)
    except Exception:
        pass
    symbols = trading_symbols()
    d = xf.compute_and_store(storage, symbols)
    print(f"xsec dispersion rows written/updated: {d} ({len(symbols)} symbols)", flush=True)
    try:
        b = xf.compute_breadth_and_store(storage, symbols)
        print(f"xsec breadth rows written/updated: {b}", flush=True)
    except Exception as exc:  # breadth is secondary; never fail the dispersion refresh
        print(f"WARN breadth refresh failed: {exc}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
