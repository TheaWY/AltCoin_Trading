#!/usr/bin/env python3
"""Which of the 125 indicators move BEFORE pumps and before dumps?

Streams every indicator in src/research/indicators.py over the 1-minute panel
(default 14 days) and tests it against pump onsets (+8% within 60 min) and
dump onsets (-8% within 60 min), train/test split in time. Writes
data/reports/indicators/latest.{json,md} and system_status["indicator_study"].

    .venv/bin/python scripts/indicator_study.py [--days 14]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.storage import get_storage  # noqa: E402
from src.research import indicators as ind  # noqa: E402
from src.research import pump_precursors as pp  # noqa: E402

OUT = PROJECT_ROOT / "data" / "reports" / "indicators"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("indicator_study")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args()
    t0 = time.time()
    storage = get_storage()
    ctx = ind.build_ctx(storage, args.days * 1440)
    p = ctx.p
    log.info("panel %s, aux %s in %.0fs", p["close"].shape, sorted(ctx.aux), time.time() - t0)
    ons = {"pump": pp.onsets(p, 1), "dump": pp.onsets(p, -1)}
    log.info("onsets: pump %d, dump %d", int(ons["pump"].to_numpy().sum()), int(ons["dump"].to_numpy().sum()))
    cat = {i.name: i for i in ind.REGISTRY}

    def stream():
        for i, (d, f) in enumerate(ind.iter_indicators(ctx)):
            if i % 20 == 0:
                log.info("indicator %d/%d %s", i + 1, len(ind.REGISTRY), d.name)
            yield d.name, d.ko, f

    res = pp.study_stream(p, stream(), 0.7, ons)
    for side in res.values():
        for r in side["precursors"]:
            r["category"] = cat[r["feature"]].cat
            r["coverage"] = r.get("train_onsets", 0) + r.get("test_onsets", 0)
    report = {"run_at": int(time.time()), "elapsed_s": round(time.time() - t0, 1), "days": args.days,
              "indicators": len(ind.REGISTRY), "catalog": ind.catalog(), **res}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "latest.json").write_text(json.dumps(report, default=float))
    (OUT / "latest.md").write_text(render(report))
    slim = {k: {"onsets": v["onsets_total"], "holds": [r for r in v["precursors"] if r["holds"]][:30]}
            for k, v in res.items()}
    storage.set_system_status("indicator_study", json.dumps({"run_at": report["run_at"], **slim}, default=float))
    log.info("done in %.0fs: pump holds %d, dump holds %d", time.time() - t0,
             len(slim["pump"]["holds"]), len(slim["dump"]["holds"]))
    return 0


def render(rep: dict) -> str:
    f2 = lambda v: "n/a" if v is None else f"{v:.2f}"  # noqa: E731
    lines = [f"# 지표 {rep['indicators']}개 전조 분석", "",
             f"{rep['days']}일 1분봉. AUC 0.5 = 무관, >0.55 높을수록 사건 전, <0.45 낮을수록 사건 전. "
             "학습·테스트 모두 같은 방향이어야 ✓.", ""]
    for side, label in (("pump", "급등 (+8%/60분) 전 vs 평소"), ("dump", "급락 (-8%/60분) 전 vs 평소"),
                        ("direction", "큰 움직임이 시작될 때 위/아래를 가르는 지표 (급등 전 vs 급락 전; 중앙값 = 급등 / 급락)")):
        if side not in rep:
            continue
        s = rep[side]
        lines += [f"## {label}: {s['onsets_total']}건", "",
                  "| 지표 | 분류 | AUC 학습 | AUC 테스트 | 상위10% 배율 | 하위10% 배율 | 사건 시 중앙값 | 평소 중앙값 | ✓ | 표본 |",
                  "|---|---|---:|---:|---:|---:|---:|---:|:-:|---:|"]
        for r in s["precursors"][:40]:
            lines.append(f"| {r['text']} (`{r['feature']}`) | {r['category']} | {f2(r['train_auc'])} | {f2(r['test_auc'])} | "
                         f"{f2(r['test_lift_top10'])} | {f2(r['test_lift_bottom10'])} | {f2(r['test_median_at_onset'])} | "
                         f"{f2(r['test_median_base'])} | {'✓' if r['holds'] else ''} | {r['coverage']} |")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
