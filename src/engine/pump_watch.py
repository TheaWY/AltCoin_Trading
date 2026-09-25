"""Hourly watch of +/-10% hours, live, with the same features as the deep search.

Every hour (scripts/pump_watch.py, launchd com.altcoin.pumpwatch):
  1. rebuild the last ~9 days of hourly feature rows for every coin from the
     live 1-minute table (prices_1m) with deep_search.build_symbol
  2. every coin-hour of the last 30 hours that went +10% (or -10%) within
     the next hour is an event: store where each feature sat, as a percentile
     among all coins at that hour, at the event and 1/3/6 hours before
  3. if a deep-search model is saved (data/models/deep_<target>.txt), score
     every coin at the current hour and store the top 15 (forward test),
     settled once their 24h window closes

Tables: pump_watch_events, pump_watch_picks. Dashboard: /api/research/pump_watch.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.research import deep_search as ds

logger = logging.getLogger(__name__)
MODELS = Path(__file__).resolve().parents[2] / "data" / "models"
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS pump_watch_events (symbol TEXT NOT NULL, ts BIGINT NOT NULL, kind TEXT NOT NULL,
       move DOUBLE PRECISION, pct JSON, raw JSON, PRIMARY KEY (symbol, ts, kind))""",
    """CREATE TABLE IF NOT EXISTS pump_watch_picks (model TEXT NOT NULL, ts BIGINT NOT NULL, symbol TEXT NOT NULL,
       rank INTEGER, score DOUBLE PRECISION, price DOUBLE PRECISION, status TEXT NOT NULL DEFAULT 'open',
       mfe DOUBLE PRECISION, mae DOUBLE PRECISION, ret DOUBLE PRECISION, hit10 INTEGER, trade DOUBLE PRECISION,
       PRIMARY KEY (model, ts, symbol))""",
)
WATCH = ["ret_60m", "ret_240m", "ret_1440m", "vsurge_15m", "vsurge_60m", "nsurge_60m", "taker_60m", "taker_z_60m",
         "cvd_60m", "oi_1h", "oi_4h", "oi_24h", "funding", "ls_global", "smart_crowd", "fut_taker_1h", "rv_1h",
         "rv_ratio_1h_24h", "dhi_24h", "dhi_7d", "max1m_60", "mcap_log", "oi_mcap", "turnover", "age_days"]


def _load_minutes(storage: Any, sym: str, since: int) -> pd.DataFrame:
    df = ds._q(storage, "SELECT ts, open AS o, high AS h, low AS l, close AS c, quote_volume AS qv, trades AS n, "
                        "taker_buy_quote AS tbq FROM prices_1m WHERE symbol = ? AND ts >= ? ORDER BY ts", (sym, since))
    return df.astype({"ts": "int64"})


def build_live(storage: Any, days: int = 9, workers: int = 6) -> pd.DataFrame:
    now = int(time.time())
    until = now // 3600 * 3600 + 3600
    since = until - 36 * 3600                      # rows we need; minutes go back `days`
    aux = ds.load_aux(storage, since)
    syms = list(ds._q(storage, "SELECT DISTINCT symbol FROM prices_1m WHERE ts > ?", (now - 3600,))["symbol"])
    m_since = until - days * 86400

    def one(sym: str) -> pd.DataFrame | None:
        try:
            raw = _load_minutes(storage, sym, m_since)
            if len(raw) < 1500:
                return None
            return ds.build_symbol(sym.replace("/", ""), since, until, aux, raw=raw, live=True)
        except Exception as e:  # noqa: BLE001
            logger.debug("%s: %r", sym, e)
            return None

    with ThreadPoolExecutor(workers) as ex:
        parts = [x for x in ex.map(one, syms) if x is not None and len(x)]
    p = pd.concat(parts, ignore_index=True)
    return ds.cross_features(p, aux["fng"])


