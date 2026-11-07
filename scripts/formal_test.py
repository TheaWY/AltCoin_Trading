"""Formal, pre-registered strategy test (see research/registry.yaml).

  .venv/bin/python -W ignore scripts/formal_test.py --period DEV
  .venv/bin/python -W ignore scripts/formal_test.py --period OOT      # once only; refuses a rerun

Pipeline: 1m klines -> hourly features (causal) -> trades per registered rule ->
1-minute execution (next-minute entry, stops/targets on 1m high/low, gap fills,
both-touch = stop) -> fees + volume-based slippage + actual funding -> daily
returns -> Sharpe, block-bootstrap CI, deflated Sharpe (N = this set and the
whole ledger), PBO (CSCV), BTC regime split -> report + trial ledger.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
REG = ROOT / "research" / "registry.yaml"
LEDGER = ROOT / "research" / "trial_ledger.csv"
OUT = ROOT / "data" / "reports" / "formal"
DIRS = {"DEV": ("data/cache/k1m", "data/cache/funding"), "OOT": ("data/cache/k1m_oot", "data/cache/funding_oot")}
H_, D_ = 3600, 86400
FEE = 0.0005
SPOT_FEE = 0.001


def slip(dv):
    dv = np.asarray(dv, float)
    return np.select([dv > 1e8, dv > 2e7, dv > 5e6], [0.0002, 0.0005, 0.0010], 0.0020)


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# ------------------------------------------------------------------ data

def load_1m(path: Path) -> pd.DataFrame | None:
    d = pd.read_parquet(path)
    if len(d) < 3 * 1440:
        return None
    idx = np.arange(int(d["ts"].iloc[0]), int(d["ts"].iloc[-1]) + 60, 60)
    d = d.drop_duplicates("ts").set_index("ts").reindex(idx)
    d["c"] = d["c"].ffill()
    for k in ("o", "h", "l"):
        d[k] = d[k].fillna(d["c"])
    for k in ("qv", "tbq"):
        d[k] = d[k].fillna(0.0)
    return d


def load_funding(fdir: Path, code: str) -> pd.DataFrame:
    f = fdir / f"{code}.parquet"
    if not f.exists():
        return pd.DataFrame({"ts": np.array([], "int64"), "f": [], "f8": []})
    d = pd.read_parquet(f, columns=["ts", "f"]).drop_duplicates("ts").sort_values("ts")
    gap = d["ts"].diff().bfill().clip(lower=H_, upper=8 * H_) / H_
    d["f8"] = d["f"] * 8 / gap
    return d.reset_index(drop=True)


def hourly_one(args) -> pd.DataFrame | None:
    code, kdir, fdir = args
    d = load_1m(Path(kdir) / f"{code}.parquet")
    if d is None:
        return None
    lr = np.log(d["c"].astype(np.float64)).diff()
    rv24 = lr.rolling(1440, min_periods=720).std() * math.sqrt(1440)
    qv60 = d["qv"].rolling(60, min_periods=1).sum()
    tb60 = d["tbq"].rolling(60, min_periods=1).sum()
    ends = d.index[(d.index + 60) % H_ == 0]
    T = ends + 60
    H = pd.DataFrame({"ts": T, "c": d["c"].reindex(ends).to_numpy(np.float64),
                      "qv": qv60.reindex(ends).to_numpy(), "tb": tb60.reindex(ends).to_numpy(),
                      "rv24": rv24.reindex(ends).to_numpy()})
    c = H["c"]
    H["ret_1h"] = c / c.shift(1) - 1
    H["ret_4h"] = c / c.shift(4) - 1
    H["ret_24h"] = c / c.shift(24) - 1
    H["ret_7d"] = c / c.shift(168) - 1
    H["ret_28d"] = c / c.shift(672) - 1
    H["rv_7d"] = H["ret_1h"].rolling(168, min_periods=72).std() * math.sqrt(24)
    H["max_7d"] = H["ret_1h"].rolling(168, min_periods=72).max()
    H["dv24"] = H["qv"].rolling(24, min_periods=12).sum()
    H["taker_1h"] = H["tb"] / H["qv"].replace(0, np.nan)
    H["taker_24h"] = H["tb"].rolling(24, min_periods=12).sum() / H["dv24"].replace(0, np.nan)
    H["vsurge"] = H["qv"] / H["qv"].rolling(168, min_periods=72).mean().shift(1)
    H["age_h"] = np.arange(len(H))
    H["fwd24"] = c.shift(-24) / c - 1
    fu = load_funding(Path(fdir), code)
    if len(fu):
        cs = np.concatenate([[0.0], np.cumsum(fu["f8"].to_numpy())])
        ts = fu["ts"].to_numpy()
        hi = np.searchsorted(ts, H["ts"].to_numpy(), side="right")
        lo = np.searchsorted(ts, H["ts"].to_numpy() - D_, side="right")
        n = hi - lo
        H["fund24"] = np.where(n > 0, (cs[hi] - cs[lo]) / np.maximum(n, 1), np.nan)
    else:
        H["fund24"] = np.nan
    H["code"] = code
    keep = ["code", "ts", "c", "ret_1h", "ret_4h", "ret_24h", "ret_7d", "ret_28d", "rv24", "rv_7d", "max_7d", "dv24",
            "taker_1h", "taker_24h", "vsurge", "age_h", "fwd24", "fund24"]
    H = H[keep]
    for k in keep[2:]:
        H[k] = H[k].astype(np.float32)
    return H


def build_hourly(period: str, excl: set[str]) -> pd.DataFrame:
    cache = ROOT / "data" / "cache" / f"formal_hourly_{period}.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    kdir, fdir = (ROOT / x for x in DIRS[period])
    codes = sorted(p.stem for p in kdir.glob("*.parquet") if p.stem.endswith("USDT")
                   and p.stem.removesuffix("USDT") not in excl)
    parts = []
    with ProcessPoolExecutor(8) as ex:
        for i, h in enumerate(ex.map(hourly_one, [(c, str(kdir), str(fdir)) for c in codes], chunksize=4)):
            if h is not None:
                parts.append(h)
            if i % 100 == 0:
                log(f"hourly {i}/{len(codes)}")
    P = pd.concat(parts, ignore_index=True)
    P.to_parquet(cache, index=False)
    return P


# ------------------------------------------------------------------ strategies -> trades

def universe(P: pd.DataFrame, floor: float) -> pd.Series:
    return (P["age_h"] >= 720) & (P["dv24"] >= floor) & P["c"].notna()


def pick(df: pd.DataFrame, col: str, n: int, largest: bool) -> pd.DataFrame:
    d = df.dropna(subset=[col])
    return d.sort_values(["ts", col], ascending=[True, not largest]).groupby("ts").head(n)


def trades_for(h: dict, P: pd.DataFrame, t0: int, t1: int) -> pd.DataFrame:
    sid, pr, fl = h["id"], h.get("params", {}), float(h.get("universe_floor_usd", 0))
    U = P[universe(P, fl) & (P["ts"] >= t0) & (P["ts"] <= t1)]
    day = U[U["ts"] % D_ == 0]
    cols = ["code", "ts", "dv24"]
    if sid == "S1_rev4h_ls":
        q = U[U["ts"] % (4 * H_) == 0]
        lo = pick(q, "ret_4h", pr["n"], False)[cols].assign(side=1, w=0.1)
        hi = pick(q, "ret_4h", pr["n"], True)[cols].assign(side=-1, w=0.1)
        t = pd.concat([lo, hi]).assign(kind="fixed", hold=pr["hold_min"])
    elif sid == "S2_lowvol_btc":
        alts = day[day["code"] != "BTCUSDT"]
        lo = pick(alts, "rv_7d", pr["n"], False)[cols].assign(side=1, w=0.5 / pr["n"])
        btc = P[(P["code"] == "BTCUSDT") & P["ts"].isin(lo["ts"].unique())][cols].assign(side=-1, w=0.5)
        t = pd.concat([lo, btc]).assign(kind="fixed", hold=pr["hold_min"])
    elif sid == "S4_tsmom28":
        mon = U[(U["ts"] % D_ == 0) & (((U["ts"] // D_) + 3) % 7 == 0)].dropna(subset=["ret_28d", "rv_7d"])
        mon = mon[mon["rv_7d"] > 0].copy()
        mon["raw"] = np.sign(mon["ret_28d"]) / mon["rv_7d"]
        mon["w"] = mon["raw"].abs() / mon.groupby("ts")["raw"].transform(lambda x: x.abs().sum())
        t = mon[cols].assign(side=np.sign(mon["ret_28d"]).astype(int), w=mon["w"], kind="fixed",
                             hold=pr["hold_min"])
        t = t[t["side"] != 0]
    elif sid == "S5_breakout8":
        t = pick(day, "rv24", pr["n"], True)[cols].assign(side=0, w=0.1, kind="bo", hold=pr["hold_min"], x=pr["x"])
    elif sid in ("S7_pump_fade", "C2_pump_chase", "R1_pump_ride_trail", "R2_pump_ride_10"):
        e = U[U["ret_1h"] >= pr["trigger"]].sort_values(["code", "ts"])
        keep, last = [], {}
        for r in e[["code", "ts"]].itertuples(index=False):
            if r.ts - last.get(r.code, -10 ** 12) >= D_:
                keep.append(True)
                last[r.code] = r.ts
            else:
                keep.append(False)
        e = e[np.array(keep, bool)]
        side = -1 if sid == "S7_pump_fade" else 1
        if sid == "R1_pump_ride_trail":
            t = e[cols].assign(side=1, w=0.1, kind="trail", hold=pr["hold_min"], stop=pr["stop"], trail=pr["trail"])
        else:
            t = e[cols].assign(side=side, w=0.1, kind="st", hold=pr["hold_min"], stop=pr["stop"],
                               target=pr["target"])
    elif sid == "S9_ml_rank":
        t = ml_trades(P, pr, fl, t0, t1)
    elif sid == "C1_random_long":
        rng = np.random.default_rng(pr["seed"])
        dd = day.assign(rnd=rng.random(len(day)))
        t = pick(dd, "rnd", pr["n"], False)[cols].assign(side=1, w=0.1, kind="fixed", hold=pr["hold_min"])
    else:
        return pd.DataFrame()
    t["sid"] = sid
    return t.reset_index(drop=True)


ML_FEATS = ["ret_1h", "ret_4h", "ret_24h", "ret_7d", "ret_28d", "rv24", "rv_7d", "max_7d", "dv24", "taker_1h",
            "taker_24h", "vsurge"]


def ml_trades(P: pd.DataFrame, pr: dict, fl: float, t0: int, t1: int) -> pd.DataFrame:
    from sklearn.linear_model import Ridge
    Q = P[universe(P, 5e6)].copy()
    g = Q.groupby("ts")
    R = pd.DataFrame({f: g[f].rank(pct=True) for f in ML_FEATS}).fillna(0.5)
    R["y"] = g["fwd24"].rank(pct=True)
    R["ts"], R["code"], R["dv24"] = Q["ts"], Q["code"], Q["dv24"]
    mondays = sorted(t for t in R["ts"].unique() if t % D_ == 0 and ((t // D_) + 3) % 7 == 0 and t0 <= t <= t1)
    out = []
    for m in mondays:
        tr = R[(R["ts"] >= m - (pr["train_days"] + pr["embargo_days"]) * D_) &
               (R["ts"] <= m - pr["embargo_days"] * D_ - D_) & (R["ts"] % (6 * H_) == 0) & R["y"].notna()]
        if len(tr) < 5000:
            continue
        mdl = Ridge(alpha=pr["alpha"]).fit(tr[ML_FEATS].to_numpy(), tr["y"].to_numpy())
        te = R[(R["ts"] >= m) & (R["ts"] < m + 7 * D_) & (R["ts"] % D_ == 0) & (R["dv24"] >= fl) & (R["ts"] <= t1)].copy()
        if te.empty:
            continue
        te["s"] = mdl.predict(te[ML_FEATS].to_numpy())
        out.append(pick(te, "s", pr["n"], True)[["code", "ts", "dv24"]].assign(side=1))
        out.append(pick(te, "s", pr["n"], False)[["code", "ts", "dv24"]].assign(side=-1))
    if not out:
        return pd.DataFrame()
    return pd.concat(out).assign(w=0.05, kind="fixed", hold=1440)


# ------------------------------------------------------------------ execution

def simulate_code(args) -> list[dict]:
    code, kdir, fdir, trades, data_end = args
    d = load_1m(Path(kdir) / f"{code}.parquet")
    if d is None:
        return []
    t0 = int(d.index[0])
    o, h, l_, c = (d[k].to_numpy(np.float64) for k in ("o", "h", "l", "c"))
    n = len(c)
    last_ts = t0 + (n - 1) * 60
    delisted = last_ts < data_end - D_
    fu = load_funding(Path(fdir), code)
    fts, ff = fu["ts"].to_numpy(), fu["f"].to_numpy()
    out = []
    for tr in trades:
        side, kind, hold = int(tr["side"]), tr["kind"], int(tr["hold"])
        reason = "time"
        if kind == "bo":
            ir = (int(tr["ts"]) - t0) // 60
            if ir < 0 or ir >= n:
                continue
            ie = ir + hold
            if ie > n:
                if not delisted:
                    continue
                ie = n
            ref, x = o[ir], float(tr["x"])
            up = np.flatnonzero(h[ir:ie] >= ref * (1 + x))
            dn = np.flatnonzero(l_[ir:ie] <= ref * (1 - x))
            ku = up[0] if len(up) else 10 ** 9
            kd = dn[0] if len(dn) else 10 ** 9
            if ku == kd == 10 ** 9:
                continue
            if ku == kd:
                side, entry, exitp, reason = 1, ref * (1 + x), ref, "both"
                i_in, i_out = ir + ku, ir + ku
            else:
                side = 1 if ku < kd else -1
                k = min(ku, kd)
                entry = ref * (1 + side * x)
                i_in = ir + k
                seg_l, seg_h = l_[i_in + 1:ie], h[i_in + 1:ie]
                hit = np.flatnonzero(seg_l <= ref) if side > 0 else np.flatnonzero(seg_h >= ref)
                if len(hit):
                    i_out = i_in + 1 + hit[0]
                    exitp = min(ref, o[i_out]) if side > 0 else max(ref, o[i_out])
                    reason = "stop"
                else:
                    i_out, exitp = ie - 1, c[ie - 1]
        else:
            i_in = (int(tr["ts"]) + 60 - t0) // 60
            if i_in < 0 or i_in >= n:
                continue
            ie = i_in + hold
            if ie >= n:
                if not delisted:
                    continue
                ie, reason = n - 1, "delisted"
            entry = o[i_in]
            if kind == "fixed":
                i_out, exitp = ie, (o[ie] if reason == "time" else c[ie])
            elif kind == "trail":                        # long only: initial stop, then trail below the peak
                seg_h, seg_l, seg_o = h[i_in:ie], l_[i_in:ie], o[i_in:ie]
                peak_prev = np.maximum.accumulate(np.concatenate([[entry], seg_h[:-1]]))  # highs of earlier bars only
                lvl = np.maximum(entry * (1 - float(tr["stop"])), peak_prev * (1 - float(tr["trail"])))
                hit = np.flatnonzero(seg_l <= lvl)
                if len(hit):
                    k = hit[0]
                    i_out, exitp = i_in + k, min(lvl[k], seg_o[k])
                    reason = "stop" if exitp < entry else "trail"
                else:
                    i_out = ie
                    exitp = o[ie] if reason == "time" else c[ie]
            else:
                stop, tgt = float(tr["stop"]), float(tr["target"])
                sp, tp = entry * (1 - side * stop), entry * (1 + side * tgt)
                seg_h, seg_l, seg_o = h[i_in:ie], l_[i_in:ie], o[i_in:ie]
                if side > 0:
                    hs, ht = np.flatnonzero(seg_l <= sp), np.flatnonzero(seg_h >= tp)
                else:
                    hs, ht = np.flatnonzero(seg_h >= sp), np.flatnonzero(seg_l <= tp)
                ks = hs[0] if len(hs) else 10 ** 9
                kt = ht[0] if len(ht) else 10 ** 9
                if ks <= kt and ks < 10 ** 9:
                    i_out = i_in + ks
                    exitp = min(sp, seg_o[ks]) if side > 0 else max(sp, seg_o[ks])
                    reason = "stop"
                elif kt < 10 ** 9:
                    i_out, exitp, reason = i_in + kt, tp, "target"
                else:
                    i_out = ie
                    exitp = o[ie] if reason == "time" else c[ie]
        if not (np.isfinite(entry) and np.isfinite(exitp)) or entry <= 0:
            continue
        ts_in, ts_out = t0 + i_in * 60, t0 + i_out * 60
        a, b = np.searchsorted(fts, ts_in, side="right"), np.searchsorted(fts, ts_out, side="right")
        fsum = float(ff[a:b].sum())
        gross = side * (exitp / entry - 1)
        cost = 2 * (FEE + float(slip(tr["dv24"])))
        out.append(dict(sid=tr["sid"], code=code, ts=int(tr["ts"]), side=side, w=float(tr["w"]), gross=gross,
                        cost=cost, funding=-side * fsum, net=gross - cost - side * fsum, reason=reason,
                        ts_out=ts_out))
    return out


SPOT = ROOT / "data" / "cache" / "spot1h"
VISION = "https://data.binance.vision/data/spot"


def spot_daily(code: str, t0: int, t1: int) -> pd.Series | None:
    """Binance spot close at each 00:00 UTC (from 1h klines, cached). None if no spot market."""
    import io
    import zipfile
    import requests
    SPOT.mkdir(parents=True, exist_ok=True)
    months = pd.period_range(pd.to_datetime(t0, unit="s"), pd.to_datetime(t1 + 8 * D_, unit="s"), freq="M")
    f = SPOT / f"{code}_{months[0]}_{months[-1]}.parquet"      # cache per period
    if f.exists():
        d = pd.read_parquet(f)
    else:
        rows = []
        s = requests.Session()
        today = pd.Timestamp.utcnow().tz_localize(None)
        for m in months:
            urls = [f"{VISION}/monthly/klines/{code}/1h/{code}-1h-{m}.zip"]
            if m == pd.Period(today, "M"):
                urls = [f"{VISION}/daily/klines/{code}/1h/{code}-1h-{day.date()}.zip"
                        for day in pd.date_range(m.start_time, today - pd.Timedelta(days=1))]
            for u in urls:
                try:
                    r = s.get(u, timeout=60)
                except requests.RequestException:
                    continue
                if r.status_code != 200:
                    continue
                with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                    txt = z.read(z.namelist()[0]).decode()
                for line in txt.splitlines():
                    x = line.split(",")
                    if x and x[0].isdigit():
                        ts = int(x[0])
                        ts = ts // 1000 if ts < 10 ** 14 else ts // 1_000_000   # spot moved to microseconds in 2025
                        rows.append((ts + H_, float(x[4])))          # close of the hour, known at its end
        d = pd.DataFrame(rows, columns=["ts", "c"]).drop_duplicates("ts").sort_values("ts")
        d.to_parquet(f, index=False)
    if d.empty:
        return None
    d = d[d["ts"] % D_ == 0]
    return d.set_index("ts")["c"]


def carry(P: pd.DataFrame, h: dict, fdir: Path, t0: int, t1: int) -> pd.DataFrame:
    """Daily returns of the hedged funding carry: short perp + long Binance spot.
    Includes basis P&L (spot return - perp return); coins without a spot market are not eligible."""
    pr, fl = h["params"], float(h["universe_floor_usd"])
    U = P[universe(P, fl) & (P["ts"] % D_ == 0) & (P["ts"] >= t0) & (P["ts"] <= t1)][["code", "ts", "fund24", "dv24"]]
    cand = U[U["fund24"] >= pr["enter"]]["code"].unique()
    U = U[U["code"].isin(cand)]
    perp = P[(P["ts"] % D_ == 0) & P["code"].isin(cand)].set_index(["code", "ts"])["c"]
    rows = []
    for code, g in U.groupby("code"):
        fu = load_funding(fdir, code)
        if fu.empty:
            continue
        sp = spot_daily(code, t0, t1)
        if sp is None:
            continue
        pc = perp.loc[code]
        basis = sp.reindex(pc.index).pct_change() - pc.pct_change()
        basis = pd.Series(basis.values, index=pc.index // D_ - 1)   # return over day d is known at d+1 00:00
        g = g[g["ts"].isin(sp.index)]
        if g.empty:
            continue
        fts, ff = fu["ts"].to_numpy(), fu["f"].to_numpy()
        on, start, dv = False, None, None
        g = g.sort_values("ts")
        for r in g.itertuples(index=False):
            if not on and r.fund24 >= pr["enter"]:
                on, start, dv = True, r.ts, r.dv24
            elif on and not (r.fund24 >= pr["exit"]):
                rows += spell(code, start, r.ts, dv, fts, ff, pr, basis)
                on = False
        if on:
            rows += spell(code, start, int(g["ts"].iloc[-1]) + D_, dv, fts, ff, pr, basis)
    if not rows:
        return pd.DataFrame(columns=["day", "r"])
    r = pd.DataFrame(rows)
    return r.groupby("day")["r"].mean().rename("r").reset_index()


def spell(code, a, b, dv, fts, ff, pr, basis) -> list[dict]:
    cost = 2 * (FEE + float(slip(dv))) + 2 * (pr["spot_fee"] + float(slip(dv)))
    out = []
    for dd in range(a, b, D_):
        i, j = np.searchsorted(fts, dd, side="right"), np.searchsorted(fts, dd + D_, side="right")
        r = float(ff[i:j].sum())                         # short perp receives positive funding
        bd = basis.get(dd // D_, np.nan)
        if not np.isfinite(bd):
            continue                                     # no spot/perp price that day: not held
        r += float(bd)                                   # long spot - short perp price P&L
        if dd == a:
            r -= cost / 2
        if dd + D_ >= b:
            r -= cost / 2
        out.append(dict(code=code, day=dd // D_, r=r))
    return out


# ------------------------------------------------------------------ statistics

def norm_ppf(p: float) -> float:
    from statistics import NormalDist
    return NormalDist().inv_cdf(p)


def norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2))


def dsr(r: np.ndarray, n_trials: int) -> float:
    r = r[np.isfinite(r)]
    T = len(r)
    if T < 30 or r.std() == 0:
        return np.nan
    sr = r.mean() / r.std()
    g3 = float(((r - r.mean()) ** 3).mean() / r.std() ** 3)
    g4 = float(((r - r.mean()) ** 4).mean() / r.std() ** 4)
    gam = 0.5772156649
    N = max(n_trials, 2)
    sr0 = math.sqrt(1 / T) * ((1 - gam) * norm_ppf(1 - 1 / N) + gam * norm_ppf(1 - 1 / (N * math.e)))
    den = math.sqrt(max(1 - g3 * sr + (g4 - 1) / 4 * sr ** 2, 1e-9))
    return norm_cdf((sr - sr0) * math.sqrt(T - 1) / den)


def boot_ci(r: np.ndarray, block: int = 5, n: int = 2000, seed: int = 1) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    T = len(r)
    if T < 20:
        return np.nan, np.nan
    k = int(np.ceil(T / block))
    means = np.empty(n)
    for i in range(n):
        st = rng.integers(0, T, k)
        idx = (st[:, None] + np.arange(block)[None, :]).ravel()[:T] % T
        means[i] = r[idx].mean()
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def pbo(M: np.ndarray, S: int = 16) -> float:
    T, K = M.shape
    if K < 2 or T < S * 5:
        return np.nan
    blocks = np.array_split(np.arange(T), S)
    lam = []
    for comb in itertools.combinations(range(S), S // 2):
        ins = np.concatenate([blocks[i] for i in comb])
        oos = np.concatenate([blocks[i] for i in range(S) if i not in comb])

        def sh(x):
            s = x.std(axis=0)
            return np.where(s > 0, x.mean(axis=0) / s, 0)
        best = int(np.argmax(sh(M[ins])))
        rk = pd.Series(sh(M[oos])).rank().to_numpy()[best] / (K + 1)
        lam.append(math.log(rk / (1 - rk)))
    return float(np.mean(np.array(lam) < 0))


# ------------------------------------------------------------------ main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", choices=["DEV", "OOT"], required=True)
    ap.add_argument("--force-rerun", action="store_true", help="logged in the ledger; OOT should be run once")
    ap.add_argument("--only", default="", help="comma-separated hypothesis ids (default: all confirmatory + control)")
    a = ap.parse_args()
    reg = yaml.safe_load(REG.read_text())
    excl = set(reg["defaults"]["exclude"])
    hyps = [h for h in reg["hypotheses"] if h["tier"] in ("confirmatory", "control")]
    if a.only:
        keep = set(a.only.split(","))
        hyps = [h for h in hyps if h["id"] in keep]
    led = pd.read_csv(LEDGER) if LEDGER.exists() else pd.DataFrame()
    done = set(led.loc[led["period"] == "OOT", "hypothesis"]) if len(led) else set()
    again = [h["id"] for h in hyps if h["id"] in done]
    if a.period == "OOT" and again and not a.force_rerun:
        print(f"OOT already run for {again}; refusing (use --force-rerun, it will be logged)")
        return 1
    p0, p1 = (int(pd.Timestamp(x).timestamp()) for x in reg["periods"][a.period])
    kdir, fdir = (ROOT / x for x in DIRS[a.period])
    P = build_hourly(a.period, excl)
    log(f"hourly panel {P.shape}, coins {P['code'].nunique()}")
    data_end = int(P["ts"].max())
    t0, t1 = p0 + 30 * D_, min(p1 + D_ - H_, data_end - 8 * D_)
    allt = []
    for h in hyps:
        if h["id"] == "S6_funding_carry":
            continue
        t = trades_for(h, P, t0, t1)
        log(f"{h['id']}: {len(t)} signals")
        allt.append(t)
    T = pd.concat(allt, ignore_index=True)
    jobs = [(code, str(kdir), str(fdir), g.to_dict("records"), data_end) for code, g in T.groupby("code")]
    res = []
    with ProcessPoolExecutor(8) as ex:
        for i, r in enumerate(ex.map(simulate_code, jobs, chunksize=2)):
            res += r
            if i % 100 == 0:
                log(f"simulated {i}/{len(jobs)} coins")
    X = pd.DataFrame(res)
    OUT.mkdir(parents=True, exist_ok=True)
    tag = f"{a.period}{'_' + '_'.join(sorted(h['id'].split('_')[0] for h in hyps)) if a.only else ''}"
    X.to_parquet(OUT / f"trades_{tag}.parquet", index=False)
    days = np.arange(t0 // D_, t1 // D_ + 1)
    daily = pd.DataFrame(index=days)
    for sid, g in X.groupby("sid"):
        daily[sid] = (g["w"] * g["net"]).groupby(g["ts"] // D_).sum().reindex(days).fillna(0.0)
    h6 = next((h for h in hyps if h["id"] == "S6_funding_carry"), None)
    if h6 is not None:
        cr = carry(P, h6, fdir, t0, t1)
        daily["S6_funding_carry"] = cr.set_index("day")["r"].reindex(days).fillna(0.0) if len(cr) else 0.0
    btc = P[(P["code"] == "BTCUSDT") & (P["ts"] % D_ == 0)].set_index("ts")["c"]
    btc30 = (btc / btc.shift(30) - 1)
    btc30.index = btc30.index // D_
    bull = btc30.reindex(days).to_numpy() > 0
    n_led = int(reg.get("prior_trials", 0)) + len(led) + len(hyps)
    conf = [h["id"] for h in hyps if h["tier"] == "confirmatory"]
    rows = []
    for h in hyps:
        sid = h["id"]
        r = daily[sid].to_numpy() if sid in daily else np.zeros(len(days))
        g = X[X["sid"] == sid] if len(X) else pd.DataFrame()
        cum = np.cumsum(r)
        lo, hi = boot_ci(r)
        sh = r.mean() / r.std() * math.sqrt(365) if r.std() > 0 else np.nan
        rows.append(dict(id=sid, tier=h["tier"], trades=int(len(g)) if len(g) else None,
                         net_per_trade=float(g["net"].mean()) if len(g) else np.nan,
                         gross_per_trade=float(g["gross"].mean()) if len(g) else np.nan,
                         hit=float((g["net"] > 0).mean()) if len(g) else np.nan,
                         daily_bps=r.mean() * 1e4, ci_lo_bps=lo * 1e4, ci_hi_bps=hi * 1e4, ann_ret=r.mean() * 365,
                         sharpe=sh, maxdd=float((cum - np.maximum.accumulate(cum)).min()),
                         dsr_set=dsr(r, len(conf)), dsr_ledger=dsr(r, n_led),
                         bull_bps=r[bull].mean() * 1e4, bear_bps=r[~bull].mean() * 1e4))
    S = pd.DataFrame(rows)
    pb = pbo(daily[[c for c in conf if c in daily]].to_numpy())
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    run_id = f"{a.period}-{int(time.time())}"
    newled = pd.DataFrame([dict(ts=now, run_id=run_id, hypothesis=r["id"], tier=r["tier"], period=a.period,
                                sharpe=r["sharpe"], mean_daily_net=r["daily_bps"] / 1e4, n_trades=r["trades"],
                                note="force-rerun" if a.force_rerun else "") for r in rows])
    pd.concat([led, newled], ignore_index=True).to_csv(LEDGER, index=False)
    S.to_csv(OUT / f"summary_{tag}.csv", index=False)
    daily.to_csv(OUT / f"daily_{tag}.csv")
    write_report(tag, S, pb, n_led, days, X)
    print((OUT / f"{tag}.md").read_text())
    return 0


def write_report(period, S, pb, n_led, days, X) -> None:
    d0, d1 = pd.to_datetime(days[0] * D_, unit="s").date(), pd.to_datetime(days[-1] * D_, unit="s").date()
    L = [f"# Formal test: {period} ({d0} to {d1}, {len(days)} days)", "",
         "Net of fees, volume-based slippage and actual funding. Entry at the next minute's open. "
         "Daily returns, Sharpe annualised with sqrt(365). CI = 95% block bootstrap of the mean daily return.",
         f"DSR(set) uses N = confirmatory strategies in this run; DSR(ledger) uses N = {n_led} (all tests run so far).",
         f"PBO across the confirmatory set (CSCV, 16 blocks): {pb:.2f}", "",
         "| strategy | tier | trades | gross/trade | net/trade | hit | daily bps | 95% CI | Sharpe | max DD | DSR set | DSR ledger | bull bps | bear bps | verdict |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for _, r in S.iterrows():
        ok = (r["tier"] == "confirmatory" and r["ci_lo_bps"] > 0 and (r["dsr_ledger"] or 0) >= 0.95)
        verdict = "PASS" if ok else ("control" if r["tier"] == "control" else "fail")
        def pct(x):
            return "" if x is None or not np.isfinite(x) else f"{x*100:.2f}%"
        L.append(f"| {r['id']} | {r['tier']} | {r['trades'] if r['trades'] is not None else ''} | "
                 f"{pct(r['gross_per_trade'])} | {pct(r['net_per_trade'])} | {pct(r['hit'])} | {r['daily_bps']:.1f} | "
                 f"[{r['ci_lo_bps']:.1f}, {r['ci_hi_bps']:.1f}] | {r['sharpe']:.2f} | {r['maxdd']*100:.0f}% | "
                 f"{r['dsr_set']:.2f} | {r['dsr_ledger']:.2f} | {r['bull_bps']:.1f} | {r['bear_bps']:.1f} | {verdict} |")
    if len(X):
        L += ["", "## Exit reasons", ""]
        L.append(X.groupby("sid")["reason"].value_counts(normalize=True).unstack().fillna(0).round(2).to_string())
    (OUT / f"{period}.md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    sys.exit(main())
