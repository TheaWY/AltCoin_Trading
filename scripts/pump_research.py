#!/usr/bin/env python3
"""Pump research job (launchd: com.altcoin.pumps, every 2h).

1. precursor study: what the 1-minute tape looked like before real pumps
2. burst rules: fast-rise triggers x exit policies (pump_study)
3. precursor rules: enter when >= m of the precursors that held in BOTH
   halves of time are in their extreme decile (thresholds fitted on the
   first 70% only), same exit policies
4. validate every rule (BH on the train window + positive, t >= 1.5 on the
   untouched last 30%), publish system_status["pump_rules"] for the live
   rider, and write data/reports/pumps/latest.{md,json}
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.storage import get_storage  # noqa: E402
from src.research import pump_precursors as pp  # noqa: E402
from src.research import pump_study as ps  # noqa: E402

LOOKBACK_DAYS = 14
MAX_SHADOW_RULES = 6
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("pump_research")


def precursor_family(panel, pre: dict, split: int) -> list[tuple[str, dict, list]]:
    held = [r for r in pre["precursors"] if r["holds"]]
    if len(held) < 2:
        return []
    feats = pp.precursor_features(panel)
    liquid = panel["quote_volume"].rolling(60, min_periods=30).sum() >= ps.MIN_DOLLAR_VOL
    spec: dict[str, list] = {}
    flags = []
    for r in held:
        name = r["feature"]
        f = feats[name]
        base = f.iloc[:split].to_numpy()[liquid.iloc[:split].to_numpy(dtype=bool)]
        base = base[np.isfinite(base)]
        if len(base) < 1000:
            continue
        high = (r["train_auc"] or 0.5) > 0.5
        thr = float(np.quantile(base, 0.9 if high else 0.1))
        spec[name] = ["ge" if high else "le", thr]
        flags.append((f >= thr) if high else (f <= thr))
    if len(flags) < 2:
        return []
    score = sum(x.astype(int) for x in flags)
    out = []
    for m in sorted({2, 3, min(4, len(flags))}):
        mask = (score >= m) & liquid
        key = f"pre{m}of{len(flags)}"
        out.append((key, {"type": "precursor", "m": m, "features": spec}, ps.events(mask)))
    return out


def main() -> int:
    storage = get_storage()
    t0 = time.time()
    panel = ps.load_panel(storage, int(time.time()) - LOOKBACK_DAYS * 86400)
    if panel["close"].empty:
        log.warning("no 1m data yet")
        return 0
    panel = {k: v.astype("float32") for k, v in panel.items()}
    n = len(panel["close"].index)
    split = int(n * ps.TRAIN_FRAC)
    log.info("panel %d symbols x %d minutes", panel["close"].shape[1], n)

    pre = pp.study(panel, train_frac=ps.TRAIN_FRAC)
    log.info("precursors: %d onsets; %d hold on both halves", pre["onsets_total"],
             sum(r["holds"] for r in pre["precursors"]))
    extra = precursor_family(panel, pre, split)
    res = ps.run_study(panel, log=log.info, extra=extra)

    validated = res["validated"]
    shadow = [r for r in res["results"] if not r["validated"] and r.get("train_mean_net", -1) > 0
              and r["train_events"] >= ps.MIN_EVENTS][:MAX_SHADOW_RULES]
    live_rules = validated[:10] + shadow
    storage.set_system_status("pump_rules", json.dumps({"run_at": int(time.time()), "rules": live_rules},
                                                      default=float))
    out = PROJECT_ROOT / "data" / "reports" / "pumps"
    out.mkdir(parents=True, exist_ok=True)
    payload = {"run_at": int(time.time()), "elapsed_s": round(time.time() - t0, 1),
               "precursors": pre, "study": {k: v for k, v in res.items() if k != "results"},
               "top_rules": res["results"][:40]}
    (out / "latest.json").write_text(json.dumps(payload, default=float))
    (out / f"run_{payload['run_at']}.json").write_text(json.dumps(payload, default=float))
    (out / "latest.md").write_text(render(payload))
    log.info("done in %.0fs: %d rules, %d validated, %d shadow", time.time() - t0,
             res["rules_tested"], len(validated), len(shadow))
    return 0


def render(p: dict) -> str:
    from datetime import datetime, timezone

    ts = lambda x: datetime.fromtimestamp(x, timezone.utc).strftime("%m-%d %H:%M UTC") if x else "?"  # noqa: E731
    s, pre = p["study"], p["precursors"]
    lines = ["# Pump research", "",
             f"{s['symbols']} coins x {s['minutes']} minutes ({ts(s['from'])} to {ts(s['to'])}), "
             f"train/test split {ts(s['split_ts'])}. {s['rules_tested']} rules tested, "
             f"**{len(s['validated'])} validated**.", "",
             f"## What came before {pre['onsets_total']} pumps (+{pp.PUMP_MIN:.0%} within {pp.PUMP_WINDOW}m)", "",
             "| precursor | AUC train | AUC test | lift top10% | at onset | normal | holds |",
             "|---|---:|---:|---:|---:|---:|:-:|"]
    f = lambda v, d=2: "—" if v is None else f"{v:.{d}f}"  # noqa: E731
    for r in pre["precursors"]:
        lines.append(f"| {r['text']} | {f(r['train_auc'])} | {f(r['test_auc'])} | {f(r['test_lift_top10'])}x | "
                     f"{f(r['test_median_at_onset'], 3)} | {f(r['test_median_base'], 3)} | {'✓' if r['holds'] else ''} |")
    lines += ["", "## Best rules (net of 0.3% round-trip costs)", "",
              "| rule | events | median run-up | min to peak | test net/trade | test t | win | validated |",
              "|---|---:|---:|---:|---:|---:|---:|:-:|"]
    for r in p["top_rules"][:20]:
        lines.append(f"| `{r['rule']}` | {r['events']} | {r['mfe_median']:.1%} | {r['minutes_to_peak_median']:.0f} | "
                     f"{(r.get('test_mean_net') or 0):+.2%} | {f(r.get('test_t'))} | "
                     f"{f(r.get('test_win_rate'))} | {'✓' if r['validated'] else ''} |")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