def record_events(storage: Any, p: pd.DataFrame) -> int:
    feats = [f for f in WATCH if f in p]
    ranks = p.groupby("ts")[feats].rank(pct=True)
    ranks.index = pd.MultiIndex.from_arrays([p["symbol"], p["ts"]])
    vals = p.set_index(["symbol", "ts"])[feats]
    n = 0
    with storage._connect() as c:  # noqa: SLF001
        for kind, lab, mv in (("up", "up10_1h", "mfe_1h"), ("down", "dn10_1h", "mae_1h")):
            ev = p[p[lab] == 1]
            for _, e in ev.iterrows():
                pct, raw = {}, {}
                for lag in (0, 1, 3, 6):
                    k = (e["symbol"], int(e["ts"]) - lag * 3600)
                    if k in ranks.index:
                        pct[f"{lag}h"] = {f: (None if pd.isna(v) else round(float(v) * 100)) for f, v in ranks.loc[k].items()}
                        raw[f"{lag}h"] = {f: (None if pd.isna(v) else float(v)) for f, v in vals.loc[k].items()}
                c.execute("INSERT INTO pump_watch_events (symbol, ts, kind, move, pct, raw) VALUES (?,?,?,?,?,?) "
                          "ON CONFLICT DO NOTHING", (e["symbol"], int(e["ts"]), kind, float(e[mv]), json.dumps(pct),
                                                     json.dumps(raw)))
                n += 1
    return n


def score_and_settle(storage: Any, p: pd.DataFrame) -> dict[str, int]:
    out = {"picked": 0, "settled": 0}
    import lightgbm as lgb
    now_h = int(p["ts"].max())
    for path in sorted(MODELS.glob("deep_*.txt")):
        name = path.stem
        meta = json.loads(path.with_suffix(".json").read_text()) if path.with_suffix(".json").exists() else {}
        feats = meta.get("features")
        if not feats or any(f not in p for f in feats):
            continue
        bst = lgb.Booster(model_file=str(path))
        cur = p[p["ts"] == now_h]
        if cur.empty:
            continue
        s = bst.predict(cur[feats].to_numpy(np.float32))
        top = np.argsort(-s)[:15]
        with storage._connect() as c:  # noqa: SLF001
            for rank, i in enumerate(top):
                r = cur.iloc[i]
                c.execute("INSERT INTO pump_watch_picks (model, ts, symbol, rank, score, status) VALUES (?,?,?,?,?,'open') "
                          "ON CONFLICT DO NOTHING", (name, now_h, r["symbol"], rank + 1, float(s[i])))
                out["picked"] += 1
    # settle: labels for older hours are now in the live rebuild
    lab = p.set_index(["symbol", "ts"])
    with storage._connect() as c:  # noqa: SLF001
        rows = [dict(r) for r in c.execute("SELECT model, ts, symbol FROM pump_watch_picks WHERE status='open' AND ts <= ?",
                                           (now_h - 25 * 3600,)).fetchall()]
        for r in rows:
            k = (r["symbol"], int(r["ts"]))
            if k not in lab.index or pd.isna(lab.at[k, "mfe_24h"]):
                if int(r["ts"]) < now_h - 30 * 3600:
                    c.execute("UPDATE pump_watch_picks SET status='void' WHERE model=? AND ts=? AND symbol=?",
                              (r["model"], r["ts"], r["symbol"]))
                continue
            L = lab.loc[k]
            win, mae, ret = L["win10_24h"], L["mae_24h"], L["ret_24h"]
            trade = (0.10 if win == 1 else (-0.05 if mae <= -0.05 else ret)) - 0.003
            c.execute("UPDATE pump_watch_picks SET status='closed', mfe=?, mae=?, ret=?, hit10=?, trade=? "
                      "WHERE model=? AND ts=? AND symbol=?",
                      (float(L["mfe_24h"]), float(mae), float(ret), int(L["up10_24h"] == 1), float(trade),
                       r["model"], r["ts"], r["symbol"]))
            out["settled"] += 1
    return out


def run(storage: Any) -> dict[str, Any]:
    with storage._connect() as c:  # noqa: SLF001
        for s in SCHEMA:
            c.execute(s)
    t0 = time.time()
    p = build_live(storage)
    res = {"rows": int(len(p)), "coins": int(p["symbol"].nunique()), "events": record_events(storage, p)}
    res.update(score_and_settle(storage, p))
    res["elapsed_s"] = round(time.time() - t0)
    return res
