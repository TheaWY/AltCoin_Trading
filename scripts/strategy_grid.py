#!/usr/bin/env python3
"""Sweep squeeze / fade / dip rules over windows, thresholds, filters,
holding times and stops (1620 rules). Writes data/reports/grid/latest.{json,md}.

    .venv/bin/python scripts/strategy_grid.py [--days 14]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.storage import get_storage  # noqa: E402
from src.research import indicators as ind  # noqa: E402
from src.research import strategy_grid as sg  # noqa: E402

OUT = PROJECT_ROOT / "data" / "reports" / "grid"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("strategy_grid")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--cost", type=float, default=sg.COST, help="round-trip cost (0.003 taker, ~0.001 maker)")
    args = ap.parse_args()
    sg.COST = args.cost
    global OUT
    if args.cost != 0.003:
        OUT = OUT.parent / f"grid_cost{args.cost:g}"
    t0 = time.time()
    storage = get_storage()
    ctx = ind.build_ctx(storage, args.days * 1440, with_daily=False)
    log.info("panel %s in %.0fs", ctx.c.shape, time.time() - t0)
    res = sg.run_grid(ctx, log=log.info)
    rules = res.pop("rules")
    picks = sg.pick(rules)
    validated = [r for r in rules if r["validated"]]
    fams = {}
    for fam in ("squeeze_long", "fade_short", "dip_long"):
        rs = [r for r in rules if r["family"] == fam and r["trades"] >= 30]
        fams[fam] = {"tried": len([r for r in rules if r["family"] == fam]),
                     "share_train_positive": round(sum(r["train_mean"] > 0 for r in rs) / max(1, len(rs)), 3),
                     "share_test_positive": round(sum(r["test_mean"] > 0 for r in rs) / max(1, len(rs)), 3),
                     "median_train": float(sorted(r["train_mean"] for r in rs)[len(rs) // 2]) if rs else None,
                     "median_test": float(sorted(r["test_mean"] for r in rs)[len(rs) // 2]) if rs else None}
    top_train = sorted([r for r in rules if r["train_trades"] >= 50], key=lambda r: -r["train_t"])[:25]
    report = {"run_at": int(time.time()), "elapsed_s": round(time.time() - t0, 1), **res,
              "validated": validated[:20], "picks": picks, "families": fams, "top_by_train": top_train}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "latest.json").write_text(json.dumps(report, default=float))
    (OUT / "all_rules.json").write_text(json.dumps(rules, default=float))
    (OUT / "latest.md").write_text(render(report))
    key = "strategy_grid" if args.cost == 0.003 else f"strategy_grid_cost{args.cost:g}"
    # forward-test candidates: the train-chosen best per family plus the top
    # stable rules by train t; the live shadow (src/engine/grid_shadow.py)
    # records every signal so the choice is judged on data it never saw
    # plus rules positive on BOTH halves (ranked by the weaker half's t): the
    # forward test is new data, so using the test half to nominate is fair
    both = sorted([r for r in rules if r["stable"] and r["train_mean"] > 0 and r["test_mean"] > 0
                   and r["train_trades"] >= 100], key=lambda r: -min(r["train_t"], r["test_t"]))
    shadow = {r["rule"]: r for r in picks + [r for r in top_train if r["stable"]][:5] + both[:4]}
    storage.set_system_status(key, json.dumps({**{k: report[k] for k in
                              ("run_at", "tried", "split_ts", "validated", "picks", "families")},
                              "cost": args.cost, "shadow": list(shadow.values())}, default=float))
    log.info("done in %.0fs: %d rules, %d validated", time.time() - t0, len(rules), len(validated))
    return 0


def _d(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m-%d %H:%M")


def render(r: dict) -> str:
    pc = lambda v: "n/a" if v is None else f"{v * 100:+.2f}%"  # noqa: E731
    row = lambda x: (f"| `{x['rule']}` | {x['train_trades']}/{x['test_trades']} | {pc(x['train_mean'])} | {x['train_t']:.2f} | "  # noqa: E731
                     f"{pc(x['test_mean'])} | {x['test_t']:.2f} | {(x['win_rate_test'] or 0) * 100:.0f}% | {x['q']:.3f} | "
                     f"{'✓' if x['stable'] else ''} | {'✓' if x['validated'] else ''} |")
    head = ["| rule | trades tr/te | train/trade | train t | test/trade | test t | test win | q | stable | valid |",
            "|---|---:|---:|---:|---:|---:|---:|---:|:-:|:-:|"]
    lines = ["# Strategy grid", "", f"{r['symbols']} coins, {_d(r['from'])} to {_d(r['to'])} UTC, test from {_d(r['split_ts'])}. "
             f"{r['tried']} rules, **{len(r['validated'])} validated**. Net of {sg.COST * 100:g}% round trip.", "",
             "## Families", "", "| family | rules | train>0 | test>0 | median train | median test |", "|---|---:|---:|---:|---:|---:|"]
    for k, f in r["families"].items():
        lines.append(f"| {k} | {f['tried']} | {f['share_train_positive'] * 100:.0f}% | {f['share_test_positive'] * 100:.0f}% | "
                     f"{pc(f['median_train'])} | {pc(f['median_test'])} |")
    lines += ["", "## Best per family (chosen on train only)", "", *head, *[row(x) for x in r["picks"]],
              "", "## Validated", "", *head, *[row(x) for x in r["validated"]],
              "", "## Top 25 by train t", "", *head, *[row(x) for x in r["top_by_train"]]]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
