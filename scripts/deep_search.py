#!/usr/bin/env python3
"""Deep search for +10% precursors on 1-minute data (all coins, ~6 months).

  .venv/bin/python scripts/deep_search.py            # rebuild panel + all tests
  .venv/bin/python scripts/deep_search.py --reuse    # reuse data/cache/deep_panel.parquet

Writes data/reports/deep/latest.json and latest.md, system_status deep_search.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from src.data.storage import get_storage  # noqa: E402
from src.research import deep_search as ds  # noqa: E402
from src.research import deep_tests as dt  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("deep")
OUT = PROJECT_ROOT / "data" / "reports" / "deep"


def pc(v, d=1):
    return "—" if v is None else f"{v * 100:+.{d}f}%"


def f2(v, d=2):
    return "—" if v is None else f"{v:.{d}f}"


def report(r: dict) -> str:
    T = {"up10_24h": "24시간 안에 +10%", "win10_24h": "24시간 안에 -5% 전에 +10%", "up10_4h": "4시간 안에 +10%",
         "win10_4h": "4시간 안에 -5% 전에 +10%", "up10_1h": "1시간 안에 +10%", "dn10_24h": "24시간 안에 -10%",
         "dir_24h": "방향만: 10% 움직인 코인 중 위로 간 쪽"}
    L = [f"# 1분봉 딥서치: +10% 전조 ({r['coins']}개 코인, {r['rows']:,}개 코인·시간)", "",
         f"- 기간 {pd.Timestamp(r['from'], unit='s'):%Y-%m-%d} ~ {pd.Timestamp(r['to'], unit='s'):%Y-%m-%d}, "
         f"테스트 {pd.Timestamp(r['test_from'], unit='s'):%Y-%m-%d}~ (학습에서 고른 것만 테스트에서 채점)",
         f"- 지표 {r['features']}개, 소요 {r['elapsed_s'] // 60}분", "", "## 기준 확률 (아무 코인, 아무 시간)", ""]
    for t, b in r["base_rates"].items():
        L.append(f"- {T[t]}: 학습 {b['train'] * 100:.2f}% / 테스트 {b['test'] * 100:.2f}%")
    L += ["", "## 1. 모델: 지표끼리 얽히는 게 도움이 되나 (상호작용 깊이)", "",
          "깊이 1 = 지표 하나씩 더하기만, 깊이 2~6 = 지표 2~6개 조합까지 허용. 테스트 AUC와 상위 1% 적중률.", ""]
    for t, m in r["models"].items():
        L.append(f"**{T[t]}** (기준 {m['full']['base'] * 100:.2f}%)")
        L.append("")
        L.append("| 깊이 | AUC | 상위 0.1% 적중 | 상위 1% 적중 | 같은 픽 -10% | 상위 1% 거래 기대값 |")
        L.append("|---|---:|---:|---:|---:|---:|")
        for d in m["depth"] + [m["full"] | {"depth": "제한 없음"}]:
            L.append(f"| {d['depth']} | {f2(d['auc'], 3)} | {pc(d.get('prec_top0.001'))} | {pc(d.get('prec_top0.01'))} | "
                     f"{pc(d.get('dn10_top0.01'))} | {pc(d.get('ev_top0.01'), 2)} |")
        L.append("")
        L.append("지표 개수별 (중요도 순 상위 k개만 사용):")
        L.append("")
        L.append("| k | AUC | 상위 1% 적중 | 상위 1% 기대값 | 지표 |")
        L.append("|---:|---:|---:|---:|---|")
        for k in m["k"]:
            L.append(f"| {k['n_feats']} | {f2(k['auc'], 3)} | {pc(k.get('prec_top0.01'))} | {pc(k.get('ev_top0.01'), 2)} | "
                     f"{', '.join(k['feats'][:5])}{' …' if len(k['feats']) > 5 else ''} |")
        L.append("")
        L.append("시간 단위 / 지표 계열별 단독 모델:")
        L.append("")
        L.append("| 계열 | 지표 수 | AUC | 상위 1% 적중 |")
        L.append("|---|---:|---:|---:|")
        for fm in sorted(m["families"], key=lambda x: -(x["auc"] or 0)):
            L.append(f"| {fm['family']} | {fm['n_feats']} | {f2(fm['auc'], 3)} | {pc(fm.get('prec_top0.01'))} |")
        L.append("")
        L.append("중요 지표 상위 12: " + ", ".join(f"{f} {v * 100:.0f}%" for f, v in m["importance"][:12]))
        if m.get("stability"):
            L.append("")
            L.append("테스트 구간 안정성 (AUC): " + ", ".join(f"{s['part']} {f2(s['auc'], 3)}" for s in m["stability"]))
        L.append("")
    L += ["## 0. 급등 직전에 무엇이 튀었나 (사건 프로필)", "",
          "각 급등 시점에 그 지표가 같은 시각 전체 코인 중 몇 %ile이었나 (50 = 보통). 급락 사건과 차이가 커야 '방향' 신호.", ""]
    for hz, e in r.get("events", {}).items():
        L.append(f"**{hz} 안에 +10%: {e['n_up']}건 / -10%: {e['n_down']}건 (코인·일 첫 사건)**")
        L.append("")
        L.append("| 지표 | 6시간 전 | 3시간 전 | 1시간 전 | 직전 | 급락 직전 | 급등-급락 차이 |")
        L.append("|---|---:|---:|---:|---:|---:|---:|")
        fs = sorted(e["features"].items(), key=lambda kv: -abs(kv[1].get("gap_0h", 0)))
        for f, d in fs[:15]:
            L.append(f"| {f} | {f2(d.get('up_med_6h'), 0)} | {f2(d.get('up_med_3h'), 0)} | {f2(d.get('up_med_1h'), 0)} | "
                     f"{f2(d.get('up_med_0h'), 0)} | {f2(d.get('down_med_0h'), 0)} | {d.get('gap_0h', 0):+.0f} |")
        L.append("")
        L.append("급등·급락 둘 다에서 튀는 것 (방향은 모르고 '곧 크게 움직인다'만 알려줌): " + ", ".join(
            f"{f} ({d.get('up_med_0h', 0):.0f}/{d.get('down_med_0h', 0):.0f})"
            for f, d in sorted(e["features"].items(), key=lambda kv: -min(kv[1].get('up_med_0h', 50), kv[1].get('down_med_0h', 50)))[:8]))
        L.append("")
    L += ["## 2. 3개 vs 4개 지표 조합 전수 탐색 (상위 10개 지표에서)", ""]
    for t, s in r.get("subsets", {}).items():
        L.append(f"**{T[t]}** ({s['tried']}개 모델)")
        L.append("")
        L.append("| 조합 | AUC | 상위 1% 적중 | 상위 1% 기대값 |")
        L.append("|---|---:|---:|---:|")
        for x in s["best3"][:5] + s["best4"][:5]:
            L.append(f"| {' + '.join(x['feats'])} | {f2(x['auc'], 3)} | {pc(x.get('prec_top0.01'))} | {pc(x.get('ev_top0.01'), 2)} |")
        L.append("")
    L += ["## 3. 조건 조합 규칙 (구간 조합 전수 탐색)", "",
          "각 지표를 5등분하고 2개/3개/4개 조건이 동시에 맞는 칸을 전부 셈. 학습에서 제일 좋았던 칸을 테스트에서 채점."
          " 통과 = 테스트 FDR q<0.05, 기준 대비 1.5배 이상, 서로 다른 코인·일 10개 이상.", ""]
    for t, c in r["cells"].items():
        L.append(f"**{T[t]}**")
        L.append("")
        L.append("| 조건 수 | 채점한 칸 | 통과 | 테스트 1.5배 이상 비율 | 학습 배수 중앙값 | 테스트 배수 중앙값 |")
        L.append("|---|---:|---:|---:|---:|---:|")
        for k, v in c.items():
            L.append(f"| {k} | {v['judged']} | {v['holds']} | {pc(v['share_test_lift_1_5'], 0)} | "
                     f"{f2(v['median_train_lift'])} | {f2(v['median_test_lift'])} |")
        L.append("")
        best = [b for v in c.values() for b in v["best"] if b["holds"]]
        best.sort(key=lambda b: -(b["test_lift"] or 0))
        for b in best[:8]:
            L.append(f"- {b['rule']} → 테스트 {pc(b['test_rate'])} ({f2(b['test_lift'])}배, {b['test_pos_units']}건), "
                     f"거래 기대값 {pc(b['test_trade_ev'], 2)}")
        if not best:
            L.append("- 통과한 조합 없음")
        L.append("")
    if r.get("korea"):
        k = r["korea"]
        L += [f"## 4. 한국 거래소 지표 (최근 {(k['rows'])} 코인·시간)", ""]
        for t in ("up10_24h", "up10_4h", "win10_24h"):
            if t in k:
                a, b = k[t]["without"], k[t]["with"]
                L.append(f"- {T[t]}: 한국 지표 없이 AUC {f2(a['auc'], 3)} → 포함 {f2(b['auc'], 3)}; 상위 1% 적중 "
                         f"{pc(a.get('prec_top0.01'))} → {pc(b.get('prec_top0.01'))}")
        L.append("")
    L += ["## 부록: 단일 지표 상위 (24시간 +10%, 테스트 AUC)", "", "| 지표 | 학습 AUC | 테스트 AUC | 상위 10% 배수 | 하위 10% 배수 |",
          "|---|---:|---:|---:|---:|"]
    uni = sorted(r["univariate"], key=lambda u: -abs((u.get("auc_up10_24h_test") or 0.5) - 0.5))
    for u in uni[:25]:
        L.append(f"| {u['feature']} | {f2(u.get('auc_up10_24h_train'), 3)} | {f2(u.get('auc_up10_24h_test'), 3)} | "
                 f"{f2(u.get('top10_up10_24h'))} | {f2(u.get('bot10_up10_24h'))} |")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse", action="store_true")
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--no-korea", action="store_true", help="skip Korean features (backfill not finished)")
    ap.add_argument("--add-korea", action="store_true", help="with --reuse: (re)attach Korean features first")
    a = ap.parse_args()
    t0 = time.time()
    if a.reuse and ds.PANEL.exists():
        p = pd.read_parquet(ds.PANEL)
        if a.add_korea:
            p = ds.add_korea(p)
            p.to_parquet(ds.PANEL, index=False)
    else:
        p = ds.build_panel(get_storage(), a.days, a.workers, log.info, korea=not a.no_korea)
    log.info("panel %s built in %.0fs", p.shape, time.time() - t0)
    feats = ds.feature_cols(p)
    r = dt.run_all(p, feats, log.info)
    OUT.mkdir(parents=True, exist_ok=True)

    def clean(o):
        if isinstance(o, dict):
            return {str(k): clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [clean(v) for v in o]
        if isinstance(o, (np.floating, float)):
            return None if not np.isfinite(o) else float(o)
        if isinstance(o, np.integer):
            return int(o)
        return o

    r = clean(r)
    (OUT / "latest.json").write_text(json.dumps(r, ensure_ascii=False))
    (OUT / "latest.md").write_text(report(r))
    get_storage().set_system_status("deep_search", json.dumps({k: r[k] for k in ("rows", "coins", "features", "test_from",
                                                                                   "elapsed_s", "base_rates")}))
    log.info("deep search done in %.0fs", time.time() - t0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
