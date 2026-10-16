#!/usr/bin/env python3
"""Run the alpha lab (src/research/alpha_lab.py) and write
data/reports/alpha/latest.{json,md} + system_status["alpha_lab"]."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.storage import get_storage  # noqa: E402
from src.research import alpha_lab as al  # noqa: E402

OUT = PROJECT_ROOT / "data" / "reports" / "alpha"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("alpha_lab")


def main() -> int:
    storage = get_storage()
    rep = al.run(storage, log=log.info)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "latest.json").write_text(json.dumps(rep, default=float))
    (OUT / "latest.md").write_text(render(rep))
    slim = {k: rep[k] for k in ("run_at", "rows", "coins", "days", "from", "to", "base_up10", "base_dn10",
                                "interactions", "model", "backtests")}
    slim["univariate"] = rep["univariate"][:25]
    slim["clusters"] = rep["correlation"]["clusters"]
    slim["by_size"] = rep["by_size"]
    storage.set_system_status("alpha_lab", json.dumps(slim, default=float))
    log.info("done in %.0fs", rep["elapsed_s"])
    return 0


def _d(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def render(r: dict) -> str:
    f2 = lambda v: "n/a" if v is None else f"{v:+.3f}"  # noqa: E731
    pc = lambda v: "n/a" if v is None else f"{v * 100:+.2f}%"  # noqa: E731
    L = [f"# 알파 리서치 (24시간 뒤 수익률, 10% 이상 움직임)", "",
         f"{r['coins']}개 코인, {_d(r['from'])} ~ {_d(r['to'])}, {r['days']}일, 표본 {r['rows']:,}. "
         f"기준 확률: 24시간 +10% {r['base_up10'] * 100:.1f}%, -10% {r['base_dn10'] * 100:.1f}%.", "",
         "## 1. 신호 하나씩 (일별 순위 IC, 앞/뒤 절반)", "",
         "| 신호 | 분류 | IC 전체 | t | IC 앞 | IC 뒤 | +10% AUC 앞/뒤 | -10% AUC 앞/뒤 | 일관 |", "|---|---|---:|---:|---:|---:|---:|---:|:-:|"]
    for u in r["univariate"][:30]:
        L.append(f"| {u['ko']} (`{u['name']}`) | {u['category']} | {f2(u['ic_all'])} | {f2(u['t_all'])} | {f2(u['ic_h1'])} | "
                 f"{f2(u['ic_h2'])} | {f2(u['up10_auc_h1'])}/{f2(u['up10_auc_h2'])} | {f2(u['dn10_auc_h1'])}/{f2(u['dn10_auc_h2'])} | "
                 f"{'✓' if u['ic_holds'] else ''} |")
    L += ["", "## 2. 같은 말을 하는 신호 묶음 (순위상관 0.7 이상)", ""]
    for cl in r["correlation"]["clusters"]:
        if len(cl) > 1:
            L.append("- " + ", ".join(cl))
    L += ["", "## 3. 시가총액별 신호 강도 (IC)", "", "| 신호 | 소형 | 중형 | 대형 |", "|---|---:|---:|---:|"]
    for s in r["by_size"]:
        L.append(f"| `{s['name']}` | {f2(s['small'])} | {f2(s['mid'])} | {f2(s['large'])} |")
    L += ["", "## 4. 조건별 다음 24시간 (앞 / 뒤 절반)", "",
          "| 조건 | 표본 | 평균 앞 | 평균 뒤 | +10% 앞 | +10% 뒤 | -10% 앞 | -10% 뒤 |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for i in r["interactions"]:
        L.append(f"| {i['condition']} | {i['n']} | {pc(i['h1_mean'])} | {pc(i['h2_mean'])} | {pc(i['h1_up10'])} | "
                 f"{pc(i['h2_up10'])} | {pc(i['h1_dn10'])} | {pc(i['h2_dn10'])} |")
    m = r["model"]
    L += ["", "## 5. 전체 신호 모델 (워크포워드, 과거로만 학습)", ""]
    for lab, name in (("up10", "+10%"), ("dn10", "-10%")):
        x = m.get(lab)
        if not x:
            continue
        L.append(f"- {name}: AUC {f2(x['auc'])}, 기준 {x['base_rate'] * 100:.1f}% → 상위 1% 적중 {x['precision_top1'] * 100:.1f}% "
                 f"({x['lift_top1']:.1f}배), 상위 5% {x['precision_top5'] * 100:.1f}%, 상위 1% 평균 24h 수익 {pc(x['mean_fwd_top1'])}")
        if x.get("importance"):
            L.append("  - 모델이 쓰는 신호: " + ", ".join(f"{a} ({b:+.3f})" for a, b in x["importance"][:10]))
    L += ["", "| 매일 매매 | 일수 | 롱 평균 | 롱 승률 | 숏 평균 | 숏 승률 | 롱숏 평균 | t |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for k, b in (m.get("books") or {}).items():
        L.append(f"| {k} | {b['days']} | {pc(b['long_mean'])} | {b['long_win'] * 100:.0f}% | {pc(b['short_mean'])} | "
                 f"{b['short_win'] * 100:.0f}% | {pc(b['ls_mean'])} | {f2(b['ls_t'])} |")
    bt = r.get("backtests") or {}
    names = {"quiet_long": "조용한 코인 롱 (하위 10%)", "noisy_short": "시끄러운 코인 숏 (상위 10%)",
             "quiet_vs_noisy": "조용 롱 + 시끄러움 숏", "leverage_long": "레버리지 상위 5% 롱",
             "breakout_5": "변동성 상위 10개 양방향 돌파 ±5%", "breakout_8": "양방향 돌파 ±8%",
             "universe": "전체 코인 평균 (비교용, 비용 없음)", "oidv_long": "미결제약정÷거래대금 상위 5% 롱 (시총 불필요)", "leverage_long_all": "(시총 있는 코인 비율)",
             "leverage_long_supply": "레버리지 상위 5% 롱 (옛 방식: 현재 공급량×가격)",
             "leverage_long_cg": "레버리지 상위 5% 롱 (코인게코 시점 시총만)"}
    L += ["", f"## 6. 매일 매매 백테스트 ({bt.get('days', 0)}일, 00:00 UTC 진입, 24시간 보유, 왕복 0.3%)", "",
          "| 전략 | 하루 평균 | t | 승률 | 누적 | 최대낙폭 | 앞 절반 평균 | 뒤 절반 평균 |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for k, v in bt.items():
        if not isinstance(v, dict) or "all" not in v or k == "leverage_long_all":
            continue
        a, h1, h2 = v["all"], v.get("h1", {}), v.get("h2", {})
        L.append(f"| {names.get(k, k)} | {pc(a['mean'])} | {a['t']:.2f} | {a['win'] * 100:.0f}% | {pc(a['total'])} | "
                 f"{pc(a['max_dd'])} | {pc(h1.get('mean'))} | {pc(h2.get('mean'))} |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
