"""B17 F02: second-level onset triggers on 3,713 pumps with 1-second bars (data/cache/b17/sec1). Registry F02_onset_long (330 cells).
  OMP_NUM_THREADS=6 .venv/bin/python -W ignore scripts/b17_f02.py [triggers|trade]   -> data/cache/b17/f02_triggers.parquet, data/reports/b17/f02.json
Window per pump: rows 0..7499 = seconds m0-65min .. m0+60min; row 3900 = first second of minute m0; minute m0 closes at row 3959.
Search window for a trigger: [m0-60min, m0+1min) = rows 300..3959 (at or before the +10% mark, which is met at the m0 close).
Baseline = rows 0..299 (the 5 minutes before the pump hour). Deviation: the registry says '1h baseline'; only 5 min of pre-pump data is in the window.
Triggers (first firing in the search window, evaluated on 1s / 15s / 1m bars):
  tps     trades per bar > 8x baseline per-bar rate
  taker   taker-buy share over the trailing 15s > 0.8 AND 15s volume > 5x baseline 15s volume
  sweep   ask-sweep proxy (no book ticks): bar range > 5x baseline median range AND buy share > 0.6
  liq     >= 3 short liquidations of the symbol within 30s (Postgres `liquidations`; data begin 2026-07-17 so only ~10 weeks of pumps; noted)
  oi      5-min OI +2% with price < +2% (data/cache/b17/metrics5m; skipped if not yet downloaded)
  cp      Bayesian online change-point (Adams-MacKay, Gaussian unknown mean, hazard 1/200, run length <= 300) on 1s returns: P(run length < 5) > 0.5
  hawkes  branching-ratio estimate from the dispersion of buy-trade counts over the trailing 60 bars: n_hat = 1 - 1/sqrt(var/mean) > 0.8
Entry at the 1s close `latency` seconds after the trigger (5/30/120). Exits: t15m, t1h (1s data), t4h (minute paths after +60m), trail2 (2x baseline
1-min ATR from the running high), trail3 (3% from the running high; stands in for the hazard exit). Cost 2x(fee+slip(dv24)); simple returns.
Control: same pump, random second in the search window, same latency and exit. Validation = onsets >= 2026-01-01; BH q=0.10 within family; MAE p90 < 15%."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b15_models as M  # noqa: E402

B = ROOT / "data/cache/b17"
OUT = ROOT / "data/reports/b17"
RNG = np.random.default_rng(1702)
VAL0 = 1767225600
R0, R1, RM0, RM0C = 300, 3960, 3900, 3959
LAT = [5, 30, 120]
EXITS = ["t15m", "t1h", "t4h", "trail2", "trail3"]
RES = {"1s": 1, "15s": 15, "1m": 60}


def agg(x, k, how):
    n = len(x) // k
    y = x[:n * k].reshape(n, k)
    return {"sum": y.sum(1), "max": y.max(1), "min": y.min(1), "last": y[:, -1]}[how]


def bocpd(r, sigma, hazard=1 / 200, maxrl=300):
    """Adams-MacKay with Gaussian likelihood, unknown mean (prior N(0, sigma^2)), known variance sigma^2. Returns P(rl < 5) per step."""
    T = len(r); out = np.zeros(T)
    P = np.zeros(maxrl + 1); P[0] = 1.0
    mu = np.zeros(maxrl + 1); kappa = np.ones(maxrl + 1)
    for t in range(T):
        x = r[t] / sigma
        pv = 1 + 1 / kappa
        pred = np.exp(-0.5 * (x - mu) ** 2 / pv) / np.sqrt(2 * np.pi * pv)
        growth = P * pred * (1 - hazard)
        cp = (P * pred * hazard).sum()
        Pn = np.zeros_like(P); Pn[1:] = growth[:-1]; Pn[-1] += growth[-1]; Pn[0] = cp
        s = Pn.sum(); P = Pn / s if s > 0 else P
        mu_n = np.empty_like(mu); kappa_n = np.empty_like(kappa)
        mu_n[1:] = (kappa[:-1] * mu[:-1] + x) / (kappa[:-1] + 1); kappa_n[1:] = kappa[:-1] + 1; mu_n[0] = 0; kappa_n[0] = 1
        mu, kappa = mu_n, kappa_n
        out[t] = P[:5].sum()
    return out


def triggers_one(pid, d, liq_times):
    c, h, l = d["c"].to_numpy(float), d["h"].to_numpy(float), d["l"].to_numpy(float)
    qv, n, tb, bn = d["qv"].to_numpy(float), d["n"].to_numpy(float), d["tbqv"].to_numpy(float), d["buy_n"].to_numpy(float)
    rng = h / np.where(l > 0, l, np.nan) - 1
    base = slice(0, R0)
    out = {"pump_id": pid}
    for res, k in RES.items():
        nb, qb, tbb, bnb = agg(n, k, "sum"), agg(qv, k, "sum"), agg(tb, k, "sum"), agg(bn, k, "sum")
        hb, lb, cb = agg(h, k, "max"), agg(l, k, "min"), agg(c, k, "last")
        rb = hb / np.where(lb > 0, lb, np.nan) - 1
        b0, b1 = 0, R0 // k
        s0, s1 = R1 // k * 0 + (R0 // k), R1 // k
        base_n = max(nb[b0:b1].mean(), 1e-9); base_q = max(qb[b0:b1].mean(), 1e-9); base_r = np.nanmedian(rb[b0:b1]) if np.isfinite(rb[b0:b1]).any() else np.nan
        w15 = max(1, 15 // k)
        cs_q = np.cumsum(np.r_[0, qb]); cs_tb = np.cumsum(np.r_[0, tbb])
        q15 = cs_q[w15:] - cs_q[:-w15]; tb15 = cs_tb[w15:] - cs_tb[:-w15]; share15 = np.r_[np.full(w15 - 1, np.nan), tb15 / np.where(q15 > 0, q15, np.nan)]
        q15f = np.r_[np.full(w15 - 1, np.nan), q15]
        T = {"tps": nb > 8 * base_n,
             "taker": (share15 > 0.8) & (q15f > 5 * base_q * w15),
             "sweep": (rb > 5 * base_r) & (tbb / np.where(qb > 0, qb, np.nan) > 0.6) if np.isfinite(base_r) and base_r > 0 else np.zeros(len(nb), bool)}
        w60 = max(1, 60 // k)
        if len(bnb) > w60:
            roll = np.lib.stride_tricks.sliding_window_view(bnb, w60)
            m, v = roll.mean(1), roll.var(1)
            nh = 1 - 1 / np.sqrt(np.where(m > 0, v / np.where(m > 0, m, 1), 1).clip(1, None))
            T["hawkes"] = np.r_[np.zeros(w60 - 1, bool), nh > 0.8]
        if res == "1s":
            r1 = np.diff(np.log(np.where(c > 0, c, np.nan)), prepend=np.nan); r1 = np.nan_to_num(r1)
            sig = max(np.std(r1[base]), 1e-5)
            T["cp"] = bocpd(r1, sig) > 0.5
        if liq_times is not None and len(liq_times):
            tsb = agg(d["ts"].to_numpy(), k, "last")
            cnt = np.searchsorted(liq_times, tsb, "right") - np.searchsorted(liq_times, tsb - 30)
            T["liq"] = cnt >= 3
        for name, arr in T.items():
            idx = np.flatnonzero(arr[s0:s1]) if len(arr) >= s1 else []
            out[f"{name}|{res}"] = int((s0 + idx[0]) * k + k - 1) if len(idx) else -1   # last second of the firing bar
    return out


def triggers():
    S = pd.read_parquet(B / "sample.parquet")
    try:
        from src.data.storage import get_storage
        with get_storage()._connect() as con:
            L = pd.DataFrame(con.raw.execute("select symbol, timestamp from liquidations where side='short' and exchange='binance'").fetchall())
        L["code"] = L["symbol"].str.replace("/", "", regex=False)
        liq = {c: np.sort(g["timestamp"].to_numpy()) for c, g in L.groupby("code")}
    except Exception as e:  # noqa: BLE001
        print("liquidations unavailable", e); liq = {}
    rows = []
    for i, (pid, code) in enumerate(zip(S["pump_id"], S["code"])):
        fp = B / f"sec1/{pid}.parquet"
        if not fp.exists():
            continue
        d = pd.read_parquet(fp)
        if len(d) != 7500:
            continue
        rows.append(triggers_one(int(pid), d, liq.get(code)))
        if i % 200 == 0:
            print(i, flush=True)
    pd.DataFrame(rows).to_parquet(B / "f02_triggers.parquet", index=False)
    print("done", len(rows))


def leg(c, h, l, mpath, c_m0, atr1m, t_entry, exit_):
    """Long from the 1s close at row t_entry. Returns (gross, mae). Minute path used beyond row 7499."""
    e = c[t_entry]
    if not np.isfinite(e) or e <= 0:
        return np.nan, np.nan
    # second-resolution part (up to row 7499 = m0+60min)
    hz = {"t15m": 900, "t1h": 3600, "t4h": 14400, "trail2": 14400, "trail3": 14400}[exit_]
    end_s = min(t_entry + hz, 7499)
    hs, ls, cs = h[t_entry + 1:end_s + 1], l[t_entry + 1:end_s + 1], c[t_entry + 1:end_s + 1]
    # minute-resolution continuation: minutes after m0+60 up to the horizon
    extra_min = max(0, (t_entry + hz - 7499) // 60)
    m_start = 61                                              # first minute after m0+60
    mret = mpath[60 + m_start:60 + m_start + extra_min] if extra_min else np.array([])
    mpx = c_m0 * (1 + mret)
    dist = {"trail2": 2 * atr1m, "trail3": 0.03}.get(exit_)
    run = e; mae_low = e
    if dist is None:
        px_all = np.r_[cs, mpx]
        if len(px_all) == 0:
            return np.nan, np.nan
        lo = np.nanmin(np.r_[ls, mpx]) if len(ls) or len(mpx) else e
        return px_all[-1] / e - 1, 1 - lo / e
    for k in range(len(cs)):
        run = max(run, hs[k]); mae_low = min(mae_low, ls[k])
        if ls[k] <= run * (1 - dist):
            return run * (1 - dist) / e - 1, 1 - mae_low / e
    for k in range(len(mpx)):
        run = max(run, mpx[k]); mae_low = min(mae_low, mpx[k])
        if mpx[k] <= run * (1 - dist):
            return run * (1 - dist) / e - 1, 1 - mae_low / e
    last = mpx[-1] if len(mpx) else (cs[-1] if len(cs) else e)
    return last / e - 1, 1 - mae_low / e


def trade():
    S = pd.read_parquet(B / "sample.parquet").set_index("pump_id")
    TR = pd.read_parquet(B / "f02_triggers.parquet").set_index("pump_id")
    P = M.pump_context().set_index("pump_id")
    paths = np.load(M.C / "b15/paths.npy", mmap_mode="r")
    cells = [c for c in TR.columns]
    recs = []
    for i, pid in enumerate(TR.index):
        d = pd.read_parquet(B / f"sec1/{pid}.parquet")
        c, h, l = d["c"].to_numpy(float), d["h"].to_numpy(float), d["l"].to_numpy(float)
        c = pd.Series(c).ffill().to_numpy(); h = np.where(np.isfinite(h), h, c); l = np.where(np.isfinite(l), l, c)
        atr1m = max(np.nanmean((h[:R0].reshape(5, 60).max(1) / l[:R0].reshape(5, 60).min(1)) - 1), 0.005)
        mpath = np.asarray(paths[pid][:, 0], np.float64); c_m0 = c[RM0C]
        cost = 2 * (M.L.FEE + float(M.slip(np.array([P.loc[pid, "dv24"]]))[0]))
        day = int(P.loc[pid, "ts"] // 86400); val = P.loc[pid, "ts"] >= VAL0
        ctrl_t = int(RNG.integers(R0, R1))
        for cell in cells:
            t_fire = int(TR.loc[pid, cell])
            for lat in LAT:
                for ex in EXITS:
                    for kind, t0 in (("signal", t_fire), ("control", ctrl_t)):
                        if t0 < 0 or t0 + lat >= 7499:
                            continue
                        g, mae = leg(c, h, l, mpath, c_m0, atr1m, t0 + lat, ex)
                        recs.append((pid, cell, lat, ex, kind, t_fire - RM0 if kind == "signal" else np.nan, g - cost, mae, day, val))
        if i % 200 == 0:
            print(i, len(recs), flush=True)
    E = pd.DataFrame(recs, columns=["pump_id", "cell", "lat", "exit", "kind", "fire_s", "net", "mae", "day", "val"]).dropna(subset=["net"])
    E.to_parquet(B / "f02_trades.parquet", index=False)
    res = {"n_pumps": int(TR.index.nunique()), "fire_rate": {}, "tests": {}}
    for cell in cells:
        f = TR[cell]; res["fire_rate"][cell] = dict(share=float((f >= 0).mean()), median_fire_s_vs_m0=float((f[f >= 0] - RM0).median()) if (f >= 0).any() else None)
    pv = {}
    for (cell, lat, ex), g in E.groupby(["cell", "lat", "exit"]):
        key = f"{cell}|{lat}s|{ex}"; out = {}
        for per, sel in (("discovery", ~g["val"]), ("validation", g["val"])):
            s = g[sel & (g["kind"] == "signal")]; cg = g[sel & (g["kind"] == "control")]
            if len(s) < 30:
                continue
            ci = M.day_ci(s["net"].to_numpy(), s["day"].to_numpy())
            b = s.groupby("day")["net"].agg(["sum", "count"])
            bo = np.array([b["sum"].to_numpy()[k].sum() / b["count"].to_numpy()[k].sum() for k in (RNG.integers(0, len(b), len(b)) for _ in range(2000))])
            out[per] = dict(n=int(len(s)), mean=float(s["net"].mean()), ci=ci, p=float((bo <= 0).mean()), win=float((s["net"] > 0).mean()),
                            mae90=float(s["mae"].quantile(0.9)), fire_s_median=float(s["fire_s"].median()), ctrl_mean=float(cg["net"].mean()) if len(cg) else None)
        if "validation" in out:
            v = out["validation"]; v["beats_control"] = bool(v["ctrl_mean"] is not None and v["ci"][0] > max(0.0, v["ctrl_mean"]))
            pv[key] = v["p"]; res["tests"][key] = out
    names = sorted(pv, key=pv.get)
    last = max([k for k, nm in enumerate(names) if pv[nm] <= 0.10 * (k + 1) / len(names)], default=-1)
    res["bh_pass"] = [nm for k, nm in enumerate(names) if k <= last]
    res["pass"] = [k for k in res["bh_pass"] if res["tests"][k]["validation"]["beats_control"] and res["tests"][k]["validation"]["mae90"] < 0.15]
    OUT.mkdir(parents=True, exist_ok=True); json.dump(res, open(OUT / "f02.json", "w"), indent=1, default=float)
    print("F02 cells", len(res["tests"]), "BH", len(res["bh_pass"]), "PASS", res["pass"])
    top = sorted(((k, v["validation"]) for k, v in res["tests"].items()), key=lambda kv: -kv[1]["mean"])[:12]
    for k, v in top:
        print(k, v["n"], round(v["mean"], 4), [round(x, 4) for x in v["ci"]], "ctrl", round(v["ctrl_mean"] or 0, 4), "mae90", round(v["mae90"], 3), "fire", v["fire_s_median"])


if __name__ == "__main__":
    {"triggers": triggers, "trade": trade}[sys.argv[1]]()
