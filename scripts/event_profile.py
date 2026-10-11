"""What did coins look like before a big 24h move up vs down?

Events: every coin-day where the next 24h return is >= +10% (up) or <= -10% (dn),
taken at the hour with the most extreme forward 24h return that day (the hour
just before the move). Controls: same hour, |next 24h| < 3%.

For every feature we compare the cross-sectional rank (same hour) of
up events, down events and controls, overall and within volatility quintiles
(so that "it was already volatile" does not explain everything). The key
number is AUC(up vs dn): 0.5 = the feature cannot tell an up move from a
down move beforehand.

Also: lag profiles (72h..0h before), pump-then-dump sequences, event flags.
Out: data/reports/events/profile.md + profile_auc.csv
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
LITP = ROOT / "data" / "cache" / "lit_panel.parquet"
OUT = ROOT / "data" / "reports" / "events"
H_ = 3600
SKIP = {"symbol", "ts", "code", "univ", "terc", "hour", "weekday", "fund_h", "btc_fwd_24h", "age_censored"}
LABEL = ("mfe_", "mae_", "ret_1h", "ret_4h", "ret_24h", "up10_", "win10_", "dn10_", "ex_ret_", "d1_ex_", "abs_ex_")
MODE = sys.argv[1] if len(sys.argv) > 1 else "fixed"
TRADFI = {"AAPL", "AMD", "AMZN", "COIN", "COPPER", "CRCL", "CRWD", "EWY", "GOOGL", "HOOD", "INTC", "KORU", "META",
          "MSFT", "MSTR", "NATGAS", "NFLX", "NVDA", "PAXG", "PLTR", "QQQ", "SOXL", "SPY", "TQQQ", "TSLA", "XAG", "XAU",
          "XAUT", "XPD", "XPT"}
LAG_FEATS = ["ret_60m", "ret_1440m", "vsurge_60m", "taker_60m", "oi_1h", "funding", "rv_1h", "bk_dep2_rel",
             "bk_imb_2", "ls_global", "kr_share_1h", "cvd_60m"]


def auc(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a[np.isfinite(a)], b[np.isfinite(b)]
    if len(a) < 30 or len(b) < 30:
        return np.nan
    x = np.concatenate([a, b])
    r = pd.Series(x).rank().to_numpy()
    return float((r[: len(a)].sum() - len(a) * (len(a) + 1) / 2) / (len(a) * len(b)))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    p = pd.read_parquet(LITP)
    p = p[p["univ"] & p["ret_24h"].notna() & ~p["symbol"].str.split("/").str[0].isin(TRADFI)].reset_index(drop=True)
    feats = [c for c in p.columns if c not in SKIP and not c.startswith(LABEL) and p[c].dtype.kind in "fi"
             and p[c].notna().mean() > 0.02]
    g = p.groupby("ts", sort=False)
    R = pd.DataFrame({c: g[c].rank(pct=True).astype(np.float32) for c in feats})
    p["day"] = p["ts"] // 86400
    p["rvq"] = np.minimum((g["rv_24h"].rank(pct=True) * 5).fillna(0).astype(int), 4)
    hold = p["ts"].max() - 35 * 86400

    # events: extreme hour per coin-day
    # fixed decision hour (00 UTC) so the event hour is not chosen with hindsight
    # (picking the most extreme hour of the day selects pump tops as "before a crash")
    at = (p["ts"] % 86400 == 0) if MODE == "fixed" else pd.Series(True, index=p.index)
    if MODE == "fixed":
        up_i = p.index[at & (p["ret_24h"] >= 0.10)]
        dn_i = p.index[at & (p["ret_24h"] <= -0.10)]
    else:
        up_i = p[p["ret_24h"] >= 0.10].sort_values("ret_24h", ascending=False).drop_duplicates(["symbol", "day"]).index
        dn_i = p[p["ret_24h"] <= -0.10].sort_values("ret_24h").drop_duplicates(["symbol", "day"]).index
    ctl = p.index[at & (p["ret_24h"].abs() < 0.03)]
    typ = pd.Series("", index=p.index)
    typ[ctl], typ[up_i], typ[dn_i] = "ctl", "up", "dn"

    rows = []
    for f in feats:
        x = R[f].to_numpy()
        a_ud = auc(x[up_i], x[dn_i])
        # within volatility quintiles
        sub = []
        for q in range(5):
            u = up_i[p.loc[up_i, "rvq"].to_numpy() == q]
            d = dn_i[p.loc[dn_i, "rvq"].to_numpy() == q]
            sub.append(auc(x[u], x[d]))
        u_dev, d_dev = up_i[p.loc[up_i, "ts"] < hold], dn_i[p.loc[dn_i, "ts"] < hold]
        u_ho, d_ho = up_i[p.loc[up_i, "ts"] >= hold], dn_i[p.loc[dn_i, "ts"] >= hold]
        rows.append(dict(feature=f, up_rank=np.nanmean(x[up_i]), dn_rank=np.nanmean(x[dn_i]),
                         ctl_rank=np.nanmean(x[ctl]), auc_up_vs_ctl=auc(x[up_i], x[ctl[::20]]),
                         auc_dn_vs_ctl=auc(x[dn_i], x[ctl[::20]]), auc_up_vs_dn=a_ud,
                         auc_up_vs_dn_volmatched=np.nanmean(sub), auc_dev=auc(x[u_dev], x[d_dev]),
                         auc_hold=auc(x[u_ho], x[d_ho]), coverage=float(np.isfinite(x[up_i]).mean())))
    a = pd.DataFrame(rows)
    a["edge"] = (a["auc_up_vs_dn_volmatched"] - 0.5).abs()
    a = a.sort_values("edge", ascending=False)
    a.to_csv(OUT / f"profile_auc_{MODE}.csv", index=False)

    # lag profiles (raw values, median) for up / dn / control
    lagrows = []
    sym = p["symbol"]
    for f in [c for c in LAG_FEATS if c in p]:
        for k in (72, 48, 24, 12, 6, 3, 1, 0):
            s = p.groupby(sym, sort=False)[f].shift(k) if k else p[f]
            s = s.groupby(p["ts"]).rank(pct=True)
            lagrows.append(dict(feature=f, lag=k, up=s[up_i].mean(), dn=s[dn_i].mean(), ctl=s[ctl].mean()))
    lag = pd.DataFrame(lagrows)

    # pump then dump: after an up event, a dn event on the same coin within 7 days?
    ev = pd.DataFrame({"symbol": p.loc[up_i.union(dn_i), "symbol"], "day": p.loc[up_i.union(dn_i), "day"],
                       "typ": typ[up_i.union(dn_i)]})
    upd = ev[ev["typ"] == "up"]
    dnd = ev[ev["typ"] == "dn"]
    dn_days = dnd.groupby("symbol")["day"].apply(np.array).to_dict()
    up_days = upd.groupby("symbol")["day"].apply(np.array).to_dict()

    def within(sym_, day, table, lo, hi):
        d = table.get(sym_)
        return d is not None and np.any((d - day >= lo) & (d - day <= hi))

    seq = dict(
        up_then_dn_7d=float(np.mean([within(s, d, dn_days, 0, 7) for s, d in zip(upd["symbol"], upd["day"])])),
        dn_then_up_7d=float(np.mean([within(s, d, up_days, 0, 7) for s, d in zip(dnd["symbol"], dnd["day"])])),
        up_after_up_3d=float(np.mean([within(s, d, up_days, -3, -1) for s, d in zip(upd["symbol"], upd["day"])])),
        dn_after_up_3d=float(np.mean([within(s, d, up_days, -3, -1) for s, d in zip(dnd["symbol"], dnd["day"])])),
        n_up=len(upd), n_dn=len(dnd), base_up_day=len(upd) / p.drop_duplicates(["symbol", "day"]).shape[0],
        base_dn_day=len(dnd) / p.drop_duplicates(["symbol", "day"]).shape[0])

    # event outcome shape
    shape = {}
    for name, idx in (("up", up_i), ("dn", dn_i)):
        e = p.loc[idx]
        shape[name] = dict(n=len(e), coins=e["symbol"].nunique(), med_move=float(e["ret_24h"].median()),
                           med_dv24_musd=float(np.expm1(e["dv24_log"]).median() / 1e6),
                           med_age_days=float(e["age_days"].median()),
                           share_listing72=float((e.get("ev_listing72", 0) > 0).mean()),
                           share_warning72=float((e.get("ev_warning72", 0) > 0).mean()),
                           share_kr_listed=float((e.get("kr_listed", 0) > 0).mean()),
                           share_hour_utc=e["hour"].value_counts(normalize=True).sort_index().round(3).to_dict())

    L = ["# 큰 움직임 전에 코인은 어떤 모습이었나", "",
         f"- 급등 이벤트 {shape['up']['n']:,}건 ({shape['up']['coins']}개 코인), 급락 {shape['dn']['n']:,}건 "
         f"({shape['dn']['coins']}개 코인). 기준: 다음 24h +10% 이상 / -10% 이하, 코인·일당 1건.",
         f"- 비교군: 같은 시각에 다음 24h가 ±3% 안에 머문 코인. 판단 시각: " + ("매일 00시 UTC 고정 (사후 선택 없음)" if MODE == "fixed" else "그날 가장 극단적인 시각 (사후 선택, 참고용)") + ". 주식·원자재 선물 제외.",
         f"- 중앙값: 급등 이벤트 거래대금 {shape['up']['med_dv24_musd']:.1f}M$, 상장 {shape['up']['med_age_days']:.0f}일; "
         f"급락 {shape['dn']['med_dv24_musd']:.1f}M$, {shape['dn']['med_age_days']:.0f}일", "",
         "## 급등과 급락은 붙어서 온다", "",
         f"- 급등한 코인이 7일 안에 급락도 한 비율: {seq['up_then_dn_7d']:.0%}",
         f"- 급락한 코인이 7일 안에 급등도 한 비율: {seq['dn_then_up_7d']:.0%}",
         f"- 급등 직전 3일 안에 이미 급등이 있었던 비율: {seq['up_after_up_3d']:.0%}, "
         f"급락 직전 3일 안에 급등이 있었던 비율: {seq['dn_after_up_3d']:.0%}",
         f"- 평범한 코인·일의 급등 확률 {seq['base_up_day']:.1%}, 급락 {seq['base_dn_day']:.1%}", "",
         "## 움직이기 직전: 급등/급락 vs 조용한 코인 (같은 시각 순위 평균, 0.5 = 보통)", "",
         "| 변수 | 급등 전 | 급락 전 | 비교군 | 급등vs비교 AUC | 급락vs비교 AUC |", "|---|---|---|---|---|---|"]
    b = a.assign(sep=(a["auc_up_vs_ctl"] - 0.5).abs() + (a["auc_dn_vs_ctl"] - 0.5).abs()).sort_values(
        "sep", ascending=False)
    for _, r in b.head(15).iterrows():
        L.append(f"| {r['feature']} | {r['up_rank']:.2f} | {r['dn_rank']:.2f} | {r['ctl_rank']:.2f} | "
                 f"{r['auc_up_vs_ctl']:.2f} | {r['auc_dn_vs_ctl']:.2f} |")
    L += ["", "## 핵심: 급등 전과 급락 전을 구분하는 변수 (AUC, 0.5 = 구분 못함)", "",
          "변동성 비슷한 코인끼리 비교한 값 기준. dev = 홀드아웃 전, hold = 마지막 35일.", "",
          "| 변수 | 급등 전 순위 | 급락 전 순위 | AUC 전체 | AUC 변동성 맞춤 | dev | hold | 커버리지 |",
          "|---|---|---|---|---|---|---|---|"]
    for _, r in a.head(25).iterrows():
        L.append(f"| {r['feature']} | {r['up_rank']:.2f} | {r['dn_rank']:.2f} | {r['auc_up_vs_dn']:.3f} | "
                 f"{r['auc_up_vs_dn_volmatched']:.3f} | {r['auc_dev']:.3f} | {r['auc_hold']:.3f} | {r['coverage']:.0%} |")
    L += ["", "## 시간에 따른 모습 (같은 시각 순위 평균; 급등 / 급락 / 비교군)", "",
          "| 변수 | 72h 전 | 24h 전 | 6h 전 | 1h 전 | 직전 |", "|---|---|---|---|---|---|"]
    for f, grp in lag.groupby("feature", sort=False):
        cells = []
        for k in (72, 24, 6, 1, 0):
            r = grp[grp["lag"] == k].iloc[0]
            cells.append(f"{r['up']:.2f} / {r['dn']:.2f} / {r['ctl']:.2f}")
        L.append(f"| {f} | " + " | ".join(cells) + " |")
    L += ["", "## 이벤트 플래그", "",
          f"- 급등 중 72h 안 상장 공지: {shape['up']['share_listing72']:.1%}, 급락 중: {shape['dn']['share_listing72']:.1%}",
          f"- 급등 중 72h 안 유의 공지: {shape['up']['share_warning72']:.1%}, 급락 중: {shape['dn']['share_warning72']:.1%}",
          f"- 한국 상장 코인 비율: 급등 {shape['up']['share_kr_listed']:.0%}, 급락 {shape['dn']['share_kr_listed']:.0%}"]
    (OUT / f"profile_{MODE}.md").write_text("\n".join(L) + "\n")
    lag.to_csv(OUT / "profile_lags.csv", index=False)
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
