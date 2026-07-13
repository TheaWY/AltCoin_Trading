"""One-shot: reset all four benchmark books to the same starting line.

Closes any open paper positions through the NORMAL exit path
(exit_reason='benchmark_baseline_reset' -- same _close_trade machinery,
fees and slippage included, no synthetic bookkeeping), resets the strategy
book to PAPER_STARTING_CAPITAL, clears benchmark state, and re-initializes
btc_hold / alt_hold / the random seeds so all four books start at the SAME
timestamp on the fixed engine. Closed-trade history is retained in
paper_trades (status='closed') -- the archive is the table itself.

    .venv/bin/python ops/benchmark_baseline_reset.py

Logs research_decisions action='benchmark_baseline_start' with the reset
timestamp and each book's starting state.
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src import config  # noqa: E402
from src.data.storage import get_storage  # noqa: E402
from src.engine import benchmarks  # noqa: E402
from src.engine.paper_trader import PaperTrader  # noqa: E402
from src.research import decisions  # noqa: E402


def main() -> int:
    storage = get_storage()
    trader = PaperTrader(storage)
    now = int(time.time())

    # 1. Close open positions through the normal exit path.
    closed = []
    for trade in list(storage.get_open_trades()):
        row = storage.get_latest_price(trade["symbol"])
        if not row:
            print(f"ERROR: no price for open position {trade['symbol']}; aborting "
                  f"-- refusing to close blind or reset around an open position")
            return 1
        result = trader._close_trade(trade, float(row["close"]), "benchmark_baseline_reset")
        closed.append({"symbol": trade["symbol"], "pnl": result["pnl"]})
        print(f"closed {trade['symbol']} pnl={result['pnl']:.4f}")

    if storage.count_open_trades() != 0:
        print("ERROR: open trades remain after close pass; aborting")
        return 1

    # 2. Reset the strategy book. init_portfolio_state REPLACEs the single
    # portfolio_state row and stamps benchmark_btc_price/started_at = now,
    # which is exactly the shared start anchor the hold-books key off.
    btc_row = storage.get_latest_price(config.SYMBOL)
    if not btc_row:
        print("ERROR: no BTC price; aborting")
        return 1
    btc_price = float(btc_row["close"])
    storage.init_portfolio_state(config.PAPER_STARTING_CAPITAL, benchmark_btc_price=btc_price)

    # 3. Clear benchmark series/meta and the random seed books, then
    # initialize the hold books at the SAME timestamp.
    benchmarks._ensure_schema(storage)
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute("DELETE FROM benchmark_equity")
        conn.execute("DELETE FROM benchmark_meta WHERE book IN ('btc_hold', 'alt_hold')")
    if benchmarks.BENCHMARK_DIR.exists():
        shutil.rmtree(benchmarks.BENCHMARK_DIR)

    btc_meta = benchmarks._init_btc_hold(storage)
    alt_meta = benchmarks._init_alt_hold(storage)
    if not btc_meta or not alt_meta:
        print("ERROR: hold-book initialization failed "
              f"(btc={bool(btc_meta)}, alt={bool(alt_meta)}); aborting")
        return 1

    # 4. First synchronized snapshot for all books (random seeds initialize
    # their own PaperTrader books lazily on this first cycle, all at `now`).
    symbols = [s for s in alt_meta["legs"]] + [config.SYMBOL]
    result = benchmarks.run_benchmark_cycle(
        storage, symbols, strategy_equity=config.PAPER_STARTING_CAPITAL, now_ts=now,
    )

    decisions.log("research", "benchmark_baseline_start", "all_books", detail={
        "reset_ts": now,
        "starting_capital": config.PAPER_STARTING_CAPITAL,
        "positions_closed_at_reset": closed,
        "btc_hold": {"entry_price": btc_meta["start_price"], "qty": btc_meta["qty"]},
        "alt_hold": {"basket": sorted(alt_meta["legs"]), "legs": len(alt_meta["legs"])},
        "random_books": benchmarks.N_RANDOM_SEEDS,
        "first_cycle_snapshots": result.get("snapshots"),
        "engine": "post exit-unification (exits.py) + execution-cost model",
    })
    print(f"BASELINE RESET DONE ts={now} snapshots={result.get('snapshots')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
