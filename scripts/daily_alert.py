"""Daily 09:15 KST alert text for 유리 (paper only). Prints a short Korean message to stdout.
Sections are added here (not in the scheduled task prompt) so the alert can grow without re-approving the task.
1) F15 trend allocation weights for today (BTC/ETH), change vs yesterday, forward P&L vs buy-and-hold.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from f2b_shadow_paper import q  # noqa: E402
from src.data.storage import get_storage  # noqa: E402


def f15(st):
    subprocess.run([sys.executable, str(ROOT / "scripts/f15_trend_paper.py")], cwd=ROOT, capture_output=True, timeout=120)
    d = q(st, "SELECT coin, day, w FROM f15_trend_paper ORDER BY day DESC, coin")
    out = ["[F15 추세 배분 · 페이퍼]"]
    if d is None or not len(d):
        out.append("데이터 없음"); return out
    last = d.day.max(); cur = d[d.day == last].set_index("coin").w
    prev = d[d.day < last]; prev = prev[prev.day == prev.day.max()].set_index("coin").w if len(prev) else cur * float("nan")
    tot = 0.0
    for c in ("KRW-BTC", "KRW-ETH"):
        w = float(cur.get(c, 0)); tot += 0.5 * w
        chg = "" if c not in prev or prev.isna().all() or float(prev[c]) == w else f" (어제 {float(prev[c]):.2f} → 변경)"
        out.append(f"{c[4:]} 비중 {w:.2f}{chg}")
    out.append(f"오늘 목표: BTC {50 * float(cur.get('KRW-BTC', 0)):.1f}% / ETH {50 * float(cur.get('KRW-ETH', 0)):.1f}% / 원화 {100 - 100 * tot:.1f}%")
    s = q(st, "SELECT day, avg(ret_net) AS s, avg(ret_coin) AS b FROM f15_trend_paper WHERE status='closed' GROUP BY day HAVING count(*)=2 ORDER BY day")
    if s is not None and len(s):
        out.append(f"포워드 {len(s)}일 누적: 전략 {((1 + s.s).prod() - 1):+.1%} / 그냥 보유 {((1 + s.b).prod() - 1):+.1%}")
    return out


def main():
    st = get_storage()
    lines = f15(st)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
