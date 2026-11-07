"""Forward paper test F5 (research/forward.yaml): short into large cliff token unlocks.

Hourly at minute 6 (launchd com.altcoin.unlockshort). Refreshes DefiLlama emissions data once a day
(data/cache/unlocks_live). Upcoming cliff unlock >= 1% of circulating (cumulative documented unlocked the day before),
coin trades as a Binance USDT perp -> paper SHORT opened at the first run at/after unlock-72h (skipped if already later
than unlock-60h), entry = open of the minute T+60s, exit = open of the minute at unlock+24h. Funding received is added.
Paper only; never places orders. Table unlock_short_paper."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.data.storage import get_storage  # noqa: E402

FEE, H_, D_ = 0.0005, 3600, 86400
LIVE = ROOT / "data/cache/unlocks_live"
SCHEMA = """CREATE TABLE IF NOT EXISTS unlock_short_paper (
  symbol TEXT NOT NULL, unlock_ts BIGINT NOT NULL, size DOUBLE PRECISION, ts_open BIGINT, ts_exit BIGINT,
  entry_px DOUBLE PRECISION, exit_px DOUBLE PRECISION, gross DOUBLE PRECISION, funding DOUBLE PRECISION,
  net DOUBLE PRECISION, cost DOUBLE PRECISION, status TEXT, created BIGINT, PRIMARY KEY (symbol, unlock_ts))"""


def slip(dv: float) -> float:
    return 0.0002 if dv > 1e8 else 0.0005 if dv > 2e7 else 0.0010 if dv > 5e6 else 0.0020


def q(st, sql, params=()):
    with st._connect() as c:  # noqa: SLF001
        cur = c.raw.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        if cur.description is None:
            return None
        return pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])


def refresh():
    LIVE.mkdir(parents=True, exist_ok=True)
    stamp = LIVE / "_refreshed"
    if stamp.exists() and time.time() - stamp.stat().st_mtime < 20 * H_:
        return
    s = requests.Session()
    lst = s.get("https://defillama-datasets.llama.fi/emissionsProtocolsList", timeout=30).json()
    for name in lst:
        name = name if isinstance(name, str) else (name.get("token") or name.get("name"))
        if not name:
            continue
        try:
            r = s.get(f"https://defillama-datasets.llama.fi/emissions/{name}", timeout=30)
            if r.status_code == 200:
                (LIVE / f"{str(name).replace('/', '_')}.json").write_text(r.text)
        except requests.RequestException:
            pass
        time.sleep(0.2)
    stamp.write_text(str(int(time.time())))


def upcoming(st, now):
    cg = q(st, "SELECT DISTINCT symbol, cg_id FROM cg_daily WHERE cg_id IS NOT NULL")
    cg2sym = dict(zip(cg["cg_id"], cg["symbol"]))
    live_syms = set(q(st, "SELECT DISTINCT symbol FROM prices_1m WHERE ts >= ?", (now - 2 * H_,))["symbol"])
    out = []
    for f in LIVE.glob("*.json"):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        sym = cg2sym.get(d.get("gecko_id"))
        if not sym:
            continue
        base = sym.split("/")[0]
        perp = next((p for p in (f"{base}/USDT", f"1000{base}/USDT") if p in live_syms), None)
        if perp is None:
            continue
        circ = {}
        for sec in (d.get("documentedData") or {}).get("data", []):
            for p in sec.get("data", []):
                circ[p["timestamp"]] = circ.get(p["timestamp"], 0.0) + float(p.get("unlocked") or 0)
        if not circ:
            continue
        cts = np.array(sorted(circ))
        cval = np.array([circ[t] for t in cts])
        day = {}
        for e in (d.get("metadata") or {}).get("events", []):
            t = int(e["timestamp"])
            if e.get("unlockType") != "cliff" or not (now < t <= now + 80 * H_):
                continue
            k = np.searchsorted(cts, t - D_) - 1
            if k < 0 or cval[k] <= 0:
                continue
            key = t // D_
            tok = float(sum(e.get("noOfTokens") or [0]))
            prev = day.get(key, (t, 0.0, cval[k]))
            day[key] = (min(prev[0], t), prev[1] + tok, prev[2])
        for t, tok, c in day.values():
            if tok / c >= 0.01:
                out.append((perp, t, tok / c))
    return out


def settle(st, now):
    op = q(st, "SELECT symbol, unlock_ts, ts_open, ts_exit, cost FROM unlock_short_paper WHERE status='open'")
    n = 0
    for r in op.itertuples(index=False):
        e0, e1 = int(r.ts_open) + 60, int(r.ts_exit)
        if now < e1 + 120:
            continue
        px = q(st, "SELECT ts, open FROM prices_1m WHERE symbol=? AND ts IN (?, ?)", (r.symbol, e0, e1))
        if px is None or len(px) < 2:
            if now > e1 + 3 * H_:
                q(st, "UPDATE unlock_short_paper SET status='no_data' WHERE symbol=? AND unlock_ts=?", (r.symbol, r.unlock_ts))
            continue
        p0 = float(px.loc[px["ts"] == e0, "open"].iloc[0])
        p1 = float(px.loc[px["ts"] == e1, "open"].iloc[0])
        fr = q(st, "SELECT COALESCE(SUM(funding_rate),0) AS f FROM funding_rates WHERE symbol=? AND timestamp > ? AND timestamp <= ?",
               (r.symbol, e0, e1))
        fund = float(fr["f"].iloc[0]) if fr is not None else 0.0
        g = -(p1 / p0 - 1)
        q(st, "UPDATE unlock_short_paper SET entry_px=?, exit_px=?, gross=?, funding=?, net=?, status='closed' WHERE symbol=? AND unlock_ts=?",
          (p0, p1, g, fund, g - r.cost + fund, r.symbol, r.unlock_ts))
        n += 1
    return n


def main() -> int:
    st = get_storage()
    q(st, SCHEMA)
    now = int(time.time())
    T = now // H_ * H_
    try:
        refresh()
    except Exception as ex:
        print("refresh failed", ex, flush=True)
    opened = 0
    ev = upcoming(st, now)
    for sym, t, size in ev:
        if not (t - 72 * H_ <= T <= t - 60 * H_):
            continue
        dv = q(st, "SELECT COALESCE(SUM(quote_volume),0) AS v FROM prices_1m WHERE symbol=? AND ts >= ? AND ts < ?", (sym, T - D_, T))
        dv24 = float(dv["v"].iloc[0])
        if dv24 < 2e6:
            continue
        exit_ts = (t + D_) // 60 * 60
        q(st, "INSERT INTO unlock_short_paper (symbol, unlock_ts, size, ts_open, ts_exit, cost, status, created) "
              "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
          (sym, t, size, T, exit_ts, 2 * (FEE + slip(dv24)), "open", now))
        opened += 1
    n = settle(st, now)
    print(time.strftime("%Y-%m-%d %H:%M"), f"upcoming={len(ev)} opened={opened} settled={n}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
