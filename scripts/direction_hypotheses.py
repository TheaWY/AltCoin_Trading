#!/usr/bin/env python3
"""30 hypotheses for the DIRECTION of coins that are about to move a lot.

Universe: coin-hours in the top 5% of 24h volatility at that hour (the
single feature carrying most of the 'will move 10%' model; causal, no model
needed), liquid (24h value >= $2M). Each hypothesis is a signal oriented so
that HIGH = expect UP.

Scored on train (first 55% of hours) and test (the rest, after 1 day):
  ic     per-hour rank correlation of the signal with the 24h return inside
         the universe, averaged per day; t over days (clustered)
  auc    among rows that did move 10% one way, signal vs 'went up'
  book   every day at 00 UTC inside the universe: long the top 3 by signal,
         short the bottom 3, hold 24h, 0.3% round trip each side; daily mean,
         t, share of positive days
Pass: same sign in train and test, test ic t >= 2, BH-FDR q < 0.10 over all
30, and a positive test long-short book. Survivors are combined (z-score sum,
weights fixed on train) and the combination is booked on test.

Writes data/reports/direction/latest.{json,md}.
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
warnings.filterwarnings("ignore")
from src.research import deep_search as ds  # noqa: E402
from src.research import deep_tests as dt  # noqa: E402

OUT = PROJECT_ROOT / "data" / "reports" / "direction"
COST = 0.003


def col(p, k):
    return p[k] if k in p else pd.Series(np.nan, index=p.index)


def hypotheses(p: pd.DataFrame) -> list[tuple[str, str, pd.Series]]:
    g = lambda k: col(p, k)  # noqa: E731
    H = [
        ("H01", "막 불붙기 시작: 직전 15분 상승이면 위로", g("ret_15m")),
        ("H02", "이미 24시간 과열이면 아래로 (24h 수익률 높을수록 하락)", -g("ret_1440m")),
        ("H03", "RSI(1h) 과매수면 아래로", -g("rsi_1h")),
        ("H04", "7일 고점 근처(돌파 중)면 위로 계속", g("dhi_7d")),
        ("H05", "24시간 저점에서 멀리 올라와 있으면 아래로", -g("dlo_24h")),
        ("H06", "1시간 시장가 매수 비중 높으면 위로", g("taker_60m")),
        ("H07", "시장가 매수 비중이 평소보다 튀면(z) 위로", g("taker_z_60m")),
        ("H08", "4시간 순매수(CVD) 양수면 위로", g("cvd_240m")),
        ("H09", "가격↑ + 미결제약정↑ (신규 롱 유입)이면 위로", np.sign(g("ret_240m")) * g("oi_4h")),
        ("H10", "가격↓ + 미결제약정↑ (숏 쌓임) = 숏스퀴즈로 위로", -g("ret_240m") * g("oi_4h").clip(lower=0)),
        ("H11", "펀딩 음수(숏 우세)면 스퀴즈로 위로", -g("funding")),
        ("H12", "펀딩이 평소보다 높으면(z) 아래로", -g("funding_z")),
        ("H13", "대중 롱/숏 비율 높으면(롱 과밀) 아래로", -g("ls_global")),
        ("H14", "고수(상위 트레이더)가 대중보다 롱이면 위로", g("smart_crowd")),
        ("H15", "선물 시장가 매수/매도 비율 높으면 위로", g("fut_taker_1h")),
        ("H16", "상장 초기 코인은 아래로 (언락·매도 물량)", g("age_days")),
        ("H17", "시총 큰 코인일수록 위로", g("mcap_log")),
        ("H18", "BTC 24시간 상승 중이면 위로", g("btc_ret_1440m")),
        ("H19", "시장 전체 오르는 코인 비율 높으면 위로", g("breadth_24h")),
        ("H20", "공포 구간(공포·탐욕 낮음)이면 위로 (역발상)", -g("fng")),
        ("H21", "매집: 거래대금 폭증인데 가격은 조용하면 위로", g("vsurge_60m") / (g("ret_60m").abs() + 0.005)),
        ("H22", "평균 체결 크기 커짐(고래)이면 위로", g("tsize_chg")),
        ("H23", "24시간 수익률 분포가 위로 치우침(skew+)이면 위로 계속", g("skew_24h")),
        ("H24", "하루에 +3% 5분봉이 많았으면 소진돼서 아래로", -g("bursts3_1440")),
        ("H25", "한국 거래대금 폭증이면 아래로", -g("kr_surge_60m")),
        ("H26", "김치프리미엄 높으면 아래로", -g("kimchi")),
        ("H27", "업비트·빗썸이 바이낸스보다 먼저 오르면 위로", g("kr_lead_15m")),
        ("H28", "아시아 시간(UTC 0~8시)엔 위로", ((g("hour") >= 0) & (g("hour") < 8)).astype(float)),
        ("H29", "30일 저점 근처에서 변동성 커지면 바닥 반등으로 위로", -g("dlo_30d")),
        ("H30", "레버리지(OI/시총) 높으면 청산 캐스케이드로 아래로", -g("oi_mcap")),
    ]
    return H


def per_hour_ic(u: pd.DataFrame, s: pd.Series, daily_only: bool = True) -> pd.Series:
    """Rank IC per decision hour inside the universe. daily_only: only 00 UTC
    hours, so consecutive values do not share the same 24h return window
    (honest t); False averages all hours per day (more data, overlapping)."""
    d = pd.DataFrame({"ts": u["ts"], "s": s, "r": u["ret_24h"]}).dropna()
    if daily_only:
        d = d[d["ts"] % 86400 == 0]
    d["rs"] = d.groupby("ts")["s"].rank()
    d["rr"] = d.groupby("ts")["r"].rank()
    ic = d.groupby("ts").apply(lambda g: g["rs"].corr(g["rr"]) if len(g) >= 6 and g["rs"].nunique() > 1 else np.nan)
    ic = ic.dropna()
    return ic.groupby(ic.index // 86400).mean()


def book(u: pd.DataFrame, s: pd.Series, k: int = 3, invvol: bool = False) -> pd.Series:
    """Daily 00 UTC long top-k / short bottom-k. invvol: each leg weighted by
    1/24h-volatility so the wildest coin does not dominate the day."""
    d = pd.DataFrame({"ts": u["ts"], "s": s, "r": u["ret_24h"], "v": u["rv_24h"]}).dropna()
    d = d[d["ts"] % 86400 == 0]
    out = {}
    for ts, g in d.groupby("ts"):
        if len(g) < 2 * k or g["s"].nunique() < 2:
            continue
        g = g.sort_values("s")
        lo, hi = g.iloc[:k], g.iloc[-k:]
        if invvol:
            wl, wh = 1 / lo["v"], 1 / hi["v"]
            out[ts] = ((hi["r"] * wh).sum() / wh.sum() - (lo["r"] * wl).sum() / wl.sum()) / 2 - COST
        else:
            out[ts] = (hi["r"].mean() - lo["r"].mean()) / 2 - COST
    return pd.Series(out, dtype=float)


def _short_only(u: pd.DataFrame, s: pd.Series, k: int) -> pd.Series:
    """Short the k lowest-scored (most 'down') coins alone, 24h, cost included."""
    d = pd.DataFrame({"ts": u["ts"], "s": s, "r": u["ret_24h"]}).dropna()
    d = d[d["ts"] % 86400 == 0]
    return d.groupby("ts").apply(lambda g: -g.nsmallest(k, "s")["r"].mean() - COST if len(g) >= k else np.nan).dropna()


def auc_dir(u: pd.DataFrame, s: pd.Series) -> float | None:
    d = pd.DataFrame({"s": s, "y": u["dir_24h"]}).dropna()
    if d["y"].sum() < 30 or (1 - d["y"]).sum() < 30 or d["s"].nunique() < 2:
        return None
    from scipy.stats import rankdata
    r = rankdata(d["s"])
    pos = d["y"].to_numpy() == 1
    return float((r[pos].sum() - pos.sum() * (pos.sum() + 1) / 2) / (pos.sum() * (~pos).sum()))


def stat(x: pd.Series) -> dict:
    x = x.dropna()
    if len(x) < 10:
        return {"n": int(len(x)), "mean": None, "t": None}
    return {"n": int(len(x)), "mean": float(x.mean()), "t": float(x.mean() / x.std(ddof=1) * np.sqrt(len(x))),
            "win": float((x > 0).mean())}


def main() -> int:
    p = pd.read_parquet(ds.PANEL)
    p = dt.add_trade_labels(p)
    tr, te, cut = dt.split(p)
    rk = p.groupby("ts")["rv_24h"].rank(pct=True)
    uni = (rk >= 0.95) & (p["dv24_log"] >= np.log1p(2e6)) & p["ret_24h"].notna()
    res = {"universe_rows": int(uni.sum()), "test_from": cut, "hypotheses": []}
    U = {"train": p[uni & tr], "test": p[uni & te]}
    base = {k: stat(v[v["ts"] % 86400 == 0].groupby("ts")["ret_24h"].mean() - COST) for k, v in U.items()}
    res["universe_long_only"] = base
    H = hypotheses(p)
    rows = []
    for hid, text, sig in H:
        r = {"id": hid, "text": text, "coverage": float(sig[uni].notna().mean())}
        nu = sig[uni].groupby(p.loc[uni, "ts"]).nunique()
        nu = nu[sig[uni].notna().groupby(p.loc[uni, "ts"]).sum() >= 2]
        market = len(nu) > 0 and nu.median() <= 1     # same value for every coin at an hour
        r["market_level"] = bool(market)
        for part, u in U.items():
            s = sig.loc[u.index]
            if market:
                # time series: does the signal tell whether these volatile coins as a group go up?
                d = pd.DataFrame({"ts": u["ts"], "s": s, "r": u["ret_24h"]}).dropna()
                d = d[d["ts"] % 86400 == 0].groupby("ts").agg(s=("s", "first"), r=("r", "mean"))
                ic = pd.Series(dtype=float)
                if len(d) > 20 and d["s"].nunique() > 1:
                    # rolling-free: sign of (signal above its train median) times the group return
                    med = sig[uni & tr].median()
                    ic = np.sign(d["s"] - med) * d["r"]
                r[f"ic_{part}"] = stat(ic)
                r[f"auc_{part}"] = auc_dir(u, s)
                r[f"book_{part}"] = stat(ic - COST if len(ic) else ic)
                continue
            ic = per_hour_ic(u, s)
            r[f"ic_{part}"] = stat(ic)
            r[f"ic_allhours_{part}"] = stat(per_hour_ic(u, s, daily_only=False))
            r[f"auc_{part}"] = auc_dir(u, s)
            r[f"book_{part}"] = stat(book(u, s))
        rows.append(r)
        print(hid, text, {k: (round(v["mean"], 4) if isinstance(v, dict) and v.get("mean") is not None else v)
                          for k, v in r.items() if k.startswith(("ic_", "book_", "auc_"))}, flush=True)
    # BH over test ic t (two-sided)
    tz = np.array([abs(r["ic_test"]["t"]) if r["ic_test"].get("t") is not None else 0 for r in rows])
    pv = 2 * (1 - norm.cdf(tz))
    order = np.argsort(pv)
    q = np.empty(len(pv))
    prev = 1.0
    for rank, i in enumerate(order[::-1]):
        k = len(pv) - rank
        prev = min(prev, pv[i] * len(pv) / k)
        q[i] = prev
    for r, pi, qi in zip(rows, pv, q):
        a, b = r["ic_train"].get("mean"), r["ic_test"].get("mean")
        r["q"] = float(qi)
        r["same_sign"] = bool(a is not None and b is not None and np.sign(a) == np.sign(b))
        r["passes"] = bool(r["same_sign"] and (r["ic_test"].get("t") or 0) * np.sign(b or 0) >= 2 and qi < 0.10
                           and (r["book_test"].get("mean") or 0) * np.sign(b or 0) > 0)
    res["hypotheses"] = rows
    # combine survivors (sign from train), z-scored within each hour
    surv = [r for r in rows if r["passes"]]
    res["survivors"] = [r["id"] for r in surv]
    if surv:
        sig = {h: s for h, _, s in H}
        z = 0
        for r in surv:
            s = sig[r["id"]] * np.sign(r["ic_train"]["mean"])
            zz = (s - s.groupby(p["ts"]).transform("mean")) / s.groupby(p["ts"]).transform("std")
            z = z + zz.fillna(0)
        res["combo"] = {part: {"ic": stat(per_hour_ic(u, z.loc[u.index])), "book": stat(book(u, z.loc[u.index])),
                               "book5": stat(book(u, z.loc[u.index], 5)),
                               "book5v": stat(book(u, z.loc[u.index], 5, invvol=True)),
                               "short5": stat(-(book(u, -z.loc[u.index], 5) + COST) * 0 + _short_only(u, z.loc[u.index], 5)),
                               "auc": auc_dir(u, z.loc[u.index])}
                        for part, u in U.items()}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "latest.json").write_text(json.dumps(res, ensure_ascii=False, default=float))
    L = ["# 방향 가설 30개 (변동성 상위 5% 코인 안에서)", "",
         f"- 대상: 매시간 24h 변동성 상위 5% + 거래대금 $2M 이상, {res['universe_rows']:,} 코인·시간",
         f"- 테스트 {pd.Timestamp(cut, unit='s'):%Y-%m-%d}~. IC = 매일 00시(UTC) 한 번씩만 (24h 수익률 겹침 없음). 북 = 매일 00시 롱 3 / 숏 3, 24h, 왕복 0.3%",
         "- 시장 전체 가설(★)은 코인 간 비교가 안 돼서: 신호가 학습 중앙값보다 유리하면 이 코인들 전부 롱, 아니면 숏한 하루 수익",
         f"- 그냥 이 코인들 다 롱: 학습 {base['train'].get('mean', 0) * 100:+.2f}%/일, 테스트 {base['test'].get('mean', 0) * 100:+.2f}%/일", "",
         "| # | 가설 | 학습 IC | 테스트 IC (t) | 방향 AUC 테스트 | 롱숏 북 테스트 (t) | 통과 |", "|---|---|---:|---:|---:|---:|---|"]
    f = lambda v, d=3: "—" if v is None else f"{v:+.{d}f}"  # noqa: E731
    for r in sorted(rows, key=lambda r: -abs(r["ic_test"].get("t") or 0)):
        L.append(f"| {r['id']}{'★' if r.get('market_level') else ''} | {r['text']} | {f(r['ic_train'].get('mean'))} | {f(r['ic_test'].get('mean'))} "
                 f"({f(r['ic_test'].get('t'), 1)}) | {f(r['auc_test'], 3) if r['auc_test'] else '—'} | "
                 f"{f((r['book_test'].get('mean') or 0) * 100, 2)}% ({f(r['book_test'].get('t'), 1)}) | {'✅' if r['passes'] else ''} |")
    if res.get("combo"):
        c = res["combo"]["test"]
        L += ["", f"**통과한 가설 합치기 ({', '.join(res['survivors'])})**: 테스트 IC {f(c['ic'].get('mean'))} (t {f(c['ic'].get('t'), 1)}), "
              f"롱숏 북 3+3 {f((c['book'].get('mean') or 0) * 100, 2)}%/일 (t {f(c['book'].get('t'), 1)}), 5+5 {f((c['book5'].get('mean') or 0) * 100, 2)}%/일 (t {f(c['book5'].get('t'), 1)}), 5+5 변동성 반비례 {f((c['book5v'].get('mean') or 0) * 100, 2)}%/일 (t {f(c['book5v'].get('t'), 1)}), 하락 예상 5개만 숏 {f((c['short5'].get('mean') or 0) * 100, 2)}%/일 (t {f(c['short5'].get('t'), 1)}), 방향 AUC {f(c['auc'])}"]
    (OUT / "latest.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
