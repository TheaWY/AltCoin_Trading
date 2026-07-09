"""Tests for the research stack: event study, generator, runner (real
subprocess into the actual backtester), paper analysis, data quality.

    python scripts/test_research_stack.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_tmp = Path(tempfile.mkdtemp())
os.environ["DATABASE_PATH"] = str(_tmp / "test.db")
os.environ["DATABASE_URL"] = ""
os.environ["DATA_DIR"] = str(_tmp)
os.environ["SYMBOL_UNIVERSE"] = "static"
os.environ["RESEARCH_SYMBOLS"] = "BTC/USDT"
os.environ["RESEARCH_WINDOW_COUNT"] = "2"
os.environ["RESEARCH_WINDOW_TEST_DAYS"] = "10"

from src.data.storage import get_storage  # noqa: E402

NOW = int(time.time())
HOUR, DAY = 3600, 86_400
passed = 0


def check(name: str, cond: bool, detail: str = ""):
    global passed
    print(f"[{'OK ' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        sys.exit(1)
    passed += 1


def seed_prices(symbol: str, hours: int, base: float, pattern: str = "flat"):
    rows, price = [], base
    start = NOW - hours * HOUR
    for i in range(hours):
        if pattern == "flat":
            price = base * (1 + 0.002 * ((i % 7) - 3) / 3)
        elif pattern == "crash_after_events":
            price = base if i < hours - 72 else price * 0.995
        rows.append({
            "symbol": symbol, "timestamp": start + i * HOUR,
            "open": price, "high": price * 1.004, "low": price * 0.996,
            "close": price, "volume": 1000.0 + (5000.0 if i % 200 == 0 else 0.0),
        })
    get_storage().insert_prices(rows, timeframe="1h")


# ---------------------------------------------------------------- event study
from src.research import event_study  # noqa: E402

seed_prices("BTC/USDT", 24 * 200, 60_000)
get_storage().insert_funding_rates([
    {"symbol": "BTC/USDT", "timestamp": NOW - i * 8 * HOUR,
     "funding_rate": 0.0001 if i % 25 else 0.0009}
    for i in range(500)
])
with get_storage()._connect() as conn:
    for i in range(24 * 200):
        conn.execute(
            "INSERT OR IGNORE INTO market_metrics (symbol, timestamp, open_interest, "
            "long_short_ratio) VALUES (?, ?, ?, ?)",
            ("BTC/USDT", NOW - i * HOUR, 80_000 + (i % 50) * 100,
             1.5 if i % 30 else 3.2),
        )

results = event_study.run(["BTC/USDT"], days=200)
check("event study produces results", len(results) > 0, f"{len(results)} rows")
signals_seen = {r["signal"] for r in results}
check("all 5 signals evaluated", len(signals_seen) == 5, str(sorted(signals_seen)))
with get_storage()._connect() as conn:
    n = conn.execute("SELECT COUNT(*) FROM event_study_results").fetchone()[0]
check("results persisted to table", n == len(results), f"{n} rows")
verdicts = {r["verdict"] for r in results}
check("verdicts assigned", verdicts <= {"PASS", "fail", "insufficient_n", "context"}, str(verdicts))

# ------------------------------------------------------------------ generator
from src.research import generator  # noqa: E402

space = {
    "axes": {
        "MEANREV_RSI_HIGH": [68, 70],
        "CONFLUENCE_ALIGNED_BONUS": [0.03, 0.05],
        "SETUP_BREAKOUT_ENABLED": [True, False],
    },
    "priority_families": [
        {"name": "no_breakout", "match": {"SETUP_BREAKOUT_ENABLED": False}},
    ],
    "limits": {"max_new_per_run": 100},
}
space_file = _tmp / "space.yaml"
import yaml  # noqa: E402
space_file.write_text(yaml.safe_dump(space))

report = generator.generate(space_file)
check("generator enqueues full space", report["newly_queued"] >= 7,
      f"queued {report['newly_queued']} of space {report['total_space']}")
report2 = generator.generate(space_file)
check("re-run dedupes by hash", report2["newly_queued"] == 0, str(report2["newly_queued"]))
with get_storage()._connect() as conn:
    prio = conn.execute(
        "SELECT config_json FROM experiments WHERE is_champion_baseline=0 "
        "ORDER BY priority ASC LIMIT 1").fetchone()[0]
check("priority family sorts first", json.loads(prio)["SETUP_BREAKOUT_ENABLED"] is False, prio[:60])
with get_storage()._connect() as conn:
    champ = conn.execute(
        "SELECT COUNT(*) FROM experiments WHERE is_champion_baseline=1").fetchone()[0]
check("champion baseline enqueued", champ == 1)

# -------------------------------------------------- runner (real subprocess!)
from src.research import runner  # noqa: E402

# keep it small: clear challenger queue except 1, use short windows
with get_storage()._connect() as conn:
    conn.execute(
        "DELETE FROM experiments WHERE id NOT IN "
        "(SELECT id FROM experiments ORDER BY priority ASC LIMIT 2)")
os.environ["RESEARCH_HOLDOUT_START"] = time.strftime(
    "%Y-%m-%d", time.gmtime(NOW - 5 * DAY))

run_report = runner.run_experiments(max_runs=2)
check("runner executes experiments via subprocess",
      run_report["done"] >= 1, str(run_report))
with get_storage()._connect() as conn:
    row = conn.execute(
        "SELECT metrics_json FROM experiments WHERE status='done' LIMIT 1").fetchone()
metrics = json.loads(row[0])
check("walk-forward windows in metrics",
      len(metrics["windows"]) == 2 and "aggregate" in metrics,
      f"windows={len(metrics['windows'])} agg_keys={sorted(metrics['aggregate'])[:4]}")

# fresh evals: backdate created_at so a fresh window exists
with get_storage()._connect() as conn:
    conn.execute("UPDATE experiments SET created_at = ? WHERE is_champion_baseline=0",
                 (NOW - 3 * DAY,))
fresh_report = runner.run_fresh_evals()
check("fresh evals recorded", fresh_report["fresh_evaluated"] >= 1, str(fresh_report))

# ---------------------------------------------------------------- paper data
from src.research import paper_analysis  # noqa: E402

with get_storage()._connect() as conn:
    conn.execute(
        "INSERT INTO signals (strategy, symbol, timestamp, direction, metadata) "
        "VALUES ('mean_reversion','BTC/USDT',?, 'SHORT', ?)",
        (NOW - 5 * DAY, json.dumps({"confidence": 0.7})))
    sig_id = conn.execute("SELECT MAX(id) FROM signals").fetchone()[0]
    for i in range(4):
        conn.execute(
            "INSERT INTO paper_trades (signal_id, symbol, direction, entry_price, "
            "quantity, stop_loss, take_profit, status, pnl, fees, opened_at, closed_at, "
            "strategy, exit_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sig_id, "BTC/USDT", "SHORT", 60_000, 0.001, 61_000, 58_000, "closed",
             12.0 if i % 2 else -8.0, 0.5, NOW - (5 - i) * DAY,
             NOW - (5 - i) * DAY + 6 * HOUR, "mean_reversion",
             "take_profit" if i % 2 else "stop_loss"))

paper_report = paper_analysis.full_report()
check("paper report generates", paper_report["closed_trades"] == 4)
check("calibration buckets present",
      any(b["n"] > 0 for b in paper_report["calibration"]),
      str([(b['bucket'], b['n']) for b in paper_report['calibration']]))
check("exit autopsy has MAE/MFE", len(paper_report["exits"]["mae_mfe"]) == 4)
check("setup scorecard groups by strategy",
      "mean_reversion" in paper_report["setups"])
check("low-n flag honest", paper_report["setups"]["mean_reversion"]["low_n"] is True)

# --------------------------------------------------------------- data quality
from src.research import data_quality  # noqa: E402

# introduce a 6h gap in prices
with get_storage()._connect() as conn:
    conn.execute("DELETE FROM prices WHERE timestamp BETWEEN ? AND ?",
                 (NOW - 50 * HOUR, NOW - 45 * HOUR))
gaps = data_quality.scan_gaps(days=10)
check("gap scan finds the injected gap",
      any(g["gap_seconds"] >= 5 * HOUR for g in gaps), f"{len(gaps)} gaps")
v = data_quality.verdict()
check("verdict structure", "healthy" in v and "action" in v, v["action"])

print(f"\nAll {passed} checks passed.")
