"""Does news come before big moves, and does it tell the direction?

Uses data/cache/gdelt/<BASE>.json (scripts/gdelt_news.py) and the lit panel.
Decision time: 00 UTC each day. 'Before' = articles on the previous UTC day
(fully known at 00 UTC). 'Same day' = articles during the next 24h (not known
in advance; shows whether news comes with the move).
Out: data/reports/events/news.md
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
GD = ROOT / "data" / "cache" / "gdelt"
OUT = ROOT / "data" / "reports" / "events"
sys.path.insert(0, str(ROOT / "scripts"))
from event_profile import TRADFI, auc  # noqa: E402


def load_news() -> pd.DataFrame:
    rows = []
    for f in GD.glob("*.json"):
        if f.name.startswith("_"):
            continue
        d = json.loads(f.read_text())
        vol = {}
        tone = {}
        try:
            for x in d["vol"]["timeline"][0]["data"]:
                vol[x["date"][:8]] = x["value"]
        except (TypeError, KeyError, IndexError):
            continue
        try:
            for x in d["tone"]["timeline"][0]["data"]:
                tone[x["date"][:8]] = x["value"]
        except (TypeError, KeyError, IndexError):
            pass
        for k, v in vol.items():
            rows.append((d["base"], k, v, tone.get(k, np.nan)))
    n = pd.DataFrame(rows, columns=["base", "date", "n", "tone"])
    n["day"] = (pd.to_datetime(n["date"]) - pd.Timestamp("1970-01-01")) // pd.Timedelta("1D")
    n = n.sort_values(["base", "day"])
    g = n.groupby("base")["n"]
    mu = g.transform(lambda s: s.shift(1).rolling(30, min_periods=10).mean())
    sd = g.transform(lambda s: s.shift(1).rolling(30, min_periods=10).std())
    n["z"] = (n["n"] - mu) / sd.replace(0, np.nan)
    n.loc[(sd == 0) & (n["n"] > mu), "z"] = 5.0
    n.loc[(sd == 0) & (n["n"] == mu), "z"] = 0.0
    n["tone"] = n["tone"].where(n["n"] > 0)
    return n


def main() -> int:
    news = load_news()
    covered = news.groupby("base")["n"].sum()
    active = covered[covered >= 20].index        # coins GDELT actually writes about
    p = pd.read_parquet(ROOT / "data" / "cache" / "lit_panel.parquet",
                        columns=["symbol", "ts", "univ", "ret_24h", "rv_24h"])
    p["base"] = p["symbol"].str.split("/").str[0]
    p = p[p["univ"] & p["ret_24h"].notna() & (p["ts"] % 86400 == 0) & ~p["base"].isin(TRADFI)
          & p["base"].isin(active)].copy()
    p["day"] = p["ts"] // 86400
    prev = news.rename(columns={"n": "n_prev", "z": "z_prev", "tone": "tone_prev"})[["base", "day", "n_prev",
                                                                                     "z_prev", "tone_prev"]]
    prev["day"] += 1
    same = news.rename(columns={"n": "n_same", "z": "z_same", "tone": "tone_same"})[["base", "day", "n_same",
                                                                                     "z_same", "tone_same"]]
    p = p.merge(prev, on=["base", "day"], how="left").merge(same, on=["base", "day"], how="left")
    p["typ"] = np.where(p["ret_24h"] >= 0.10, "up", np.where(p["ret_24h"] <= -0.10, "dn",
                                                             np.where(p["ret_24h"].abs() < 0.03, "ctl", "mid")))
    L = ["# 뉴스(GDELT 기사 수·논조)와 큰 움직임", "",
         f"- GDELT에 기사가 6개월간 20건 이상 있는 코인 {len(active)}개 / 이름 확인된 코인 {news['base'].nunique()}개",
         f"- 코인·일 {len(p):,}개 (판단 00시 UTC). 급등 {int((p.typ == 'up').sum()):,}, 급락 {int((p.typ == 'dn').sum()):,}", "",
         "## 뉴스가 튄 비율 (기사 수가 평소 30일 대비 z>2)", "",
         "| | 전날 (미리 알 수 있음) | 당일 (움직이는 동안) |", "|---|---|---|"]
    for t, nm in (("up", "급등"), ("dn", "급락"), ("ctl", "조용"), ("mid", "중간")):
        s = p[p["typ"] == t]
        L.append(f"| {nm} | {(s['z_prev'] > 2).mean():.1%} | {(s['z_same'] > 2).mean():.1%} |")
    up, dn = p[p["typ"] == "up"], p[p["typ"] == "dn"]
    L += ["", "## 방향 구분력 (급등 vs 급락 AUC, 0.5 = 구분 못함)", "",
          f"- 전날 기사 z: {auc(up['z_prev'].to_numpy(float), dn['z_prev'].to_numpy(float)):.3f}",
          f"- 전날 논조: {auc(up['tone_prev'].to_numpy(float), dn['tone_prev'].to_numpy(float)):.3f} "
          f"(급등 평균 {up['tone_prev'].mean():.2f}, 급락 {dn['tone_prev'].mean():.2f}, 조용 {p.loc[p.typ == 'ctl', 'tone_prev'].mean():.2f})",
          f"- 당일 논조 (사후): {auc(up['tone_same'].to_numpy(float), dn['tone_same'].to_numpy(float)):.3f} "
          f"(급등 {up['tone_same'].mean():.2f}, 급락 {dn['tone_same'].mean():.2f})", "",
          "## 전날 뉴스가 튄 코인을 다음날 샀다면 (24h, 비용 전)", "",
          "| 조건 | n | 평균 | 중앙값 | +10% 확률 | -10% 확률 |", "|---|---|---|---|---|---|"]
    conds = {
        "전날 기사 급증 (z>2)": p["z_prev"] > 2,
        "전날 급증 + 좋은 논조 (tone>1)": (p["z_prev"] > 2) & (p["tone_prev"] > 1),
        "전날 급증 + 나쁜 논조 (tone<-1)": (p["z_prev"] > 2) & (p["tone_prev"] < -1),
        "전날 기사 없음": p["n_prev"] == 0,
        "전체": p["ret_24h"].notna(),
    }
    for k, m in conds.items():
        s = p.loc[m, "ret_24h"]
        L.append(f"| {k} | {len(s):,} | {s.mean()*100:.2f}% | {s.median()*100:.2f}% | "
                 f"{(s >= 0.10).mean():.1%} | {(s <= -0.10).mean():.1%} |")
    (OUT / "news.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
