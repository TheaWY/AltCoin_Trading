"""Registered event tests E1 (Upbit KRW listing speed) and E2 (Binance futures delisting squeeze).

Uses Binance USDT-perp 1m klines from data/cache/k1m_oot (2025-03..2026-02) + data/cache/k1m (2026-03..),
Upbit announcements (first_listed_at, fetched fresh) and the exchange_notices table for Binance.
Out: data/reports/formal/events_E1_E2.md, events_E1.csv, events_E2.csv; appends the trial ledger.
"""

from __future__ import annotations

import re
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from formal_test import FEE, slip  # noqa: E402

K = [ROOT / "data/cache/k1m_oot", ROOT / "data/cache/k1m"]
F = [ROOT / "data/cache/funding_oot", ROOT / "data/cache/funding"]
OUT = ROOT / "data" / "reports" / "formal"
LEDGER = ROOT / "research" / "trial_ledger.csv"
START = int(pd.Timestamp("2025-03-01").timestamp())
MID = int(pd.Timestamp("2026-01-01").timestamp())
_cache: dict[str, pd.DataFrame | None] = {}


def minutes(code: str) -> pd.DataFrame | None:
    if code in _cache:
        return _cache[code]
    parts = [pd.read_parquet(d / f"{code}.parquet") for d in K if (d / f"{code}.parquet").exists()]
    if not parts:
        _cache[code] = None
        return None
    d = pd.concat(parts).drop_duplicates("ts").sort_values("ts").set_index("ts")
    _cache[code] = d
    return d


def funding(code: str) -> pd.DataFrame:
    parts = [pd.read_parquet(d / f"{code}.parquet", columns=["ts", "f"]) for d in F if (d / f"{code}.parquet").exists()]
    return pd.concat(parts).drop_duplicates("ts").sort_values("ts") if parts else pd.DataFrame({"ts": [], "f": []})


def perp_code(sym: str, at: int) -> str | None:
    for c in (f"{sym}USDT", f"1000{sym}USDT", f"1000000{sym}USDT"):
        d = minutes(c)
        if d is not None and d.index[0] <= at - 3600 and d.index[-1] >= at + 3600:
            return c
    return None


def px(d: pd.DataFrame, ts: int, col: str = "o") -> float:
    v = d[col].get(ts)
    return float(v) if v is not None and np.isfinite(v) else np.nan


def dv24(d: pd.DataFrame, ts: int) -> float:
    return float(d["qv"].loc[ts - 86400:ts - 60].sum())


def boot(x: np.ndarray, n: int = 5000) -> tuple[float, float]:
    x = x[np.isfinite(x)]
    if len(x) < 5:
        return np.nan, np.nan
    rng = np.random.default_rng(1)
    m = rng.choice(x, (n, len(x))).mean(axis=1)
    return float(np.quantile(m, 0.025)), float(np.quantile(m, 0.975))


# ------------------------------------------------------------------ E1

def upbit_notices() -> pd.DataFrame:
    s = requests.Session()
    rows, page = [], 1
    while page < 200:
        r = s.get("https://api-manager.upbit.com/api/v1/announcements",
                  params={"os": "web", "page": page, "per_page": 20, "category": "trade"}, timeout=30)
        ns = r.json().get("data", {}).get("notices", [])
        if not ns:
            break
        for n in ns:
            t = int(datetime.fromisoformat(n.get("first_listed_at") or n["listed_at"]).timestamp())
            rows.append((n["id"], t, n["title"]))
        if min(x[1] for x in rows[-len(ns):]) < START - 30 * 86400:
            break
        page += 1
        time.sleep(0.3)
    return pd.DataFrame(rows, columns=["id", "ts", "title"]).drop_duplicates("id")


