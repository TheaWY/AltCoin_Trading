"""B2 notice hypotheses N1..N5 (registered in research/batch_B2.yaml).

N1 Upbit caution designation -> short Binance perp at first trade >= notice+2s, exit +60m (aggTrades)
N2 Upbit delisting notice    -> short, same execution
N3 Binance "Will List" spot notice for a coin with a live perp -> long, same execution
N4 new Binance perp          -> short at the next minute 1h after the first trade, hold 72h (1m bars)
N5 Upbit caution lifted      -> long, same as N1
Out: data/reports/b2/notices.csv/md; ledger rows.
"""

from __future__ import annotations

import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from b2_panel import OUT as PANEL, load_funding, load_minutes  # noqa: E402
from b2_hypotheses import FEE, slip, MID  # noqa: E402
from listing_latency import trades  # noqa: E402
from event_edges import boot  # noqa: E402

OUTD = ROOT / "data" / "reports" / "b2"
LEDGER = ROOT / "research" / "trial_ledger.csv"
START = int(pd.Timestamp("2025-03-01").timestamp())
END = int(pd.Timestamp("2026-09-23").timestamp())


def upbit_all() -> pd.DataFrame:
    s = requests.Session()
    rows, page = [], 1
    while page < 400:
        r = s.get("https://api-manager.upbit.com/api/v1/announcements",
                  params={"os": "web", "page": page, "per_page": 20, "category": "trade"}, timeout=30)
        ns = r.json().get("data", {}).get("notices", [])
        if not ns:
            break
        for n in ns:
            rows.append((n["id"], int(datetime.fromisoformat(n.get("first_listed_at") or n["listed_at"]).timestamp()),
                         n["title"]))
        if min(x[1] for x in rows[-len(ns):]) < START - 5 * 86400:
            break
        page += 1
        time.sleep(0.3)
    return pd.DataFrame(rows, columns=["id", "ts", "title"]).drop_duplicates("id")


def syms(title: str) -> list[str]:
    return list(dict.fromkeys(x for x in re.findall(r"\(([A-Z0-9]{2,12})\)", title) if x not in ("KRW", "BTC", "USDT")))


def perp(sym: str, at: int) -> str | None:
    for c in (f"{sym}USDT", f"1000{sym}USDT"):
        p = [ROOT / "data/cache/k1m_oot" / f"{c}.parquet", ROOT / "data/cache/k1m" / f"{c}.parquet"]
        if any(x.exists() for x in p):
            d = load_minutes(c)
            if d is not None and d.index[0] <= at - 86400 and d.index[-1] >= at + 3700:
                return c
    return None


def second_level(ev: dict) -> dict | None:
    try:
        tr = trades(ev["code"], int(ev["ts"]))
    except Exception:  # noqa: BLE001
        return None
    if tr is None or tr.empty:
        return None
    t_ms = int(ev["ts"]) * 1000
    ms, p = tr["ms"].to_numpy(), tr["p"].to_numpy()
    pre = p[ms < t_ms]
    if len(pre) == 0:
        return None
    i = np.searchsorted(ms, t_ms + 2000)
    if i >= len(ms):
        return None
    j = np.searchsorted(ms, ms[i] + 3_600_000) - 1
    side = ev["side"]
    d = load_minutes(ev["code"])
    dv = float(d["qv"].loc[ev["ts"] - 86400:ev["ts"]].sum()) if d is not None else 0.0
    g = side * (p[j] / p[i] - 1)
    return dict(hid=ev["hid"], code=ev["code"], ts=int(ev["ts"]), move_before=p[i] / pre[-1] - 1,
                gross=g, net=g - 2 * (FEE + float(slip(dv))), title=ev.get("title", "")[:80])


