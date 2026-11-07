#!/usr/bin/env python3
"""Round 2: 30 hypotheses on coins that are ALREADY pumping -- does the pump
keep going (another +10%) or reverse (-10%) in the next 24h?

Event: the first hour of a UTC day in which a liquid coin (24h value >= $1M)
is up >= 5% over the last hour or >= 10% over the last 4 hours. One event
per coin per day. Entry = the next minute's open (as in the panel labels).

Each hypothesis is a signal oriented so HIGH = expect CONTINUATION (up).
Split at the train median of the signal into a 'continue' half and a
'reverse' half, then compare on train and test:
  diff     mean 24h return, continue half minus reverse half; t clustered
           by day (pumps on the same day share the market)
  up/dn    +10% and -10% hit rates in each half
  trade    long the continue half with +10% take-profit / -10% stop (if both
           are touched in the window the loss is assumed), short the reverse
           half the same way; 0.3% round trip AND the real funding paid or
           received over the 24h (from the funding archive)
Pass: diff same sign on train and test, test t >= 2, BH q < 0.10, and the
test trade of the favoured leg is positive after costs and funding.
Writes data/reports/direction/pumps.{json,md}.
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


def events(p: pd.DataFrame) -> pd.DataFrame:
    hot = ((p["ret_60m"] >= np.log(1.05)) | (p["ret_240m"] >= np.log(1.10))) & (p["dv24_log"] >= np.log1p(1e6))
    # market context at that hour: how many coins are pumping right now
    p["n_pumping"] = hot.groupby(p["ts"]).transform("sum")
    e = p[hot & p["mfe_24h"].notna()].copy()
    e["day"] = e["ts"] // 86400
    e = e.sort_values("ts").drop_duplicates(["symbol", "day"])
    return e


def funding_24h(e: pd.DataFrame) -> np.ndarray:
    out = np.full(len(e), np.nan)
    for sym, idx in e.groupby("symbol").groups.items():
        f = PROJECT_ROOT / "data" / "cache" / "funding" / (sym.replace("/", "") + ".parquet")
        if not f.exists():
            continue
        s = pd.read_parquet(f).sort_values("ts")
        cs = np.concatenate([[0.0], np.cumsum(s["f"].to_numpy())])
        tt = s["ts"].to_numpy()
        pos = [e.index.get_loc(i) for i in idx]
        t0 = e["ts"].to_numpy()[pos]
        a = np.searchsorted(tt, t0, side="right")
        b = np.searchsorted(tt, t0 + 86400, side="right")
        out[pos] = cs[b] - cs[a]
    return out


def hypotheses(e: pd.DataFrame) -> list[tuple[str, str, pd.Series]]:
    g = lambda k: e[k] if k in e else pd.Series(np.nan, index=e.index)  # noqa: E731
    return [
        ("P01", "거래대금이 크게 동반된 급등이면 계속 (1시간 거래대금 폭증)", g("vsurge_60m")),
        ("P02", "시장가 매수가 주도한 급등이면 계속 (1시간 매수 비중)", g("taker_60m")),
        ("P03", "순매수(CVD 1시간)가 큰 급등이면 계속", g("cvd_60m")),
        ("P04", "급등과 함께 미결제약정이 늘면(새 돈 유입) 계속", g("oi_1h")),
        ("P05", "4시간 미결제약정 증가가 크면 계속", g("oi_4h")),
        ("P06", "펀딩비가 이미 높으면(롱 과밀) 되돌림", -g("funding")),
        ("P07", "펀딩이 평소보다 낮으면(z, 숏이 버티는 중) 계속", -g("funding_z")),
        ("P08", "고수가 대중보다 롱이면 계속", g("smart_crowd")),
        ("P09", "대중 롱/숏 비율 높으면(개미 롱) 되돌림", -g("ls_global")),
        ("P10", "이미 4시간에 너무 많이 올랐으면 되돌림", -g("ret_240m")),
        ("P11", "1분봉 하나로 튄 급등(스파이크)이면 되돌림", -g("max1m_60")),
        ("P12", "1시간 고점 근처에서 마감(윗꼬리 없음)이면 계속", g("dhi_1h")),
        ("P13", "30일 고점 돌파 중이면 계속", g("dhi_30d")),
        ("P14", "30일 저점 근처에서 시작한 급등(바닥 반등)이면 계속", -g("dlo_30d")),
        ("P15", "7일 추세가 이미 위면 계속", g("ret_10080m")),
        ("P16", "BTC가 24시간 오르는 중이면 계속", g("btc_ret_1440m")),
        ("P17", "시장 전체 상승 코인 비율 높으면 계속", g("breadth_24h")),
        ("P18", "같은 시간에 급등 코인이 많으면(섹터·시장 파도) 계속", g("n_pumping")),
        ("P19", "시총 작은 코인 급등은 되돌림 (펌프앤덤프)", g("mcap_log")),
        ("P20", "상장 초기 코인 급등은 되돌림", g("age_days")),
        ("P21", "조용하던 코인이 처음 터진 급등이면 계속 (24h 거래대금 평소 이하 → 폭증)", -g("vsurge_1440m") + g("vsurge_60m") / 10),
        ("P22", "하루에 이미 여러 번 튀었으면 소진돼서 되돌림", -g("bursts3_1440")),
        ("P23", "평균 체결 크기 커짐(고래 매수)이면 계속", g("tsize_chg")),
        ("P24", "체결 수가 거래대금보다 더 폭증(개미 몰림)이면 되돌림", -(g("nsurge_60m") / g("vsurge_60m"))),
        ("P25", "한국 거래대금 폭증 동반이면 되돌림", -g("kr_surge_60m")),
        ("P26", "김치프리미엄 붙은 급등이면 되돌림", -g("kimchi")),
        ("P27", "한국이 먼저 오른 급등이면 계속", g("kr_lead_15m")),
        ("P28", "RSI(1h) 85 넘는 과열이면 되돌림", -(g("rsi_1h") >= 85).astype(float)),
        ("P29", "조용하다 갑자기 변동성 확대(1h/24h)면 계속", g("rv_ratio_1h_24h")),
        ("P30", "상장 공지 72시간 안 급등이면 계속", g("ev_listing72")),
    ]


def cluster_t(y: np.ndarray, x: np.ndarray, day: np.ndarray) -> tuple[float, float]:
    """Difference in means (x=1 minus x=0) and its day-clustered t."""
    ok = np.isfinite(y)
    y, x, day = y[ok], x[ok], day[ok]
    if x.sum() < 20 or (1 - x).sum() < 20:
        return np.nan, np.nan
    X = np.column_stack([np.ones_like(y), x])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    res = y - X @ beta
    XtX_inv = np.linalg.inv(X.T @ X)
    meat = np.zeros((2, 2))
    for d in np.unique(day):
        m = day == d
        s = X[m].T @ res[m]
        meat += np.outer(s, s)
    V = XtX_inv @ meat @ XtX_inv
    return float(beta[1]), float(beta[1] / np.sqrt(V[1, 1]))


def trade(e: pd.DataFrame, side: int) -> np.ndarray:
    mfe, mae, ret, f24 = e["mfe_24h"].to_numpy(), e["mae_24h"].to_numpy(), e["ret_24h"].to_numpy(), e["f24"].fillna(0).to_numpy()
    if side > 0:
        r = np.where(mae <= -0.10, -0.10, np.where(mfe >= 0.10, 0.10, ret)) - f24     # long pays positive funding
    else:
        r = np.where(mfe >= 0.10, -0.10, np.where(mae <= -0.10, 0.10, -ret)) + f24    # short receives it
    return r - COST


def main() -> int:
    p = pd.read_parquet(ds.PANEL)
    p = dt.add_trade_labels(p)
    tr_mask, te_mask, cut = dt.split(p)
    e = events(p)
    e["f24"] = funding_24h(e)
    e["part"] = np.where(e["ts"] < cut - 86400, "train", np.where(e["ts"] >= cut, "test", "gap"))
    e = e[e["part"] != "gap"]
    base = {}
    for part, g in e.groupby("part"):
        base[part] = {"events": int(len(g)), "up10": float(g["up10_24h"].mean()), "dn10": float(g["dn10_24h"].mean()),
                      "ret24": float(g["ret_24h"].mean()), "long_trade": float(np.nanmean(trade(g, 1))),
                      "short_trade": float(np.nanmean(trade(g, -1))), "f24": float(g["f24"].mean())}
    rows = []
    for hid, text, sig in hypotheses(e):
        med = sig[e["part"] == "train"].median()
        r = {"id": hid, "text": text, "coverage": float(sig.notna().mean())}
        for part in ("train", "test"):
            m = (e["part"] == part) & sig.notna()
            g = e[m]
            if len(g) < 60 or not np.isfinite(med):
                continue
            x = (sig[m] > med).astype(float).to_numpy()
            if x.mean() in (0.0, 1.0):
                x = (sig[m] >= med).astype(float).to_numpy()
            diff, t = cluster_t(g["ret_24h"].to_numpy(), x, g["day"].to_numpy())
            hi, lo = g[x == 1], g[x == 0]
            r[part] = {"n": int(len(g)), "diff": diff, "t": t,
                       "up_hi": float(hi["up10_24h"].mean()), "up_lo": float(lo["up10_24h"].mean()),
                       "dn_hi": float(hi["dn10_24h"].mean()), "dn_lo": float(lo["dn10_24h"].mean()),
                       "long_hi": float(np.nanmean(trade(hi, 1))), "short_lo": float(np.nanmean(trade(lo, -1))),
                       "short_hi": float(np.nanmean(trade(hi, -1))), "long_lo": float(np.nanmean(trade(lo, 1)))}
        rows.append(r)
        te_ = r.get("test", {})
        print(hid, text, {k: round(v, 4) if isinstance(v, float) else v for k, v in te_.items()}, flush=True)
    tz = np.array([abs(r.get("test", {}).get("t") or 0) for r in rows])
    pv = 2 * (1 - norm.cdf(tz))
    order = np.argsort(pv)
    q = np.empty(len(pv))
    prev = 1.0
    for rank, i in enumerate(order[::-1]):
        k = len(pv) - rank
        prev = min(prev, pv[i] * len(pv) / k)
        q[i] = prev
    for r, qi in zip(rows, q):
        a, b = r.get("train", {}).get("diff"), r.get("test", {}).get("diff")
        r["q"] = float(qi)
        ok = a is not None and b is not None and np.isfinite(a) and np.isfinite(b) and np.sign(a) == np.sign(b)
        te_ = r.get("test", {})
        # favoured leg: if continuation half beats reversal half, long the 'hi' half; else short the 'hi' half
        fav = (te_.get("long_hi") if (b or 0) > 0 else te_.get("short_hi")) if te_ else None
        r["favoured_trade_test"] = fav
        r["passes"] = bool(ok and abs(te_.get("t") or 0) >= 2 and qi < 0.10 and (fav or 0) > 0)
    res = {"base": base, "test_from": cut, "hypotheses": rows, "survivors": [r["id"] for r in rows if r["passes"]]}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "pumps.json").write_text(json.dumps(res, ensure_ascii=False, default=float))
    pc = lambda v, d=1: "—" if v is None or not np.isfinite(v) else f"{v * 100:+.{d}f}%"  # noqa: E731
    f1 = lambda v: "—" if v is None or not np.isfinite(v) else f"{v:+.1f}"  # noqa: E731
    b_tr, b_te = base.get("train", {}), base.get("test", {})
    L = ["# 급등 중인 코인: 계속 가나, 되돌리나 (가설 30개)", "",
         f"- 사건: 1시간 +5% 또는 4시간 +10% 급등, 코인·일 첫 시점. 학습 {b_tr.get('events')}건 / 테스트 {b_te.get('events')}건 "
         f"(테스트 {pd.Timestamp(cut, unit='s'):%Y-%m-%d}~)",
         f"- 기준 (테스트): 24h 안에 +10% 더 {pc(b_te.get('up10'), 0)}, -10% {pc(b_te.get('dn10'), 0)}, 24h 평균 {pc(b_te.get('ret24'))}; "
         f"그냥 롱(±10% 익절/손절, 비용·펀딩 포함) {pc(b_te.get('long_trade'), 2)}, 그냥 숏 {pc(b_te.get('short_trade'), 2)}, 24h 펀딩 평균 {pc(b_te.get('f24'), 2)}",
         "- '계속' 쪽 = 신호가 학습 중앙값보다 높은 절반. 차이 = 계속 쪽 24h 수익 - 되돌림 쪽 (t는 날짜 클러스터)", "",
         "| # | 가설 | 학습 차이 (t) | 테스트 차이 (t) | 테스트 +10% 계속/되돌림 쪽 | 테스트 -10% 계속/되돌림 쪽 | 유리한 쪽 거래 (테스트) | 통과 |",
         "|---|---|---:|---:|---:|---:|---:|---|"]
    for r in sorted(rows, key=lambda r: -abs(r.get("test", {}).get("t") or 0)):
        a, b = r.get("train", {}), r.get("test", {})
        L.append(f"| {r['id']} | {r['text']} | {pc(a.get('diff'))} ({f1(a.get('t'))}) | {pc(b.get('diff'))} ({f1(b.get('t'))}) | "
                 f"{pc(b.get('up_hi'), 0)} / {pc(b.get('up_lo'), 0)} | {pc(b.get('dn_hi'), 0)} / {pc(b.get('dn_lo'), 0)} | "
                 f"{pc(r.get('favoured_trade_test'), 2)} | {'✅' if r['passes'] else ''} |")
    (OUT / "pumps.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
