"""B26_VOLTARGET (registered 2026-10-01, before running). Literature-backed version of "predict bad days":
forecast the SIZE of tomorrow's move (predictable: B25 HIVOL AUC 0.73; Bergsli et al. 2022 HAR-RV; Harvey et al. 2018
vol targeting) and the crash risk from crowding (Schmeling-Schrimpf-Todorov 2023 "Crypto Carry": high funding/basis
predicts crashes), then size positions, instead of guessing direction (B24/B25: AUC ~0.5).

Forecast   log RV of the liquid-alt index for day d (hourly returns) with HAR: RV_1d, RV_7d, RV_30d, negative
           semivariance 1d, + Deribit BTC DVOL (previous day close) and mean alt funding z-score. OLS, walk-forward
           quarterly refits from 2025-01-01 (training from 2024-05, target day before the block).
Sizing     w_d = clip(target_vol / forecast_vol, 0.25, 2), target_vol = training-median realised vol;
           crowding haircut: x0.5 when mean funding is in the top decile of the training window.
Books      ALTLONG (equal-weight liquid alts, long), BTC long, VSH (B20 K4), PFOLLOW (pump follow-through)
Baselines  unmanaged; and CONSTANT leverage equal to the managed book's average weight (Cederburg et al. 2020 test)
Pass (per book): managed Sharpe > both baselines in 2025 AND 2026, and max drawdown not worse than unmanaged.
Also reported: forecast quality (R2 of log RV, HAR vs RV_1d persistence).
Output data/reports/b26/voltarget.{json,md}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b7_lib as L  # noqa: E402

OUT = ROOT / "data/reports/b26"
D = 86400
TRAIN0 = int(pd.Timestamp("2024-05-01").timestamp()) // D
BLOCKS = [int(pd.Timestamp(x).timestamp()) // D for x in ("2025-01-01", "2025-04-01", "2025-07-01", "2025-10-01", "2026-01-01",
                                                          "2026-04-01", "2026-07-01", "2026-10-01")]
Y26 = BLOCKS[4]


def mdd(r):
    eq = (1 + pd.Series(r).fillna(0)).cumprod()
    return float((eq / eq.cummax() - 1).min())


def sharpe(r):
    r = pd.Series(r).dropna()
    return float(r.mean() / r.std() * np.sqrt(365)) if r.std() > 0 else 0.0


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ts, codes, X = L.data()
    bi = codes.index("BTCUSDT")
    U = (np.nan_to_num(X["dv24"]) >= 5e6) & (X["age"] >= 720); U[:, bi] = False
    idx = pd.Series(np.nan_to_num(np.nanmean(np.where(U, X["r1"], np.nan), 1)), index=ts)
    day = pd.Series(ts // D, index=ts)
    rv = (idx ** 2).groupby(day).sum()
    neg = (np.minimum(idx, 0) ** 2).groupby(day).sum()
    fund = pd.Series(np.nanmean(np.where(U, X["f8"], np.nan), 1), index=ts).groupby(day).mean()
    dv = pd.read_parquet(ROOT / "data/cache/deribit_dvol_btc_1d.parquet")
    dvol = pd.Series(dv.c.to_numpy(), index=(dv.ts // 1000 // D).to_numpy())
    F = pd.DataFrame(index=rv.index)
    lrv = np.log(rv + 1e-10)
    F["rv1"] = lrv.shift(1)
    F["rv7"] = np.log(rv.shift(1).rolling(7).mean() + 1e-10)
    F["rv30"] = np.log(rv.shift(1).rolling(30).mean() + 1e-10)
    F["neg1"] = np.log(neg.shift(1) + 1e-10)
    F["dvol"] = np.log(dvol.reindex(F.index).shift(1))
    fz = (fund - fund.rolling(90, min_periods=30).mean()) / fund.rolling(90, min_periods=30).std()
    F["fund_z"] = fz.shift(1)
    F["fund_lvl"] = fund.shift(1)
    y = lrv
    S = pd.read_csv(ROOT / "data/reports/b24/strategy_daily.csv", index_col=0)
    books = pd.DataFrame({"ALTLONG": np.expm1(idx.groupby(day).sum()), "BTC": S["BTC"], "VSH": S["VSH"], "PFOLLOW": S["PFOLLOW"]})
    days = F.index.to_numpy()
    fc, base = pd.Series(np.nan, index=F.index), pd.Series(np.nan, index=F.index)
    w = pd.Series(np.nan, index=F.index)
    for b0, b1 in zip(BLOCKS[:-1], BLOCKS[1:]):
        tr = (days >= TRAIN0) & (days < b0); te = (days >= b0) & (days < b1)
        Xtr = F[tr].dropna(); ytr = y.loc[Xtr.index]
        A = np.c_[np.ones(len(Xtr)), Xtr.to_numpy()]
        beta = np.linalg.lstsq(A, ytr.to_numpy(), rcond=None)[0]
        Xte = F[te].fillna(Xtr.mean())
        fc[te] = np.c_[np.ones(len(Xte)), Xte.to_numpy()] @ beta
        base[te] = F.rv1[te]
        tgt = np.median(np.sqrt(rv[tr].dropna()))
        hot = F.fund_lvl[te] > np.nanpercentile(F.fund_lvl[tr].dropna(), 90)
        w[te] = np.clip(tgt / np.sqrt(np.exp(fc[te])), 0.25, 2.0) * np.where(hot, 0.5, 1.0)
    oos = days >= BLOCKS[0]
    yo, fo, bo = y[oos], fc[oos], base[oos]
    ok = yo.notna() & fo.notna() & bo.notna() & np.isfinite(yo)
    r2 = lambda p: float(1 - ((yo[ok] - p[ok]) ** 2).sum() / ((yo[ok] - yo[ok].mean()) ** 2).sum())  # noqa: E731
    R = {"forecast": {"r2_har": r2(fo), "r2_persistence": r2(bo), "corr_har": float(np.corrcoef(yo[ok], fo[ok])[0, 1])}}
    wo = w[oos]
    R["books"] = {}
    for b in books.columns:
        r = books[b].reindex(F.index)[oos].fillna(0)
        # weight changes cost fee + slippage on the change (alts ~0.1%, BTC 0.07%); VSH/PFOLLOW already net of their own costs
        tc = wo.diff().abs().fillna(0) * (0.0007 if b == "BTC" else 0.0012)
        man = wo * r - tc
        res = {}
        for yr, sel in (("2025", r.index < Y26), ("2026", r.index >= Y26)):
            c = float(wo[sel].mean())
            res[yr] = {"unmanaged": {"sharpe": sharpe(r[sel]), "mdd": mdd(r[sel]), "bp": float(r[sel].mean() * 1e4)},
                       "constant": {"sharpe": sharpe(c * r[sel]), "mdd": mdd(c * r[sel]), "lev": c},
                       "managed": {"sharpe": sharpe(man[sel]), "mdd": mdd(man[sel]), "bp": float(man[sel].mean() * 1e4)}}
        res["pass"] = bool(all(res[y_]["managed"]["sharpe"] > max(res[y_]["unmanaged"]["sharpe"], res[y_]["constant"]["sharpe"])
                               and res[y_]["managed"]["mdd"] >= res[y_]["unmanaged"]["mdd"] for y_ in ("2025", "2026")))
        R["books"][b] = res
    R["weight_stats"] = {"mean": float(wo.mean()), "min": float(wo.min()), "max": float(wo.max()),
                         "share_haircut": float((F.fund_lvl[oos] > 0).mean())}
    json.dump(R, open(OUT / "voltarget.json", "w"), indent=1, default=float)
    Lm = ["# B26: vol-targeted sizing from a HAR-RV + DVOL + funding forecast (OOS 2025-01..2026-09)", "",
          f"Forecast of tomorrow's log realised vol: R2 HAR {R['forecast']['r2_har']:.3f} vs yesterday-only {R['forecast']['r2_persistence']:.3f} (corr {R['forecast']['corr_har']:.2f})", "",
          "| book | year | unmanaged Sharpe / maxDD | constant-lev Sharpe / maxDD | managed Sharpe / maxDD | pass |", "|---|---|---|---|---|---|"]
    for b, res in R["books"].items():
        for yr in ("2025", "2026"):
            u, c, m = res[yr]["unmanaged"], res[yr]["constant"], res[yr]["managed"]
            Lm.append(f"| {b} | {yr} | {u['sharpe']:.2f} / {u['mdd']:.1%} | {c['sharpe']:.2f} / {c['mdd']:.1%} | {m['sharpe']:.2f} / {m['mdd']:.1%} | {res['pass'] if yr == '2026' else ''} |")
    (OUT / "voltarget.md").write_text("\n".join(Lm) + "\n")
    print("\n".join(Lm))


if __name__ == "__main__":
    main()