def e1() -> pd.DataFrame:
    n = upbit_notices()
    n = n[(n["ts"] >= START) & n["title"].str.contains("신규 거래지원|디지털 자산 추가") & n["title"].str.contains("KRW")
          & ~n["title"].str.contains("취소")]
    out = []
    for r in n.itertuples(index=False):
        syms = [x for x in re.findall(r"[A-Z][A-Z0-9]{1,11}", r.title) if x not in ("KRW", "BTC", "USDT")]
        for sym in dict.fromkeys(syms):
            code = perp_code(sym, r.ts)
            if code is None:
                continue
            d = minutes(code)
            m0 = r.ts // 60 * 60
            e = m0 + 60                                   # next full minute: 0-60 s after the notice
            p_notice, p_in = px(d, m0), px(d, e)
            row = dict(id=r.id, ts=r.ts, sym=sym, code=code, title=r.title, pre60=p_notice / px(d, m0 - 3600) - 1,
                       jump_in_notice_minute=p_in / p_notice - 1, dv24=dv24(d, m0))
            cost = 2 * (FEE + float(slip(row["dv24"])))
            for h in (5, 15, 60, 240):
                g = px(d, e + h * 60) / p_in - 1
                row[f"gross_{h}"] = g
                row[f"net_{h}"] = g - cost
                row[f"ideal_{h}"] = px(d, e + h * 60) / p_notice - 1   # impossible instant fill, for reference
            row["cost"] = cost
            out.append(row)
    return pd.DataFrame(out)


# ------------------------------------------------------------------ E2

