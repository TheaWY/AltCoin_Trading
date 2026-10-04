"""B42 (2026-10-04, asked by 유리): an Upbit-native, LONG-ONLY pump strategy ("F2U") that cuts the losses of B41.

B41: buying every Upbit +10%/1h pump at T+60s loses -3.9% over 4h (76% losers); the Binance-trained F2 CNN made it worse.
Here everything is fitted on Upbit's own data (data/upbit_db, built by scripts/upbit_db.py), long only (Korea: no shorts).

Pre-registered protocol (fixed before looking at any result):
  split by event time   train 2024-01-01..2025-06-30 | validation 2025-07-01..2025-12-31 | TEST 2026-01-01..now (touched once)
  entries (long)        T+1m, T+15m, T+30m, T+60m, T+120m   (T = close of the +10% hour; open of that minute)
  exits                 hold 1h, 4h, 8h; trailing (arm +15% on closes, give back 10 points); stop -8% on closes
  cost                  2 x 0.05% Upbit fee + 2 x slippage by 24h value (USD tiers as F2), +1 slippage leg for stop/trail exits
  model                 LightGBM regressor on pre-entry features -> predicted net; one model per entry delay, trained on
                        train only; on validation choose (entry, exit, threshold on predicted net) maximising mean net with
                        >= 30 validation trades; then report that single configuration on TEST with a day-clustered 95% CI.
  also reported         the same grid without a model (trade every event) so the model's value is visible.
Success = TEST mean net > 0 with CI lower bound > 0. Anything else is reported as a failure, not re-tuned.
Run 1 (22:28) was INVALID: pre_ret_1h used c[-1] (end of window, T+12h) through negative-index wrap-around.
Output: research/b42_upbit_long.md, data/upbit_db/b42_dataset.parquet. Paper research only.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/upbit_db"
FEE, USDKRW = 0.0005, 1370.0
DELAYS = [1, 15, 30, 60, 120]                          # minutes after T
EXITS = {"hold1h": 60, "hold4h": 240, "hold8h": 480}
TRAIN_END, VAL_END = pd.Timestamp("2025-07-01").timestamp(), pd.Timestamp("2026-01-01").timestamp()


def slip(v24_krw):
    dv = v24_krw / USDKRW
    return np.where(dv > 1e8, 0.0002, np.where(dv > 2e7, 0.0005, np.where(dv > 5e6, 0.0010, 0.0020)))


def era(t):
    return "train" if t < TRAIN_END else "val" if t < VAL_END else "test"


def hourly_ctx():
    h = {}
    for f in (DB / "h1").glob("*.parquet"):
        g = pd.read_parquet(f).set_index("ts").sort_index()
        h[f.stem] = g
    return h


def ctx_at(g, T):
    """hourly context known at T (candles starting before T)."""
    x = g.loc[:T - 3600]
    if len(x) < 30:
        return {}
    c = x["c"]
    last = c.iloc[-1]
    out = {"ret_24h": last / c.iloc[-25] - 1 if len(c) > 25 else np.nan,
           "ret_7d": last / c.iloc[-169] - 1 if len(c) > 169 else np.nan,
           "age_d": (T - x.index[0]) / 86400,
           "vsurge_h": x["v"].iloc[-1] / (x["v"].iloc[-169:-1].mean() + 1),
           "upwick_h": (x["h"].iloc[-1] - last) / max(x["h"].iloc[-1] - x["l"].iloc[-1], 1e-12),
           "range_7d": c.iloc[-169:].max() / c.iloc[-169:].min() - 1 if len(c) > 169 else np.nan}
    return out


def sim(o, c, i0, cost):
    """nets for every exit from entry minute index i0 (entry at open[i0])."""
    e0 = o[i0]
    res = {}
    for k, m in EXITS.items():
        res[k] = o[i0 + m] / e0 - 1 - cost if i0 + m < len(o) else np.nan
    rc = c[i0:i0 + 480] / e0 - 1
    trail, stop = res["hold8h"], res["hold8h"]
    pk = -1e9
    for k in range(len(rc) - 1):
        pk = max(pk, rc[k])
        if pk >= 0.15 and rc[k] <= pk - 0.10:
            trail = o[i0 + k + 1] / e0 - 1 - cost * 1.5
            break
    for k in range(len(rc) - 1):
        if rc[k] <= -0.08:
            stop = o[i0 + k + 1] / e0 - 1 - cost * 1.5
            break
    res["trail"], res["stop8_8h"] = trail, stop
    return res


def build():
    ev = pd.read_parquet(DB / "events.parquet")
    H = hourly_ctx()
    btc = H.get("KRW-BTC")
    rows = []
    ev_t = ev["T"].to_numpy()
    for j, r in ev.iterrows():
        T = int(r["T"])
        f = DB / "m1" / f"{r.market}_{T}.parquet"
        if not f.exists():
            continue
        w = pd.read_parquet(f).set_index("ts")
        idx = np.arange(T - 7200, T + 12 * 3600, 60)
        w = w.reindex(idx)
        if w["c"].notna().sum() < 300:
            continue
        w["c"] = w["c"].ffill().bfill()
        for k in ("o", "h", "l"):
            w[k] = w[k].fillna(w["c"])
        w["v"] = w["v"].fillna(0)
        o, h, l_, c, v = (w[k].to_numpy(float) for k in ("o", "h", "l", "c", "v"))
        iT = 120                                           # index of minute starting at T
        cost = float(2 * (FEE + slip(r.v24_krw)))
        base = {"market": r.market, "T": T, "era": era(T), "ret_1h": r.ret_1h, "lv24": np.log10(r.v24_krw),
                "hour_kst": ((T // 3600) + 9) % 24, "weekday": pd.Timestamp(T, unit="s").weekday(),
                "pumps_30d": int(((ev_t < T) & (ev_t >= T - 30 * 86400) & (ev.market.to_numpy() == r.market)).sum()),
                "breadth_24h": int(((ev_t < T) & (ev_t >= T - 86400)).sum()),
                "pre_ret_1h": c[iT - 61] / c[0] - 1}          # BUGFIX 2026-10-04: was c[iT-121] = c[-1] (wraps to the END of the window = look-ahead)
        base.update(ctx_at(H[r.market], T) if r.market in H else {})
        if btc is not None:
            b = btc.loc[:T - 3600, "c"]
            if len(b) > 25:
                base["btc_1h"], base["btc_24h"] = b.iloc[-1] / b.iloc[-2] - 1, b.iloc[-1] / b.iloc[-25] - 1
        for d in DELAYS:
            i0 = iT + d
            if i0 + 480 >= len(o):
                continue
            pre = c[iT - 1]
            hi_since = h[iT - 60:i0].max()
            feat = dict(base, delay=d,
                        ret_since_T=c[i0 - 1] / pre - 1, peak_since=h[iT:i0].max() / pre - 1 if i0 > iT else 0.0,
                        dd_from_peak=c[i0 - 1] / hi_since - 1, ret_5m=c[i0 - 1] / c[i0 - 6] - 1, ret_15m=c[i0 - 1] / c[i0 - 16] - 1,
                        vol_ratio_15_60=v[i0 - 15:i0].sum() / (v[i0 - 60:i0].sum() + 1),
                        vol_ratio_60_prev=v[i0 - 60:i0].sum() / (v[iT - 120:iT - 60].sum() + 1),
                        rv_30m=float(np.std(np.diff(np.log(c[i0 - 31:i0])))) if i0 > 31 else np.nan)
            feat.update(sim(o, c, i0, cost))
            rows.append(feat)
        if j % 200 == 0:
            print(time.strftime("%H:%M:%S"), "built", j, len(rows), flush=True)
    d = pd.DataFrame(rows)
    d.to_parquet(DB / "b42_dataset.parquet", index=False)
    return d


FEATS = ["ret_1h", "lv24", "hour_kst", "weekday", "pumps_30d", "breadth_24h", "pre_ret_1h", "ret_24h", "ret_7d", "age_d",
         "vsurge_h", "upwick_h", "range_7d", "btc_1h", "btc_24h", "ret_since_T", "peak_since", "dd_from_peak", "ret_5m",
         "ret_15m", "vol_ratio_15_60", "vol_ratio_60_prev", "rv_30m"]
EXIT_COLS = ["hold1h", "hold4h", "hold8h", "trail", "stop8_8h"]


def boot(x, day, n=2000, seed=9):
    rng = np.random.default_rng(seed); u = np.unique(day)
    g = {k: x[day == k] for k in u}
    m = [np.concatenate([g[k] for k in rng.choice(u, len(u))]).mean() for _ in range(n)]
    return np.percentile(m, [2.5, 97.5])


def main():
    import lightgbm as lgb
    t0 = time.time()
    d = build()
    d["day"] = d["T"] // 86400
    L = ["# B42 Upbit-native long-only pump strategy (F2U) (2026-10-04)", "",
         f"{d['T'].nunique()} Upbit KRW +10%/1h events with 1m paths ({pd.Timestamp(d['T'].min(), unit='s'):%Y-%m-%d}.."
         f"{pd.Timestamp(d['T'].max(), unit='s'):%Y-%m-%d}); events per era: "
         + ", ".join(f"{k} {v}" for k, v in d.drop_duplicates('T').groupby('era').size().items()) + ".",
         "Long only, Upbit fees 2x0.05% + slippage. Protocol pre-registered in the script docstring.", "",
         "## No model: buy every pump (mean net by entry delay x exit)", ""]
    for er in ("train", "val", "test"):
        x = d[d.era == er]
        if not len(x):
            continue
        tab = x.groupby("delay")[EXIT_COLS].mean().map(lambda v: f"{v:+.2%}")
        L += [f"**{er}** (n events {x['T'].nunique()})", "", tab.to_markdown(), ""]
    # one model per delay, train only, target = net of each exit
    preds = []
    for dl in DELAYS:
        x = d[d.delay == dl].copy()
        tr = x[x.era == "train"]
        for ex in EXIT_COLS:
            y = tr[ex]
            ok = y.notna()
            if ok.sum() < 200:
                continue
            m = lgb.LGBMRegressor(n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=40,
                                  subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0, verbose=-1, random_state=1)
            m.fit(tr.loc[ok, FEATS], y[ok])
            x["pred"] = m.predict(x[FEATS])
            x["exit"] = ex
            x["net"] = x[ex]
            preds.append(x[["T", "day", "era", "market", "delay", "exit", "pred", "net"]])
    P = pd.concat(preds, ignore_index=True)
    # choose on validation
    best = None
    grid = []
    for (dl, ex), g in P[P.era == "val"].groupby(["delay", "exit"]):
        for q in (0.0, 0.5, 0.7, 0.8, 0.9):
            thr = float(np.quantile(P[(P.era == "train") & (P.delay == dl) & (P.exit == ex)].pred, q)) if q > 0 else -1e9
            s = g[g.pred > thr].dropna(subset=["net"])
            if len(s) < 30:
                continue
            grid.append((dl, ex, q, thr, len(s), s.net.mean()))
            if best is None or s.net.mean() > best[5]:
                best = (dl, ex, q, thr, len(s), s.net.mean())
    G = pd.DataFrame(grid, columns=["delay", "exit", "train_quantile", "thr", "val_n", "val_mean"]).sort_values("val_mean", ascending=False)
    L += ["## Validation grid (model filter), top 10", "", G.head(10).to_markdown(index=False, floatfmt="+.4f"), ""]
    dl, ex, q, thr, vn, vm = best
    te = P[(P.era == "test") & (P.delay == dl) & (P.exit == ex)].dropna(subset=["net"])
    pick = te[te.pred > thr]
    allb = te
    lo, hi = boot(pick.net.to_numpy(), pick.day.to_numpy()) if len(pick) >= 10 else (np.nan, np.nan)
    L += ["## TEST (2026, touched once) for the validation-chosen configuration", "",
          f"Chosen on validation: entry T+{dl}m, exit {ex}, threshold = train-prediction quantile {q} (val n={vn}, val mean {vm:+.2%}).", "",
          "| set | n | mean net | median | win | p10 | worst | 95% CI (day-clustered) |", "|---|---|---|---|---|---|---|---|"]
    for nm, s, ci in (("model picks", pick, (lo, hi)), ("all pumps, same entry/exit", allb, boot(allb.net.to_numpy(), allb.day.to_numpy()))):
        L.append(f"| {nm} | {len(s)} | {s.net.mean():+.2%} | {s.net.median():+.2%} | {(s.net > 0).mean():.0%} | "
                 f"{s.net.quantile(.1):+.1%} | {s.net.min():+.1%} | [{ci[0]:+.2%}, {ci[1]:+.2%}] |")
    verdict = "PASS" if len(pick) >= 30 and lo > 0 else "FAIL"
    L += ["", f"Verdict under the pre-registered rule: **{verdict}**.", f"Runtime {time.time() - t0:.0f}s."]
    P.to_parquet(DB / "b42_predictions.parquet", index=False)
    (ROOT / "research/b42_upbit_long.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
