"""End-to-end test of the promotion engine against a temp DB.

    python scripts/test_promotion.py
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
os.environ["PROMO_S2_MIN_FRESH_DAYS"] = "14"
os.environ["PROMO_ROLLBACK_MIN_TRADES"] = "5"

from src.data.storage import get_storage  # noqa: E402
from src.research import promotion  # noqa: E402

NOW = int(time.time())
DAY = 86_400
passed = 0


def check(name: str, cond: bool, detail: str = ""):
    global passed
    print(f"[{'OK ' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        sys.exit(1)
    passed += 1


def metrics(windows_pnl: list[float], trades=60, expectancy=1.5, pf=1.5) -> str:
    import random as _r
    rng = _r.Random(5)
    per_w = max(trades // max(len(windows_pnl), 1), 1)
    windows = []
    for p in windows_pnl:
        mu = p / per_w
        windows.append({
            "total_pnl": p,
            "trade_pnls": [round(rng.gauss(mu, abs(mu) * 0.5 or 0.5), 4) for _ in range(per_w)],
        })
    return json.dumps({
        "windows": windows,
        "aggregate": {"trade_count": trades, "expectancy": expectancy, "profit_factor": pf},
    })


def insert_experiment(h: str, cfg: dict, m: str, champion=False, created=NOW - 20 * DAY):
    with get_storage()._connect() as conn:
        conn.execute(
            "INSERT INTO experiments (config_hash, config_json, status, "
            "is_champion_baseline, created_at, finished_at, metrics_json) "
            "VALUES (?, ?, 'done', ?, ?, ?, ?)",
            (h, json.dumps(cfg), 1 if champion else 0, created, NOW, m),
        )


def insert_fresh(h: str, start: int, end: int, trades: int, pnl: float):
    with get_storage()._connect() as conn:
        conn.execute(
            "INSERT INTO fresh_evals (config_hash, eval_start, eval_end, trade_count, "
            "expectancy, profit_factor, total_pnl, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (h, start, end, trades, pnl / max(trades, 1), 1.4, pnl, NOW),
        )


promotion._ensure_schema()

# champion baseline: mediocre but positive
insert_experiment("champ0000", {"MEANREV_RSI_HIGH": 70}, metrics([10, 5, -2, 8], expectancy=0.5), champion=True)

# challenger A: inconsistent windows -> stage 1 fail
insert_experiment("aaaa1111", {"MEANREV_RSI_HIGH": 75}, metrics([50, -30, -20, 40]))
# challenger B: consistent, beats champion, but no fresh data -> stage 2 fail
insert_experiment("bbbb2222", {"MEANREV_RSI_HIGH": 72}, metrics([20, 15, 10, -1], expectancy=2.0))
# challenger C: passes everything, includes a forbidden key that must be stripped
insert_experiment(
    "cccc3333",
    {"MEANREV_RSI_HIGH": 68, "CONFLUENCE_ALIGNED_BONUS": 0.06, "DATABASE_URL": "hack", "ALLOW_LONG": "true"},
    metrics([25, 18, 12, 9], expectancy=2.5),
)
insert_fresh("cccc3333", NOW - 16 * DAY, NOW - 8 * DAY, 8, 40.0)
insert_fresh("cccc3333", NOW - 8 * DAY, NOW - 1 * DAY, 7, 25.0)

result = promotion.promote_if_ready()
check("promotes the fully-gated challenger", result.get("promoted") and result["hash"] == "cccc3333", str(result.get("hash")))
check("forbidden keys stripped", "DATABASE_URL" in result["rejected_keys"] and "ALLOW_LONG" in result["rejected_keys"], str(result["rejected_keys"]))

overrides = json.loads((Path(os.environ["DATA_DIR"]) / "config_overrides.json").read_text())
check("overrides file written with whitelisted keys only",
      overrides == {"MEANREV_RSI_HIGH": 68, "CONFLUENCE_ALIGNED_BONUS": 0.06}, str(overrides))
check("restart flag touched", (Path(os.environ["DATA_DIR"]) / "restart.flag").exists())

# blocked reasons must be visible for the losers (multiple-testing guard)
result2 = promotion.promote_if_ready()
check("cooldown blocks immediate re-promotion", not result2["promoted"], result2["reason"])

# health: 6 losing live trades since promotion -> auto rollback
with get_storage()._connect() as conn:
    for i in range(6):
        conn.execute(
            "INSERT INTO paper_trades (symbol, direction, entry_price, quantity, status, "
            "pnl, opened_at, closed_at) VALUES ('BTC/USDT', 'SHORT', 100, 1, 'closed', ?, ?, ?)",
            (-15.0, NOW, NOW + i + 1),
        )
health = promotion.health_check()
check("health check auto-rolls back on negative expectancy", health["status"] == "rolled_back", str(health.get("restored")))

restored = json.loads((Path(os.environ["DATA_DIR"]) / "config_overrides.json").read_text())
check("rollback restored previous (empty) overrides", restored == {}, str(restored))

with get_storage()._connect() as conn:
    audit = conn.execute("SELECT action FROM promotions ORDER BY id").fetchall()
check("audit trail has promote then rollback", [a[0] for a in audit] == ["promote", "rollback"], str([a[0] for a in audit]))

print(f"\nAll {passed} checks passed.")
