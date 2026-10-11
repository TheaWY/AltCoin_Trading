"""B37 (2026-10-03): what can be predicted and traded using ONLY Upbit information, executed on Upbit (long-only spot)?

Three parts, every return measured on Upbit KRW prices with Upbit costs (0.05% fee per side + 0.10% assumed spread/slippage
round trip = 0.20% total):
  A. Notice event study: every Upbit announcement in exchange_notices (listing / warning / warning_lifted / delisting / other)
     with a KRW market -> Upbit 1-minute candles (REST, cached) from -60m to +24h. Entry = open of the minute after the
     notice (listings: open of the first traded minute). Raw returns at +5m/+15m/+1h/+4h/+24h, plus the F13 rules replayed:
     listing 60-min hold with -5% stop; confirmation-gated (+3% within 10 min) 4h hold with -3% stop.
  B. Upbit-only burst scan on upbit_1h (291 KRW markets, 2026-03-16..): hourly close >= +5% on >= 3x trailing-24h value
     -> forward 1h/4h/24h. Splits using Upbit-only information: fresh (r7d <= 0) vs extended, KST day vs night, notice in the
     prior 24h, coin's share of Upbit turnover, and Upbit-only coin (no Binance perp) vs cross-listed.
  C. Upbit long-only cross-section (hourly): 24h momentum, 1h reversal, turnover-share change; top decile vs equal-weight
     Upbit universe at 4h and 24h holds.
Short history -> 60/40 time split: first 60% picks, last 40% reports. Output research/b37_upbit_only.md + trial_ledger row.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.data.storage import get_storage  # noqa: E402

CACHE = ROOT / "data/cache/b37"; CACHE.mkdir(parents=True, exist_ok=True)
OUT = ROOT / "research/b37_upbit_only.md"
COST = 0.0020                       # round trip: 2 x 0.05% fee + ~0.10% spread/slippage on KRW alts
KST = timezone(timedelta(hours=9))
S = requests.Session(); S.headers["User-Agent"] = "Mozilla/5.0"


def q(sql):
    st = get_storage()
    with st._connect() as c:  # noqa: SLF001
        cur = c.raw.cursor(); cur.execute(sql); rows = cur.fetchall()
        return pd.DataFrame(rows) if rows and isinstance(rows[0], dict) else pd.DataFrame(rows, columns=[d[0] for d in cur.description])


def ci(x, n=2000, pct=2.5):
    x = np.asarray(x, float); x = x[np.isfinite(x)]
    if len(x) < 5:
        return (np.nan, np.nan)
    rng = np.random.default_rng(7); b = [x[rng.integers(0, len(x), len(x))].mean() for _ in range(n)]
    return (float(np.percentile(b, pct)), float(np.percentile(b, 100 - pct)))


def row(name, x):
    x = np.asarray(x, float); x = x[np.isfinite(x)]
    lo, hi = ci(x)
    return f"| {name} | {len(x)} | {x.mean() * 100:+.2f}% | {np.median(x) * 100:+.2f}% | [{lo * 100:+.2f}, {hi * 100:+.2f}] | {(x > 0).mean() * 100:.0f}% |" if len(x) else f"| {name} | 0 | | | | |"


HDR = "| set | n | mean | median | 95% CI | hit |\n|---|---|---|---|---|---|"


# ---------------------------------------------------------------- A. notices -> Upbit 1m candles
def upbit_1m(market, t0, t1):
    """1-minute candles [t0, t1] (epoch s) from Upbit REST, cached per (market, day-of-t0)."""
    f = CACHE / f"{market}_{int(t0)}.parquet"
    if f.exists():
        return pd.read_parquet(f)
    rows, to = [], datetime.fromtimestamp(t1, tz=timezone.utc)
    for _ in range(12):
        r = S.get("https://api.upbit.com/v1/candles/minutes/1", params={"market": market, "count": 200, "to": to.strftime("%Y-%m-%dT%H:%M:%SZ")}, timeout=10)
        if r.status_code == 429:
            time.sleep(1); continue
        if r.status_code != 200:
            break
        d = r.json()
        if not d:
            break
        rows += d
        to = datetime.strptime(d[-1]["candle_date_time_utc"], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        if to.timestamp() <= t0:
            break
        time.sleep(0.12)
    if not rows:
        return pd.DataFrame()
    d = pd.DataFrame(rows)
    d["ts"] = pd.to_datetime(d["candle_date_time_utc"]).astype("datetime64[ns]").astype("int64") // 10**9
    d = d.rename(columns={"opening_price": "o", "high_price": "h", "low_price": "l", "trade_price": "c", "candle_acc_trade_price": "v"})
    d = d[["ts", "o", "h", "l", "c", "v"]].drop_duplicates("ts").sort_values("ts")
    d = d[(d.ts >= t0 - 60) & (d.ts <= t1)].reset_index(drop=True)
    d.to_parquet(f, index=False)
    return d


def replay(d, t_ref, listing):
    """Returns dict of raw returns and the two F13 rule replays, or None."""
    if listing:
        after = d[(d.ts > t_ref) & (d.v > 0)]
        if after.empty:
            return None
        i0 = after.index[0]; entry = float(d.o[i0]); t_in = int(d.ts[i0])
        out = {"open_lag_min": (t_in - t_ref) / 60}
    else:
        after = d[d.ts > t_ref]
        if after.empty:
            return None
        i0 = after.index[0]; entry = float(d.o[i0]); t_in = int(d.ts[i0]); out = {}
    px = d.set_index("ts")["c"]
    for lab, h in (("5m", 300), ("15m", 900), ("1h", 3600), ("4h", 14400), ("24h", 86400)):
        seg = px[(px.index > t_in) & (px.index <= t_in + h)]
        out[f"r_{lab}"] = float(seg.iloc[-1] / entry - 1 - COST) if len(seg) else np.nan
    # F13 listing rule: 60 min, -5% stop on close
    seg = px[(px.index > t_in) & (px.index <= t_in + 3600)]
    if len(seg):
        stop = seg[seg / entry - 1 <= -0.05]
        out["f13_listing"] = float((stop.iloc[0] if len(stop) else seg.iloc[-1]) / entry - 1 - COST)
    # F13 momentum rule: +3% on close within 10 min of reference -> enter, 4h hold, -3% stop
    ref = entry
    win = px[(px.index > t_ref) & (px.index <= t_ref + 600)]
    hit = win[win / ref - 1 >= 0.03]
    if len(hit):
        e2, t2 = float(hit.iloc[0]), int(hit.index[0])
        seg = px[(px.index > t2) & (px.index <= t2 + 14400)]
        if len(seg):
            stop = seg[seg / e2 - 1 <= -0.03]
            out["f13_mom"] = float((stop.iloc[0] if len(stop) else seg.iloc[-1]) / e2 - 1 - COST)
            out["f13_mom_confirmed"] = 1
    else:
        out["f13_mom_confirmed"] = 0
    return out


def part_a():
    n = q("SELECT notice_id, ts, kind, title, symbols FROM exchange_notices WHERE source='upbit' AND ts > extract(epoch from now()) - 400*86400 ORDER BY ts")
    markets = {m["market"] for m in S.get("https://api.upbit.com/v1/market/all", timeout=10).json()}
    recs = []
    for _, r in n.iterrows():
        syms = r["symbols"] if isinstance(r["symbols"], list) else re.findall(r"[A-Z0-9]{2,12}", str(r["symbols"] or ""))
        syms = [s for s in syms if s not in ("KRW", "BTC", "USDT")] or re.findall(r"\(([A-Z0-9]{2,12})\)", r["title"])
        for sym in dict.fromkeys(syms):
            mk = f"KRW-{sym}"
            if mk not in markets:
                continue
            d = upbit_1m(mk, r["ts"] - 3600, r["ts"] + 86400 + 60)
            if d.empty:
                continue
            o = replay(d, int(r["ts"]), r["kind"] == "listing")
            if o:
                recs.append({"notice_id": r["notice_id"], "ts": r["ts"], "kind": r["kind"], "market": mk, "title": r["title"][:80], **o})
    ev = pd.DataFrame(recs)
    ev.to_parquet(CACHE / "events.parquet", index=False)
    return ev


def report_a(ev):
    L = ["## A. Upbit notices, executed on Upbit (long only, cost 0.20% round trip)", "",
         f"{len(ev)} notice x market events with a KRW market and 1m candles (last 400 days). Entry = open of the minute after the notice "
         "(listings: first traded minute). Returns net of cost.", ""]
    for kind, g in ev.groupby("kind"):
        L += [f"### {kind} (n={len(g)})", "", HDR]
        for lab in ("5m", "15m", "1h", "4h", "24h"):
            L.append(row(f"raw +{lab}", g[f"r_{lab}"]))
        if "f13_listing" in g and kind == "listing":
            L.append(row("F13 listing rule (60m, -5% stop)", g["f13_listing"]))
            L.append(f"| open lag (min) median | {g['open_lag_min'].median():.0f} | | | | |")
        if "f13_mom" in g:
            c = g[g.get("f13_mom_confirmed", 0) == 1]
            L.append(row(f"F13 momentum rule (confirmed {len(c)}/{len(g)})", c["f13_mom"]))
        L.append("")
    # time split for listings and warnings (the two with n)
    for kind in ("listing", "warning"):
        g = ev[ev.kind == kind].sort_values("ts")
        if len(g) < 20:
            continue
        k = int(len(g) * 0.6)
        L += [f"### {kind}: first 60% vs last 40% (time split)", "", HDR,
              row("first 60% +1h", g.iloc[:k]["r_1h"]), row("last 40% +1h", g.iloc[k:]["r_1h"]),
              row("first 60% +24h", g.iloc[:k]["r_24h"]), row("last 40% +24h", g.iloc[k:]["r_24h"]), ""]
    return L


# ---------------------------------------------------------------- B. Upbit-only hourly bursts
def load_1h():
    d = q("SELECT symbol, ts, open AS o, close AS c, value_krw AS v FROM upbit_1h ORDER BY symbol, ts")
    d["base"] = d["symbol"].str.split("/").str[0]
    P = d.pivot(index="ts", columns="base", values="c").sort_index()
    V = d.pivot(index="ts", columns="base", values="v").sort_index().fillna(0.0)
    return P, V


def part_b(P, V):
    try:
        info = S.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=20).json()
        perps = {x["symbol"][:-4] for x in info["symbols"] if x.get("contractType") == "PERPETUAL" and x["status"] == "TRADING"}
    except Exception:  # noqa: BLE001
        perps = set()
    n = q("SELECT ts, symbols, kind FROM exchange_notices WHERE source='upbit'")
    nt = {}
    for _, r in n.iterrows():
        for s in (r["symbols"] if isinstance(r["symbols"], list) else re.findall(r"[A-Z0-9]{2,12}", str(r["symbols"] or ""))):
            nt.setdefault(s, []).append(r["ts"])
    r1 = P / P.shift(1) - 1
    vmean = V.rolling(24).mean().shift(1)
    r7 = P / P.shift(168) - 1
    share = V.div(V.sum(axis=1), axis=0)
    fwd = {h: P.shift(-h) / P - 1 for h in (1, 4, 24)}
    onset = (r1 >= 0.05) & (V >= 3 * vmean) & (vmean > 0)
    recs = []
    for ts, col in zip(*np.where(onset.values)):
        t, b = P.index[ts], P.columns[col]
        hr = datetime.fromtimestamp(int(t), KST).hour
        recs.append({"ts": int(t), "base": b, "r1": r1.iat[ts, col], "r7d": r7.iat[ts, col], "share": share.iat[ts, col],
                     "kst_day": 9 <= hr < 18, "perp": b in perps, "notice_24h": any(0 <= t - x <= 86400 for x in nt.get(b, [])),
                     **{f"f{h}": fwd[h].iat[ts, col] - COST for h in (1, 4, 24)}})
    e = pd.DataFrame(recs).dropna(subset=["f4"])
    e.to_parquet(CACHE / "bursts.parquet", index=False)
    k = e["ts"].quantile(0.6)
    ins, oos = e[e.ts <= k], e[e.ts > k]
    L = ["## B. Upbit-only hourly bursts (close >= +5% on >= 3x trailing-24h turnover), long at the burst close", "",
         f"{len(e)} onsets, {e.base.nunique()} coins, {datetime.fromtimestamp(int(e.ts.min()), KST):%Y-%m-%d}..{datetime.fromtimestamp(int(e.ts.max()), KST):%Y-%m-%d}; "
         f"in-sample = first 60% ({len(ins)}), holdout = last 40% ({len(oos)}). Net of 0.20%.", ""]
    splits = {"all": lambda g: g, "fresh r7d<=0": lambda g: g[g.r7d <= 0], "extended r7d>0": lambda g: g[g.r7d > 0],
              "KST day 09-18": lambda g: g[g.kst_day], "KST night": lambda g: g[~g.kst_day],
              "no Binance perp": lambda g: g[~g.perp], "has Binance perp": lambda g: g[g.perp],
              "notice in prior 24h": lambda g: g[g.notice_24h], "no notice": lambda g: g[~g.notice_24h],
              "turnover share > 1%": lambda g: g[g.share > 0.01], "turnover share <= 1%": lambda g: g[g.share <= 0.01],
              "fresh & no perp": lambda g: g[(g.r7d <= 0) & (~g.perp)], "fresh & no perp & night": lambda g: g[(g.r7d <= 0) & (~g.perp) & (~g.kst_day)]}
    for h in ("f1", "f4", "f24"):
        L += [f"### forward {h[1:]}h", "", "| split | n in | mean in | CI in | n out | mean out | CI out | hit out |", "|---|---|---|---|---|---|---|---|"]
        for name, fn in splits.items():
            a, b = fn(ins)[h].dropna(), fn(oos)[h].dropna()
            la, ha = ci(a); lb, hb = ci(b)
            L.append(f"| {name} | {len(a)} | {a.mean() * 100:+.2f}% | [{la * 100:+.2f},{ha * 100:+.2f}] | {len(b)} | {b.mean() * 100:+.2f}% | [{lb * 100:+.2f},{hb * 100:+.2f}] | {(b > 0).mean() * 100:.0f}% |" if len(a) and len(b) else f"| {name} | {len(a)} | | | {len(b)} | | | |")
        L.append("")
    return L


# ---------------------------------------------------------------- C. Upbit long-only cross-section
def part_c(P, V):
    r24, r1 = P / P.shift(24) - 1, P / P.shift(1) - 1
    share = V.div(V.sum(axis=1), axis=0)
    sh_chg = share.rolling(24).mean() / share.rolling(168).mean() - 1
    sigs = {"mom_24h (+)": r24, "rev_1h (-)": -r1, "turnover_share_chg (+)": sh_chg, "rev_24h (-)": -r24}
    k = P.index[int(len(P.index) * 0.6)]
    L = ["## C. Upbit long-only cross-section: top decile by signal vs equal-weight Upbit universe (hourly rebalanced, net 0.20%/trade)", "",
         "| signal | hold | in-sample mean/trade | in t | holdout mean/trade | out t | out hit |", "|---|---|---|---|---|---|---|"]
    for name, s in sigs.items():
        for h in (4, 24):
            fwd = P.shift(-h) / P - 1
            rows = []
            for t in P.index[::h]:
                x = s.loc[t].dropna(); f = fwd.loc[t]
                liq = V.loc[t] > 1e8                   # >= 100M KRW in the hour: tradable
                x = x[liq.reindex(x.index).fillna(False)]
                if len(x) < 30:
                    continue
                top = x[x >= x.quantile(0.9)].index
                rows.append((t, f[top].mean() - f[x.index].mean() - COST))
            d = pd.DataFrame(rows, columns=["t", "x"]).dropna()
            a, b = d[d.t <= k]["x"], d[d.t > k]["x"]
            ta = a.mean() / a.std() * np.sqrt(len(a)) if len(a) > 2 else np.nan
            tb = b.mean() / b.std() * np.sqrt(len(b)) if len(b) > 2 else np.nan
            L.append(f"| {name} | {h}h | {a.mean() * 100:+.3f}% | {ta:+.1f} | {b.mean() * 100:+.3f}% | {tb:+.1f} | {(b > 0).mean() * 100:.0f}% |")
    return L + [""]


def main():
    t0 = time.time()
    L = [f"# B37 Upbit-only information study ({datetime.now(KST):%Y-%m-%d %H:%M} KST)", "",
         "Question: using nothing but what Upbit itself publishes (its notices, its KRW prices and turnover), is there a long-only Upbit "
         "spot strategy that makes money after Upbit costs? Every number is Upbit-executed. Short history -> 60/40 time split, holdout decides.", ""]
    ev = part_a(); L += report_a(ev)
    P, V = load_1h()
    L += part_b(P, V)
    L += part_c(P, V)
    L += [f"_runtime {time.time() - t0:.0f}s; caches in data/cache/b37/_"]
    OUT.write_text("\n".join(L))
    print(OUT, f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
