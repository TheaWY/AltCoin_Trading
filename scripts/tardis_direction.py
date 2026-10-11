"""Direction test on the Tardis trial window (2026-05-19 .. 05-28, 10 days).

Cross-exchange (Binance, Bybit, OKX, Bitget) liquidations, open interest,
funding and premium, plus large-trade flow and order book if those files
exist. Question: among coins, and among the most volatile coins (top 5% rv_24h
each hour), does any of it tell the next 24h direction?
Out: data/reports/lit/tardis_direction.md
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TD = ROOT / "data" / "tardis"
OUT = ROOT / "data" / "reports" / "lit"
H_ = 3600
sys.path.insert(0, str(ROOT / "scripts"))
from event_profile import TRADFI, auc  # noqa: E402
from lit_retest import nw_t, ts_corr  # noqa: E402

EXS = ["binance-futures", "bybit", "okex-swap", "bitget-futures"]


def deriv_hourly() -> pd.DataFrame:
    parts = []
    for ex in EXS:
        for f in sorted((TD / "deriv" / ex).glob("*.parquet")):
            d = pd.read_parquet(f, columns=["base", "minute", "oi_usd", "funding_rate", "last_price", "index_price"])
            d["ts"] = (d["minute"] // H_ + 1) * H_
            a = d.sort_values("minute").groupby(["base", "ts"]).agg(
                oi=("oi_usd", "last"), fund=("funding_rate", "last"), lp=("last_price", "last"),
                ip=("index_price", "last")).reset_index()
            a["ex"] = ex
            parts.append(a)
    d = pd.concat(parts, ignore_index=True)
    d["prem"] = d["lp"] / d["ip"] - 1
    g = d.groupby(["base", "ts"])
    out = g.agg(oi=("oi", "sum"), fund=("fund", "mean"), prem=("prem", "median"), n_ex=("ex", "nunique")).reset_index()
    out = out.sort_values(["base", "ts"])
    s = out.groupby("base")["oi"]
    for k in (1, 4, 24):
        out[f"x_oi_{k}h"] = np.log(out["oi"] / s.shift(k))
    out["x_fund_spread"] = g["fund"].agg(lambda x: x.max() - x.min()).reset_index(drop=True).reindex(out.index).values \
        if False else np.nan
    return out.drop(columns=["x_fund_spread"])


def liq_hourly() -> pd.DataFrame:
    parts = []
    for ex in EXS:
        for f in sorted((TD / "liq" / ex).glob("*.parquet")):
            q = pd.read_parquet(f, columns=["base", "timestamp", "side", "usd"])
            q["ts"] = (q["timestamp"] // 1_000_000 // H_ + 1) * H_
            q["ll"] = np.where(q["side"] == "sell", q["usd"], 0.0)   # long liquidated
            q["sl"] = np.where(q["side"] == "buy", q["usd"], 0.0)    # short liquidated
            a = q.groupby(["base", "ts"])[["ll", "sl"]].sum().reset_index()
            a["ex"] = ex
            parts.append(a)
    q = pd.concat(parts, ignore_index=True)
    out = q.groupby(["base", "ts"]).agg(ll=("ll", "sum"), sl=("sl", "sum"),
                                        n_ex_liq=("ex", "nunique")).reset_index()
    return out


def trades_hourly() -> pd.DataFrame:
    parts = []
    for ex in EXS:
        for f in sorted((TD / "trades" / ex).glob("*.parquet")):
            t = pd.read_parquet(f)
            parts.append(t.assign(ex=ex))
    if not parts:
        return pd.DataFrame()
    t = pd.concat(parts, ignore_index=True)
    t["ts"] = (t["minute"] // H_ + 1) * H_
    num = [c for c in t.columns if c.startswith(("big", "buy", "sell", "usd"))]
    return t.groupby(["base", "ts"])[num].sum().reset_index() if "base" in t else pd.DataFrame()


def main() -> int:
    p = pd.read_parquet(ROOT / "data" / "cache" / "lit_panel.parquet",
                        columns=["symbol", "ts", "univ", "rv_24h", "dv24_log", "ret_24h", "ex_ret_24h",
                                 "up10_24h", "dn10_24h", "ret_1440m", "ret_60m", "taker_60m"])
    p["base"] = p["symbol"].str.split("/").str[0]
    p = p[p["univ"] & ~p["base"].isin(TRADFI)]
    d = deriv_hourly()
    lq = liq_hourly()
    t0, t1 = int(d["ts"].min()) + 24 * H_, int(d["ts"].max())
    p = p[(p["ts"] >= t0) & (p["ts"] <= t1)].copy()
    p = p.merge(d, on=["base", "ts"], how="left").merge(lq, on=["base", "ts"], how="left")
    p[["ll", "sl", "n_ex_liq"]] = p[["ll", "sl", "n_ex_liq"]].fillna(0)
    p = p.sort_values(["base", "ts"])
    dv1h = np.expm1(p["dv24_log"]) / 24
    for k in (1, 4, 24):
        ll = p.groupby("base")["ll"].transform(lambda s: s.rolling(k, min_periods=1).sum())
        sl = p.groupby("base")["sl"].transform(lambda s: s.rolling(k, min_periods=1).sum())
        p[f"liq_long_{k}h"] = ll / (dv1h * k)
        p[f"liq_short_{k}h"] = sl / (dv1h * k)
        p[f"liq_imb_{k}h"] = (sl - ll) / (sl + ll).replace(0, np.nan)
    p["liq_cluster_1h"] = p["n_ex_liq"]
    p["oi_x_ret_4h"] = p["x_oi_4h"] * np.sign(p["ret_1440m"])
    tr = trades_hourly()
    feats = ["liq_long_1h", "liq_short_1h", "liq_imb_1h", "liq_long_4h", "liq_short_4h", "liq_imb_4h", "liq_imb_24h",
             "liq_cluster_1h", "x_oi_1h", "x_oi_4h", "x_oi_24h", "oi_x_ret_4h", "fund", "prem"]
    if len(tr):
        p = p.merge(tr, on=["base", "ts"], how="left")
        for c in tr.columns:
            if c not in ("base", "ts"):
                p[c] = p[c] / dv1h
                feats.append(c)
    g = p.groupby("ts")
    p["hv"] = g["rv_24h"].rank(pct=True) >= 0.95
    mid = t0 + (t1 - t0) // 2
    rows = []
    for f in feats:
        if f not in p or p[f].notna().mean() < 0.05:
            continue
        for uni in ("all", "hv"):
            s = p if uni == "all" else p[p["hv"]]
            uts, code = np.unique(s["ts"].to_numpy(), return_inverse=True)
            x = s.groupby("ts")[f].rank(pct=True).to_numpy(float)
            y = s.groupby("ts")["ex_ret_24h"].rank(pct=True).to_numpy(float)
            ic = ts_corr(code, len(uts), x, y, minn=10 if uni == "hv" else 20)
            first = uts < mid
            m, t = nw_t(ic, 24)
            m1, _ = nw_t(ic[first], 24)
            m2, _ = nw_t(ic[~first], 24)
            up = s["up10_24h"].to_numpy() == 1
            dn = (s["dn10_24h"].to_numpy() == 1) & ~up
            xr = s[f].to_numpy(float)
            rows.append(dict(feature=f, uni=uni, ic=m, t=t, ic_first=m1, ic_second=m2,
                             auc_up_dn=auc(xr[up], xr[dn]), n_up=int(up.sum()), n_dn=int(dn.sum())))
    r = pd.DataFrame(rows)
    r["stable"] = np.sign(r["ic_first"]) == np.sign(r["ic_second"])
    days = (t1 - t0) / 86400
    L = [f"# Tardis 체험판 방향 테스트 ({pd.to_datetime(t0, unit='s').date()} ~ {pd.to_datetime(t1, unit='s').date()}, "
         f"{days:.0f}일)", "",
         f"거래소: 바이낸스·바이빗·OKX·비트겟 청산, OI, 펀딩, 프리미엄" +
         (" + 대량 체결" if len(tr) else " (대량 체결·호가는 수집 실패, 재수집 중)") +
         ". 타깃: 다음 24h 초과수익 순위. hv = 매시간 변동성 상위 5% 코인.",
         "", "주의: 24h 수익은 서로 겹쳐서 독립 표본이 사실상 9일 정도. t>3이라도 우연일 수 있음.", "",
         "| 변수 | 대상 | IC | t | 전반 IC | 후반 IC | 같은 부호 | AUC 급등vs급락 | 급등/급락 n |",
         "|---|---|---|---|---|---|---|---|---|"]
    for _, x in r.sort_values("t", key=abs, ascending=False).iterrows():
        L.append(f"| {x['feature']} | {x['uni']} | {x['ic']:.3f} | {x['t']:.1f} | {x['ic_first']:.3f} | "
                 f"{x['ic_second']:.3f} | {'O' if x['stable'] else 'X'} | {x['auc_up_dn']:.2f} | {x['n_up']}/{x['n_dn']} |")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "tardis_direction.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