def e2() -> pd.DataFrame:
    from src.data.storage import get_storage
    st = get_storage()
    with st._connect() as c:  # noqa: SLF001
        cur = c.raw.cursor()
        cur.execute("SELECT notice_id, ts, title FROM exchange_notices WHERE source='binance' "
                    "AND title ILIKE 'Binance Futures Will Delist%%' AND ts >= %s ORDER BY ts", (START,))
        n = pd.DataFrame(cur.fetchall(), columns=["id", "ts", "title"])
    last = {}
    for d in K:
        for p in d.glob("*USDT.parquet"):
            t = pd.read_parquet(p, columns=["ts", "n"])
            t = t.loc[t["n"] > 0, "ts"]          # vision keeps writing flat zero-volume bars after a delisting
            if len(t):
                last[p.stem] = max(last.get(p.stem, 0), int(t.iloc[-1]))
    data_end = max(last.values())
    ended = {c: t for c, t in last.items() if t < data_end - 2 * 86400}
    out = []
    for r in n.itertuples(index=False):
        dates = set(re.findall(r"\d{4}-\d{2}-\d{2}", r.title))
        named = set(re.findall(r"([A-Z0-9]{2,20}USDT)", r.title))
        for code, lt in ended.items():
            ld = pd.to_datetime(lt, unit="s").strftime("%Y-%m-%d")
            if ld not in dates or not (r.ts < lt < r.ts + 14 * 86400):
                continue
            if named and code not in named:
                continue
            d = minutes(code)
            e = r.ts // 60 * 60 + 60
            x = (lt // 60 * 60) - 3600
            p_in, p_out = px(d, e), px(d, x)
            if not (np.isfinite(p_in) and np.isfinite(p_out)):
                continue
            seg = d.loc[e:x]
            fu = funding(code)
            fsum = float(fu[(fu["ts"] > e) & (fu["ts"] <= x)]["f"].sum())
            dv = dv24(d, e)
            cost = 2 * (FEE + float(slip(dv)))
            g = p_out / p_in - 1
            out.append(dict(id=r.id, ts=r.ts, code=code, hold_h=(x - e) / 3600, gross=g, funding=-fsum, cost=cost,
                            net=g - cost - fsum, max_up=float(seg["h"].max() / p_in - 1),
                            max_dn=float(seg["l"].min() / p_in - 1), dv24=dv, title=r.title[:90]))
    return pd.DataFrame(out).drop_duplicates(["code"])


# ------------------------------------------------------------------ report

def summ(x: pd.Series, ts: pd.Series) -> dict:
    x = x.astype(float)
    lo, hi = boot(x.to_numpy())
    a, b = x[ts < MID], x[ts >= MID]
    return dict(n=int(x.notna().sum()), mean=x.mean(), median=x.median(), hit=(x > 0).mean(), lo=lo, hi=hi,
                m2025=a.mean(), n2025=int(a.notna().sum()), m2026=b.mean(), n2026=int(b.notna().sum()))


def pct(v):
    return "" if v is None or not np.isfinite(v) else f"{v*100:+.2f}%"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    E1 = e1()
    E1.to_csv(OUT / "events_E1.csv", index=False)
    E2 = e2()
    E2.to_csv(OUT / "events_E2.csv", index=False)
    L = ["# Event edges: E1 Upbit KRW listing speed, E2 Binance futures delisting squeeze", "",
         "Binance USDT perps, 1-minute bars, 2025-03 to 2026-09. Entry at the open of the next full minute after the "
         "notice (0-60 s delay). Net = after fees and volume-based slippage (E2 also funding). CI = 95% bootstrap of the mean.", "",
         "## E1 Upbit KRW listing notices", "",
         f"{len(E1)} events. Registered exit: 60 minutes.", "",
         "| hold | n | mean net | median | hit | 95% CI | 2025 | 2026 |", "|---|---|---|---|---|---|---|---|"]
    rows = []
    if len(E1):
        for h in (5, 15, 60, 240):
            s = summ(E1[f"net_{h}"], E1["ts"])
            L.append(f"| {h}m{' (registered)' if h == 60 else ''} | {s['n']} | {pct(s['mean'])} | {pct(s['median'])} | "
                     f"{s['hit']:.0%} | [{pct(s['lo'])}, {pct(s['hi'])}] | {pct(s['m2025'])} (n={s['n2025']}) | "
                     f"{pct(s['m2026'])} (n={s['n2026']}) |")
            if h == 60:
                rows.append(("E1_upbit_krw_listing_speed", s))
        L += ["", f"- Move inside the notice minute (what the fastest bots take before a 0-60 s entry): mean "
                  f"{pct(E1['jump_in_notice_minute'].mean())}, median {pct(E1['jump_in_notice_minute'].median())}",
              f"- Impossible instant fill at the notice-minute open, 60m: mean {pct(E1['ideal_60'].mean())}",
              f"- 60 minutes before the notice (leak check): mean {pct(E1['pre60'].mean())}", "",
              "Largest events:", "",
              E1.sort_values("jump_in_notice_minute", ascending=False).head(10)[
                  ["sym", "code", "jump_in_notice_minute", "gross_5", "gross_60", "gross_240"]].assign(
                  t=lambda x: pd.to_datetime(E1.loc[x.index, "ts"], unit="s")).round(4).to_string(index=False)]
    L += ["", "## E2 Binance futures delisting notices", "", f"{len(E2)} delisted perps matched to notices.", ""]
    if len(E2):
        s = summ(E2["net"], E2["ts"])
        rows.append(("E2_binance_futures_delist_squeeze", s))
        L += ["| n | mean net | median | hit | 95% CI | 2025 | 2026 | mean hold | mean max run-up | mean max drawdown |",
              "|---|---|---|---|---|---|---|---|---|---|",
              f"| {s['n']} | {pct(s['mean'])} | {pct(s['median'])} | {s['hit']:.0%} | [{pct(s['lo'])}, {pct(s['hi'])}] | "
              f"{pct(s['m2025'])} (n={s['n2025']}) | {pct(s['m2026'])} (n={s['n2026']}) | {E2['hold_h'].mean():.0f}h | "
              f"{pct(E2['max_up'].mean())} | {pct(E2['max_dn'].mean())} |", "",
              f"- Funding paid by the long on average: {pct(-E2['funding'].mean())}", "",
              E2.sort_values("net", ascending=False)[["code", "hold_h", "gross", "funding", "net", "max_up", "max_dn"]]
              .round(3).to_string(index=False)]
    (OUT / "events_E1_E2.md").write_text("\n".join(L) + "\n")
    led = pd.read_csv(LEDGER)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    add = pd.DataFrame([dict(ts=now, run_id=f"EVT-{int(time.time())}", hypothesis=k, tier="confirmatory",
                             period="2025-03..2026-09", sharpe=np.nan, mean_daily_net=s["mean"], n_trades=s["n"],
                             note="per-trade mean") for k, s in rows])
    pd.concat([led, add], ignore_index=True).to_csv(LEDGER, index=False)
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