def main() -> int:
    up = upbit_all()
    up = up[(up["ts"] >= START) & (up["ts"] <= END)]
    evs = []
    for r in up.itertuples(index=False):
        t = r.title
        if "유의 종목 지정" in t and "해제" not in t and "연장" not in t:
            hid, side = "B2_N1_upbit_warning_short", -1
        elif "유의 종목 지정 해제" in t:
            hid, side = "B2_N5_upbit_warning_lifted_long", 1
        elif "거래지원 종료" in t and "취소" not in t:
            hid, side = "B2_N2_upbit_delist_short", -1
        else:
            continue
        for s in syms(t):
            c = perp(s, r.ts)
            if c:
                evs.append(dict(hid=hid, side=side, code=c, ts=r.ts, title=t))
    from src.data.storage import get_storage
    with get_storage()._connect() as conn:  # noqa: SLF001
        cur = conn.raw.cursor()
        cur.execute("SELECT ts, title, symbols FROM exchange_notices WHERE source='binance' AND title ILIKE 'Binance Will List%%' "
                    "AND ts BETWEEN %s AND %s", (START, END))
        for r in cur.fetchall():
            for s in syms(r["title"]) or ([r["symbols"]] if r["symbols"] else []):
                c = perp(s, r["ts"])
                if c:
                    evs.append(dict(hid="B2_N3_binance_spot_listing_long", side=1, code=c, ts=r["ts"], title=r["title"]))
    print(pd.Series([e["hid"] for e in evs]).value_counts(), flush=True)
    with ThreadPoolExecutor(6) as ex:
        res = [x for x in ex.map(second_level, evs) if x]
    # N4 new perps, 1m bars
    P = pd.read_parquet(PANEL, columns=["code", "ts", "age_h", "listed_in_window", "dv24"])
    nl = P[(P["listed_in_window"] == 1) & (P["age_h"] == 1)]
    for r in nl.itertuples(index=False):
        d = load_minutes(r.code)
        if d is None:
            continue
        t0 = int(d.index[0])
        i = (int(r.ts) + 60 - t0) // 60
        j = i + 72 * 60
        if j >= len(d):
            continue
        o = d["o"].to_numpy()
        fu = load_funding(r.code)
        fs = float(fu[(fu["ts"] > t0 + i * 60) & (fu["ts"] <= t0 + j * 60)]["f"].sum())
        dv = float(d["qv"].iloc[max(0, i - 1440):i].sum()) * (1440 / max(1, min(i, 1440)))
        g = -(o[j] / o[i] - 1)
        res.append(dict(hid="B2_N4_new_perp_short_72h", code=r.code, ts=int(r.ts), move_before=np.nan, gross=g,
                        net=g - 2 * (FEE + float(slip(dv))) + fs))
    X = pd.DataFrame(res)
    OUTD.mkdir(parents=True, exist_ok=True)
    X.to_csv(OUTD / "notices.csv", index=False)
    L = ["# B2 notice hypotheses", "", "| id | n | move before entry (median) | mean net | median | hit | 95% CI | 2025 | 2026 |",
         "|---|---|---|---|---|---|---|---|---|"]
    led_rows = []
    for hid, g in X.groupby("hid"):
        lo, hi = boot(g["net"].to_numpy())
        a, b = g[g["ts"] < MID]["net"], g[g["ts"] >= MID]["net"]
        L.append(f"| {hid} | {len(g)} | {g['move_before'].median()*100:+.2f}% | {g['net'].mean()*100:+.2f}% | "
                 f"{g['net'].median()*100:+.2f}% | {(g['net']>0).mean():.0%} | [{lo*100:+.2f}%, {hi*100:+.2f}%] | "
                 f"{a.mean()*100:+.2f}% (n={len(a)}) | {b.mean()*100:+.2f}% (n={len(b)}) |")
        led_rows.append(dict(ts=time.strftime("%Y-%m-%dT%H:%M:%S"), run_id=f"B2N-{int(time.time())}", hypothesis=hid,
                             tier="confirmatory", period="2025-03..2026-09", mean_daily_net=g["net"].mean(),
                             n_trades=len(g), note="per-trade mean"))
    (OUTD / "notices.md").write_text("\n".join(L) + "\n")
    led = pd.read_csv(LEDGER)
    pd.concat([led, pd.DataFrame(led_rows)], ignore_index=True).to_csv(LEDGER, index=False)
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
