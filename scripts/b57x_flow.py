"""B57x (prereg v9): Binance perp flow / cross-venue features at 00:00 UTC -> Upbit next-day relative return.
b2 hourly rows: ts = bar END (close known at ts). Upbit h1 rows: ts = bar OPEN.
Trade: Upbit open of the 01:00 UTC bar -> open of the 01:00 UTC bar next day (1h safety lag)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import b51_prereg_v2 as B  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
H = 3600
DEV = ("2024-03-15", "2025-10-01")
SEALED = ("2025-10-01", "2026-09-25")
WF_START = "2024-09-01"
X = ["X1_taker", "X2_fund", "X3_gap24", "X4_gap1", "X5_prem", "X6_dprem", "X7_vsurge", "X8_share"]
GBM = dict(n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=50, subsample=0.8,
           subsample_freq=1, colsample_bytree=0.8, verbose=-1, random_state=7)


def bn_panel():
    cols = ["ts", "code", "c", "taker_24h", "fund24", "ret_24h", "ret_1h", "vsurge", "dv24"]
    d = pd.concat([pd.read_parquet(ROOT / f"data/cache/{f}.parquet", columns=cols)
                   for f in ("b2_hourly_2024", "b2_hourly")]).drop_duplicates(["ts", "code"], keep="last")
    d = d[d.ts % 86400 == 0].copy()                                   # rows closing exactly at 00:00 UTC
    base = d.code.str.replace("USDT$", "", regex=True)
    mult = np.where(base.str.match(r"^1000000\D"), 1e6, np.where(base.str.match(r"^1000\D"), 1e3, 1.0))
    base = base.str.replace(r"^1000000(?=\D)", "", regex=True).str.replace(r"^1000(?=\D)", "", regex=True)
    d["coin"] = "KRW-" + base; d["mult"] = mult
    return d.drop(columns="code")


def upbit_at(coins, days):
    """per coin: close at T, close at T-24h, close at T-1h, open at T+1h, open at T+25h, 24h KRW value."""
    T = (days.astype("int64") // 10**9).to_numpy()
    out = []
    for m in coins:
        f = ROOT / f"data/upbit_db/h1/{m}.parquet"
        if not f.exists():
            continue
        u = pd.read_parquet(f).drop_duplicates("ts").set_index("ts").sort_index()
        g = lambda col, t: u[col].reindex(t).to_numpy()  # noqa: E731
        v24 = u.v.rolling(24, min_periods=12).sum()
        out.append(pd.DataFrame({"day": days, "coin": m, "u_c0": g("c", T - H), "u_c24": g("c", T - 25 * H),
                                 "u_c1": g("c", T - 2 * H), "u_o1": g("o", T + H), "u_o25": g("o", T + 25 * H),
                                 "u_v24": v24.reindex(T - H).to_numpy()}))
    return pd.concat(out, ignore_index=True)


def dataset():
    bn = bn_panel()
    O, C, V = B.panel()
    days = pd.DatetimeIndex(sorted(pd.to_datetime(bn.ts.unique(), unit="s")))
    days = days[(days >= DEV[0]) & (days < SEALED[1])]
    # universe from Upbit DAILY data of the previous complete day (T-1)
    medv = V.rolling(30, min_periods=20).median().shift(1); age = C.notna().cumsum().shift(1)
    dkey = days.tz_localize(None).normalize()
    coins = sorted(set(bn.coin) & set(C.columns))
    u = upbit_at(coins, days)
    usdt = upbit_at(["KRW-USDT"], days).set_index("day")
    bn["day"] = pd.to_datetime(bn.ts, unit="s")
    D = u.merge(bn, on=["day", "coin"], how="inner")
    D["fx0"] = D.day.map(usdt.u_c0); D["fx24"] = D.day.map(usdt.u_c24)
    D["medv"] = [medv.at[d.normalize(), c] if d.normalize() in medv.index else np.nan for d, c in zip(D.day, D.coin)]
    D["age"] = [age.at[d.normalize(), c] if d.normalize() in age.index else np.nan for d, c in zip(D.day, D.coin)]
    D = D[(D.age >= 90) & D.medv.notna() & D.u_c0.notna() & D.u_o1.notna()]
    D["rk"] = D.groupby("day").medv.rank(ascending=False, method="first")
    D = D[D.rk <= 40].copy()
    # features (all known at 00:00 UTC)
    up24 = D.u_c0 / D.u_c24 - 1; up1 = D.u_c0 / D.u_c1 - 1
    prem = D.u_c0 / (D.c / D.mult * D.fx0) - 1
    c24 = D.c / (1 + D.ret_24h)
    prem24 = D.u_c24 / (c24 / D.mult * D.fx24) - 1
    D["X1_taker"] = D.taker_24h; D["X2_fund"] = D.fund24
    D["X3_gap24"] = D.ret_24h - up24; D["X4_gap1"] = D.ret_1h - up1
    D["X5_prem"] = prem - prem.groupby(D.day).transform("median")
    D["X6_dprem"] = prem - prem24
    D["X7_vsurge"] = np.log(D.vsurge.clip(lower=1e-6))
    sh = np.log((D.dv24 * D.fx0).clip(lower=1) / D.u_v24.clip(lower=1))
    D["X8_share"] = sh - sh.groupby(D.day).transform("median")
    D["y_raw"] = D.u_o25 / D.u_o1 - 1
    D["y"] = D.y_raw - D.groupby("day").y_raw.transform("mean")
    D = D[D.y_raw.notna()]
    # BTC F15 gate (daily trend weight at day T-1 close) and costs
    tw = B.trend_w(C["KRW-BTC"].ffill()).shift(1)
    D["gate"] = D.day.dt.normalize().map(tw).fillna(0.0)
    D["cost"] = B.FEE + B.slip(D.medv.fillna(0).to_numpy())
    return D.reset_index(drop=True)


def daily_ic(D, col, ycol="y"):
    return D.groupby("day").apply(lambda g: g[col].rank().corr(g[ycol].rank()) if g[col].notna().sum() > 8 else np.nan).dropna()


def boot_mean_p(x, two_sided=True, draws=5000, block=5, seed=7):
    x = np.asarray(x, float); rng = np.random.default_rng(seed); obs = x.mean(); xc = x - obs
    ds = np.array([xc[B.stat_boot(len(x), block, rng)].mean() for _ in range(draws)])
    return float(np.mean(np.abs(ds) >= abs(obs))) if two_sided else float(np.mean(ds >= obs))


def bhy(p, q=0.10):
    p = np.asarray(p); m = len(p); c = np.sum(1 / np.arange(1, m + 1)); o = np.argsort(p)
    ok = p[o] <= q * np.arange(1, m + 1) / (m * c); k = np.max(np.where(ok)[0]) + 1 if ok.any() else 0
    out = np.zeros(m, bool); out[o[:k]] = True
    return out


def ranks(D):
    R = D.groupby("day")[X].rank(pct=True).fillna(0.5)
    return R.to_numpy()


def walk_forward(D):
    import lightgbm as lgb
    from sklearn.linear_model import Ridge
    Xr = ranks(D); D = D.copy(); D["p_ridge"] = np.nan; D["p_lgbm"] = np.nan
    end = D.day + pd.Timedelta(hours=25)
    for ms in pd.date_range(WF_START, SEALED[1], freq="MS"):
        te = ((D.day >= ms) & (D.day < ms + pd.offsets.MonthBegin(1))).to_numpy()
        tr = (end < ms).to_numpy()
        if not te.any() or tr.sum() < 2000:
            continue
        y = D.y.to_numpy()[tr]; y = np.clip(y, *np.nanpercentile(y, [1, 99]))
        D.loc[te, "p_ridge"] = Ridge(alpha=10.0).fit(Xr[tr], y).predict(Xr[te])
        D.loc[te, "p_lgbm"] = lgb.LGBMRegressor(**GBM).fit(Xr[tr], y).predict(Xr[te])
    return D


def book(D, score, top=5):
    rets, prev = [], {}
    for d, g in D.groupby("day"):
        if score is None:
            pick = g
        else:
            pick = g.dropna(subset=[score]).nlargest(top, score)
        if not len(pick):
            continue
        w = {c: g.gate.iloc[0] / len(pick) for c in pick.coin}
        cost = g.set_index("coin").cost
        turn = sum(abs(w.get(c, 0) - prev.get(c, 0)) * cost.get(c, 0.002) for c in set(w) | set(prev))
        rets.append((d, sum(w[c] * r for c, r in zip(pick.coin, pick.y_raw)) - turn)); prev = w
    return pd.Series(dict(rets)).sort_index()


def main():
    t0 = time.time()
    D = dataset(); print(time.strftime("%T"), "dataset", D.shape, D.day.nunique(), flush=True)
    D.to_parquet(ROOT / "data/upbit_db/b57x_dataset.parquet")
    dev = D[(D.day >= DEV[0]) & (D.day < DEV[1])]
    uni = []
    for c in X:
        ic = daily_ic(dev, c)
        uni.append(dict(feature=c, ic=ic.mean(), t=ic.mean() / ic.std(ddof=1) * np.sqrt(len(ic)), days=len(ic),
                        p=boot_mean_p(ic.to_numpy())))
    U = pd.DataFrame(uni); U["bhy_pass"] = bhy(U.p.to_numpy())
    print(U.round(4).to_string(), flush=True)
    D = walk_forward(D)
    win = lambda d, a, z: d[(d.day >= a) & (d.day < z)]  # noqa: E731
    dv = win(D, WF_START, DEV[1])
    dev_ic = {m: daily_ic(dv, f"p_{m}").mean() for m in ("ridge", "lgbm")}
    sel = max(dev_ic, key=dev_ic.get); score = f"p_{sel}"
    dev_book, dev_ew = book(dv, score), book(dv, None)
    # ---- sealed, evaluated once for the selected composite
    se = win(D, *SEALED)
    ic_s = daily_ic(se, score)
    p_ic = boot_mean_p(ic_s.to_numpy(), two_sided=False)
    sb, se_ew = book(se, score), book(se, None)
    idx = sb.index.intersection(se_ew.index)
    diff, p_bk = B.boot_p(sb.reindex(idx).to_numpy(), se_ew.reindex(idx).to_numpy(), B.sharpe, 20, draws=5000)
    import b54_sweep100 as S
    f17 = S.run(S.W_f17()); f17 = f17[(f17.index >= SEALED[0]) & (f17.index < SEALED[1])]
    res = dict(selected=sel, dev_ic=dev_ic,
               dev=dict(book_sharpe=B.sharpe(dev_book), ew_sharpe=B.sharpe(dev_ew), book_maxdd=B.maxdd(dev_book)),
               sealed=dict(ic=float(ic_s.mean()), ic_p=p_ic, ic_pass=bool(ic_s.mean() > 0 and p_ic < 0.025),
                           book_sharpe=B.sharpe(sb), ew_sharpe=B.sharpe(se_ew), sharpe_diff=float(diff), book_p=p_bk,
                           book_pass=bool(diff > 0 and p_bk < 0.025), book_maxdd=B.maxdd(sb), book_cagr=B.cagr(sb),
                           ew_maxdd=B.maxdd(se_ew), f17_sharpe=B.sharpe(f17), f17_maxdd=B.maxdd(f17),
                           f17_cagr=B.cagr(f17)),
               univariate=U.to_dict("records"))
    json.dump(res, open(ROOT / "research/b57x_results.json", "w"), indent=1, default=float)
    with open(ROOT / "research/trial_ledger.csv", "a") as fh:
        for r in U.itertuples():
            fh.write(f"2026-10-06T00:30:00,B57x_{r.feature},Binance flow -> Upbit next-day rel ret (prereg v9),flow,"
                     f"dev 2024-03..2025-09,,,,IC {r.ic:+.4f} p={r.p:.4f} BHY={'pass' if r.bhy_pass else 'fail'}\n")
        s = res["sealed"]
        fh.write(f"2026-10-06T00:30:00,B57x_composite_{sel},Binance flow composite (prereg v9),flow,sealed 2025-10..2026-09,"
                 f"{s['book_sharpe']:.3f},,,IC {s['ic']:+.4f} p={s['ic_p']:.4f} ({'PASS' if s['ic_pass'] else 'FAIL'}); "
                 f"book vs EW diff {s['sharpe_diff']:+.2f} p={s['book_p']:.4f} ({'PASS' if s['book_pass'] else 'FAIL'})\n")
    print(json.dumps({k: v for k, v in res.items() if k != "univariate"}, indent=1, default=float))
    print(f"done {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
