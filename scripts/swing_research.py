#!/usr/bin/env python3
"""Swing research run (daily bars, 2020 onward). Writes
data/reports/swing/latest.{json,md} and publishes the best rules to
system_status["swing_rules"] for the dashboard.

    .venv/bin/python scripts/swing_research.py
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.storage import get_storage  # noqa: E402
from src.research import swing_study as ss  # noqa: E402

OUT = PROJECT_ROOT / "data" / "reports" / "swing"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("swing_research")


def _d(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def main() -> int:
    t0 = time.time()
    storage = get_storage()
    p = ss.load_daily(storage)
    log.info("daily panel %s in %.0fs", p["close"].shape, time.time() - t0)
    res = ss.run_study(p, log=log.info)
    f = res.pop("features")
    rs = res["results"]
    validated = [r for r in rs if r["validated"]]
    picks = validated[:3] or [r for r in rs if r["train_mean_net"] > 0 and r["train_trades"] >= 60
                              and r["test_trades"] >= ss.MIN_TEST_TRADES][:3]
    books = {}
    for r in picks:
        books[r["rule"]] = ss.portfolio(p, f, r)
        log.info("portfolio %s: test %s vs btc %s", r["rule"], books[r["rule"]]["test"], books[r["rule"]]["btc_test"])
    hold_books = ss.run_books(p, f)
    for k, b in hold_books["books"].items():
        log.info("book %-26s train %+.1f%% SR %.2f | test %+.1f%% SR %.2f DD %.0f%% %s", k,
                 b["train"]["total"] * 100, b["train"]["sharpe"], b["test"]["total"] * 100, b["test"]["sharpe"],
                 b["test"]["max_dd"] * 100, "BEATS BTC" if b["beats_btc_test"] else "")
    clean = [{k: v for k, v in r.items() if k != "entry"} | {"side": r["entry"]["side"]} for r in rs]
    report = {"run_at": int(time.time()), "elapsed_s": round(time.time() - t0, 1),
              **{k: v for k, v in res.items() if k != "results"},
              "validated": len(validated), "top_rules": clean[:40], "portfolios": books,
              "books": hold_books["books"]}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "latest.json").write_text(json.dumps(report, default=float))
    (OUT / "latest.md").write_text(render(report))
    storage.set_system_status("swing_rules", json.dumps({
        "run_at": report["run_at"], "validated": [r for r in rs if r["validated"]][:5],
        "picks": [{**r, "portfolio": {k: v for k, v in books[r["rule"]].items() if "equity" not in k}} for r in picks],
    }, default=float))
    log.info("done in %.0fs: %d rules, %d validated", time.time() - t0, len(rs), len(validated))
    return 0


def render(rep: dict) -> str:
    pc = lambda v: "n/a" if v is None else f"{v * 100:+.2f}%"  # noqa: E731
    lines = [f"# Swing research", "",
             f"{rep['symbols']} coins, daily bars {_d(rep['from'])} to {_d(rep['to'])}, "
             f"test period from {_d(rep['split_ts'])}. {rep['rules_tested']} rules, **{rep['validated']} validated**.",
             "", "## Best rules (net of 0.3% round trip + 0.02%/day funding)", "",
             "| rule | trades | hold d | train net/trade | train t | test net/trade | test t | test win | q | ok |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|:-:|"]
    for r in rep["top_rules"][:25]:
        lines.append(f"| `{r['rule']}` | {r['trades']} | {r['median_hold_days']:.0f} | {pc(r['train_mean_net'])} | "
                     f"{r['train_t']:.2f} | {pc(r['test_mean_net'])} | {r['test_t']:.2f} | "
                     f"{(r['test_win_rate'] or 0) * 100:.0f}% | {r['q']:.3f} | {'✓' if r['validated'] else ''} |")
    lines += ["", "## Portfolio (10% per trade, max 10 open) vs holding BTC", "",
              "| rule | test return | test Sharpe | test max DD | BTC test return | BTC Sharpe | BTC max DD | full return |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for k, b in rep["portfolios"].items():
        lines.append(f"| `{k}` | {pc(b['test']['total'])} | {b['test']['sharpe']:.2f} | {pc(b['test']['max_dd'])} | "
                     f"{pc(b['btc_test']['total'])} | {b['btc_test']['sharpe']:.2f} | {pc(b['btc_test']['max_dd'])} | "
                     f"{pc(b['full']['total'])} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
