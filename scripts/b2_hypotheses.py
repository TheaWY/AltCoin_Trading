"""Batch B2: ~100 pre-registered event-sliced hypotheses over 2025-03..2026-09 (all coins, delisted included).

  .venv/bin/python -W ignore scripts/b2_hypotheses.py --register   # writes research/batch_B2.yaml (commit it first)
  .venv/bin/python -W ignore scripts/b2_hypotheses.py --run        # runs every registered rule hypothesis once

Every hypothesis slices ALL occurrences of an event across ALL coins (e.g. every +10% hour on every perp),
enters at the next minute's open after the signal hour closes, holds a fixed time, pays fees + volume-based
slippage + actual funding. One trade per coin per hypothesis per 24h. Stats: per-trade mean, day-clustered t,
Benjamini-Hochberg FDR over the whole batch, and the 2025 vs 2026 halves.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from b2_panel import OUT as PANEL, load_funding, load_minutes  # noqa: E402

REG = ROOT / "research" / "batch_B2.yaml"
LEDGER = ROOT / "research" / "trial_ledger.csv"
OUTD = ROOT / "data" / "reports" / "b2"
H_, D_ = 3600, 86400
FEE = 0.0005
import os
T0 = int(pd.Timestamp("2024-03-08" if os.environ.get("B2_ERA") == "2024" else "2025-03-08").timestamp())
T1 = int(pd.Timestamp("2025-02-26" if os.environ.get("B2_ERA") == "2024" else "2026-09-22").timestamp())
MID = int(pd.Timestamp("2026-01-01").timestamp())


def slip(dv):
    dv = np.asarray(dv, float)
    return np.select([dv > 1e8, dv > 2e7, dv > 5e6], [0.0002, 0.0005, 0.0010], 0.0020)


# ------------------------------------------------------------------ hypothesis families
# (id, trigger description, mechanism, reference, sides) ; each x holds 1h/4h/24h
FAMILIES = [
    ("P1", "1h return >= +10%", "pump: attention/overreaction vs momentum",
     "Kamps & Kleinberg 2018; La Morgia et al. 2020; Caporale & Plastun 2019", ("L", "S")),
    ("P4", "4h return >= +20%", "multi-hour pump", "same as P1", ("L", "S")),
    ("P24", "24h return >= +30%", "daily pump; short-term reversal vs daily momentum",
     "Liu & Tsyvinski 2021 (momentum); Dobrynskaya 2023 (reversal)", ("L", "S")),
    ("D1", "1h return <= -10%", "dump: liquidation overshoot rebound vs continuation",
     "Caporale & Plastun 2019 overreaction", ("L", "S")),
    ("D24", "24h return <= -25%", "daily crash", "overreaction / capitulation literature", ("L", "S")),
    ("BH", "close breaks prior 30d closing high and 24h return > +5%", "trend breakout (Donchian)",
     "Hudson & Urquhart 2021 technical rules in crypto", ("L", "S")),
    ("BL", "close breaks prior 30d closing low and 24h return < -5%", "breakdown", "Hudson & Urquhart 2021", ("L", "S")),
    ("VS", "hourly volume >= 5x its 7d average with |1h return| < 2%", "high-volume return premium / accumulation",
     "Gervais, Kaniel & Mingelgrin 2001", ("L", "S")),
    ("SQ", "volatility compression (24h vol <= 0.6 x 7d vol) then a >= 3% 1h move; trade in the move direction (L=up move, S=down move)",
     "squeeze breakout", "practitioner (Bollinger squeeze)", ("L", "S")),
    ("BT", "BTC 1h >= +2% (L: 5 liquid laggards) or <= -2% (S: 5 liquid laggards)", "BTC leads alts: price-delay catch-up",
     "Hou & Moskowitz 2005 price delay; crypto BTC lead-lag studies", ("L", "S")),
    ("FU", "trailing 24h funding (8h-normalised) >= +0.10% (S) or <= -0.10% (L)", "crowding: extreme funding reverses",
     "He, Manela, Ross & von Wachter 2022 perpetual futures", ("L", "S")),
    ("TK", "taker-buy share of the hour >= 65% (L) or <= 35% (S) with volume >= 3x", "order-flow imbalance persists",
     "Chordia & Subrahmanyam 2004 order imbalance", ("L", "S")),
    ("NL", "new perp listing, 24 hours after the first trade", "new listings: hedging supply vs hype",
     "IPO/listing underperformance analogues", ("L", "S")),
    ("RV", "two consecutive hours each >= +5%", "persistent buying pressure vs exhaustion", "momentum/overreaction", ("L", "S")),
    ("BR", "alt-season burst: >= 15 liquid coins up >= 5% in the same hour; L = 5 liquid laggards", "rotation catch-up",
     "lead-lag / attention spillover", ("L",)),
]
HOLDS = {"1h": 60, "4h": 240, "24h": 1440}
SLICES = [("liq", "trailing 24h volume >= $20M"), ("illiq", "trailing 24h volume $2-5M"),
          ("first", "no +10% hour in the prior 30 days"), ("repeat", ">= 1 prior +10% hour in 30 days"),
          ("btcup", "BTC 24h return > 0"), ("btcdn", "BTC 24h return <= 0"), ("fundneg", "funding < 0 (shorts crowded)"),
          ("vs10", "hour volume >= 10x the 7d average")]


def registry() -> list[dict]:
    hs = []
    for fid, trig, mech, ref, sides in FAMILIES:
        for side in sides:
            for hn, hm in HOLDS.items():
                hs.append(dict(id=f"B2_{fid}_{side}_{hn}", family=fid, side=side, hold_min=hm, trigger=trig,
                               mechanism=mech, reference=ref, tier="confirmatory"))
    for sl, desc in SLICES:
        for side in ("L", "S"):
            hs.append(dict(id=f"B2_P1_{side}_4h_{sl}", family="P1", side=side, hold_min=240, slice=sl,
                           trigger=f"1h return >= +10% and {desc}", mechanism="which pumps continue vs fade",
                           reference="La Morgia et al. 2023; Kamps & Kleinberg 2018", tier="confirmatory"))
    return hs


# ------------------------------------------------------------------ events

def cooldown(e: pd.DataFrame, gap: int = D_) -> pd.DataFrame:
    e = e.sort_values(["code", "ts"])
    keep = np.zeros(len(e), bool)
    last_code, last_ts = None, -10 ** 12
    for i, (c, t) in enumerate(zip(e["code"].to_numpy(), e["ts"].to_numpy())):
        if c != last_code:
            last_code, last_ts = c, -10 ** 12
        if t - last_ts >= gap:
            keep[i] = True
            last_ts = t
    return e[keep]


def events(P: pd.DataFrame, fid: str, side: str) -> pd.DataFrame:
    U = P[(P["dv24"] >= 2e6) & (P["age_h"] >= 72)]
    if fid == "P1":
        e = U[U["ret_1h"] >= 0.10]
    elif fid == "P4":
        e = U[U["ret_4h"] >= 0.20]
    elif fid == "P24":
        e = U[U["ret_24h"] >= 0.30]
    elif fid == "D1":
        e = U[U["ret_1h"] <= -0.10]
    elif fid == "D24":
        e = U[U["ret_24h"] <= -0.25]
    elif fid == "BH":
        e = U[(U["dhi30"] > 0) & (U["ret_24h"] > 0.05)]
    elif fid == "BL":
        e = U[(U["dlo30"] < 0) & (U["ret_24h"] < -0.05)]
    elif fid == "VS":
        e = U[(U["vsurge"] >= 5) & (U["ret_1h"].abs() < 0.02)]
    elif fid == "SQ":
        sq = U[(U["rv24"] <= 0.6 * U["rv_7d"])]
        e = sq[sq["ret_1h"] >= 0.03] if side == "L" else sq[sq["ret_1h"] <= -0.03]
    elif fid == "BT":
        L = U[(U["dv24"] >= 2e7) & (U["code"] != "BTCUSDT")]
        if side == "L":
            L = L[(L["btc_ret_1h"] >= 0.02) & (L["ret_1h"] < 0.5 * L["btc_ret_1h"])]
            e = L.sort_values(["ts", "ret_1h"]).groupby("ts").head(5)
        else:
            L = L[(L["btc_ret_1h"] <= -0.02) & (L["ret_1h"] > 0.5 * L["btc_ret_1h"])]
            e = L.sort_values(["ts", "ret_1h"], ascending=[True, False]).groupby("ts").head(5)
    elif fid == "FU":
        e = U[U["fund24"] <= -0.001] if side == "L" else U[U["fund24"] >= 0.001]
    elif fid == "TK":
        e = U[(U["taker_1h"] >= 0.65) & (U["vsurge"] >= 3)] if side == "L" else U[(U["taker_1h"] <= 0.35) & (U["vsurge"] >= 3)]
    elif fid == "NL":
        Q = P[(P["listed_in_window"] == 1) & (P["age_h"] == 24) & (P["dv24"] >= 2e6)]
        e = Q
    elif fid == "RV":
        e = U[(U["ret_1h"] >= 0.05) & (U["ret_prev1h"] >= 0.05)]
    elif fid == "BR":
        L = U[(U["breadth_pump"] >= 15) & (U["dv24"] >= 2e7)]
        e = L.sort_values(["ts", "ret_1h"]).groupby("ts").head(5)
    else:
        raise ValueError(fid)
    e = e[(e["ts"] >= T0) & (e["ts"] <= T1)]
    return e


def slice_mask(e: pd.DataFrame, sl: str) -> pd.Series:
    return {"liq": e["dv24"] >= 2e7, "illiq": e["dv24"] < 5e6, "first": e["pumps_30d"] == 0,
            "repeat": e["pumps_30d"] >= 1, "btcup": e["btc_ret_24h"] > 0, "btcdn": e["btc_ret_24h"] <= 0,
            "fundneg": e["fund24"] < 0, "vs10": e["vsurge"] >= 10}[sl]


# ------------------------------------------------------------------ execution

def sim_code(args) -> list[dict]:
    code, trades, data_end = args
    d = load_minutes(code)
    if d is None:
        return []
    t0 = int(d.index[0])
    o, c = d["o"].to_numpy(np.float64), d["c"].to_numpy(np.float64)
    n = len(c)
    delisted = t0 + (n - 1) * 60 < data_end - 2 * D_
    fu = load_funding(code)
    fts, ff = fu["ts"].to_numpy(), fu["f"].to_numpy()
    out = []
    for tr in trades:
        i = (int(tr["ts"]) + 60 - t0) // 60
        if i < 0 or i >= n:
            continue
        j = i + int(tr["hold"])
        if j >= n:
            if not delisted:
                continue
            j, px1 = n - 1, c[n - 1]
        else:
            px1 = o[j]
        px0 = o[i]
        if not (px0 > 0 and np.isfinite(px1)):
            continue
        side = 1 if tr["side"] == "L" else -1
        a, b = np.searchsorted(fts, t0 + i * 60, side="right"), np.searchsorted(fts, t0 + j * 60, side="right")
        fsum = float(ff[a:b].sum())
        g = side * (px1 / px0 - 1)
        cost = 2 * (FEE + float(slip(tr["dv24"])))
        out.append(dict(hid=tr["hid"], code=code, ts=int(tr["ts"]), gross=g, net=g - cost - side * fsum))
    return out


def run() -> int:
    hs = yaml.safe_load(REG.read_text())["hypotheses"]
    rule = [h for h in hs if h["id"].startswith("B2_")]
    led = pd.read_csv(LEDGER)
    if led["hypothesis"].astype(str).str.startswith("B2_").any():
        print("B2 already run; refusing")
        return 1
    P = pd.read_parquet(PANEL)
    data_end = int(P["ts"].max())
    cache: dict[tuple, pd.DataFrame] = {}
    rows = []
    for h in rule:
        key = (h["family"], h["side"])
        if key not in cache:
            cache[key] = events(P, *key)
        e = cache[key]
        if "slice" in h:
            e = e[slice_mask(e, h["slice"])]
        e = cooldown(e)
        rows.append(e[["code", "ts", "dv24"]].assign(hid=h["id"], side=h["side"], hold=h["hold_min"]))
        print(h["id"], len(e), flush=True)
    T = pd.concat(rows, ignore_index=True)
    jobs = [(code, g.to_dict("records"), data_end) for code, g in T.groupby("code")]
    res = []
    with ProcessPoolExecutor(8) as ex:
        for i, r in enumerate(ex.map(sim_code, jobs, chunksize=2)):
            res += r
            if i % 100 == 0:
                print("sim", i, len(jobs), flush=True)
    X = pd.DataFrame(res)
    OUTD.mkdir(parents=True, exist_ok=True)
    X.to_parquet(OUTD / "trades.parquet", index=False)
    summ = []
    for h in rule:
        g = X[X["hid"] == h["id"]]
        s = dict(id=h["id"], family=h["family"], side=h["side"], hold=h["hold_min"], slice=h.get("slice", ""),
                 n=len(g))
        if len(g) >= 10:
            dly = g.groupby(g["ts"] // D_)["net"].mean()
            s.update(mean=g["net"].mean(), gross=g["gross"].mean(), median=g["net"].median(), hit=(g["net"] > 0).mean(),
                     days=len(dly), t=dly.mean() / (dly.std(ddof=1) / math.sqrt(len(dly))) if len(dly) > 2 else np.nan,
                     m2025=g.loc[g["ts"] < MID, "net"].mean(), m2026=g.loc[g["ts"] >= MID, "net"].mean(),
                     n2025=int((g["ts"] < MID).sum()), n2026=int((g["ts"] >= MID).sum()))
        summ.append(s)
    S = pd.DataFrame(summ)
    S["p"] = S["t"].map(lambda t: math.erfc(abs(t) / math.sqrt(2)) if np.isfinite(t) else np.nan)
    ok = S["p"].notna()
    ps = S.loc[ok, "p"].to_numpy()
    o = np.argsort(ps)
    q = ps[o] * len(ps) / (np.arange(len(ps)) + 1)
    q = np.minimum.accumulate(q[::-1])[::-1]
    qq = np.empty_like(ps)
    qq[o] = np.minimum(q, 1)
    S.loc[ok, "q"] = qq
    S["pass"] = (S["q"] < 0.05) & (S["mean"] > 0) & (S["m2025"] > 0) & (S["m2026"] > 0)
    S.to_csv(OUTD / "summary.csv", index=False)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    add = pd.DataFrame([dict(ts=now, run_id=f"B2-{int(time.time())}", hypothesis=r["id"], tier="confirmatory",
                             period="2025-03..2026-09", mean_daily_net=r.get("mean"), n_trades=r["n"],
                             note="per-trade mean") for r in summ])
    pd.concat([led, add], ignore_index=True).to_csv(LEDGER, index=False)
    print(S.sort_values("t", ascending=False).head(25).to_string())
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--register", action="store_true")
    ap.add_argument("--run", action="store_true")
    a = ap.parse_args()
    if a.register:
        hs = registry()
        doc = dict(batch="B2", registered="2026-09-27", period=["2025-03-08", "2026-09-22"],
                   execution="next-minute open after the signal hour; fixed hold; fees 0.05%/side + volume slippage + actual funding; 1 trade per coin per hypothesis per 24h; universe 24h volume >= $2M, >= 3 days listed, no stock/commodity perps",
                   pass_rule="BH-FDR q < 0.05 over the batch AND mean net > 0 AND positive in both 2025 and 2026",
                   hypotheses=hs)
        REG.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=140))
        print(len(hs), "hypotheses registered")
        return 0
    if a.run:
        return run()
    return 1


if __name__ == "__main__":
    sys.exit(main())
