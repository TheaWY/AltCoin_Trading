"""Forward paper test F2 (research/forward.yaml): pump chart-CNN, frozen 6-seed ensemble (data/models/pump_cnn).

Runs hourly at minute 2 (launchd com.altcoin.pumpcnn). For the hour that just closed (T):
  trigger = close(T) / close(T-1h) - 1 >= 10%, 24h quote volume >= $2M, >= 72h of 1m history
  image   = last 60 one-minute bars (same renderer as training), mean probability of 6 seeds
  side    = long if p >= hi, short if p <= lo (Dec-2025 thresholds), else no trade
  entry   = open of the minute starting T+60s, exit = open of the minute starting T+60s+4h, net of fees+slippage
Also settles open paper trades. Paper only; never places orders. Table pump_cnn_paper.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from b2_dataset import image  # noqa: E402
from b2_models import CNN2D  # noqa: E402
from src.data.storage import get_storage  # noqa: E402

MOD = ROOT / "data/models/pump_cnn"
FEE = 0.0005
SCHEMA = """CREATE TABLE IF NOT EXISTS pump_cnn_paper (
  symbol TEXT NOT NULL, ts_signal BIGINT NOT NULL, ret_1h DOUBLE PRECISION, dv24 DOUBLE PRECISION,
  p_mean DOUBLE PRECISION, side INTEGER, entry_px DOUBLE PRECISION, exit_px DOUBLE PRECISION,
  gross DOUBLE PRECISION, net DOUBLE PRECISION, cost DOUBLE PRECISION, status TEXT, created BIGINT,
  PRIMARY KEY (symbol, ts_signal))"""


def slip(dv: float) -> float:
    return 0.0002 if dv > 1e8 else 0.0005 if dv > 2e7 else 0.0010 if dv > 5e6 else 0.0020


def q(st, sql, params=()):
    with st._connect() as c:  # noqa: SLF001
        cur = c.raw.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        if cur.description is None:
            return None
        return pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])


def models():
    meta = json.loads((MOD / "meta.json").read_text())
    ms = []
    for s in meta["seeds"]:
        m = CNN2D()
        m.load_state_dict(torch.load(MOD / f"seed{s}.pt", map_location="cpu"))
        m.eval()
        ms.append(m)
    return ms, meta


def settle(st) -> int:
    op = q(st, "SELECT symbol, ts_signal, side, cost FROM pump_cnn_paper WHERE status='open'")
    n = 0
    for r in op.itertuples(index=False):
        e0, e1 = int(r.ts_signal) + 60, int(r.ts_signal) + 60 + 240 * 60
        px = q(st, "SELECT ts, open FROM prices_1m WHERE symbol=? AND ts IN (?, ?)", (r.symbol, e0, e1))
        if px is None or len(px) < 2:
            if time.time() > e1 + 3 * 3600:
                q(st, "UPDATE pump_cnn_paper SET status='no_data' WHERE symbol=? AND ts_signal=?", (r.symbol, r.ts_signal))
            continue
        p0 = float(px.loc[px["ts"] == e0, "open"].iloc[0])
        p1 = float(px.loc[px["ts"] == e1, "open"].iloc[0])
        g = r.side * (p1 / p0 - 1)
        q(st, "UPDATE pump_cnn_paper SET entry_px=?, exit_px=?, gross=?, net=?, status='closed' WHERE symbol=? AND ts_signal=?",
          (p0, p1, g, g - r.cost, r.symbol, r.ts_signal))
        n += 1
    return n


def main() -> int:
    st = get_storage()
    q(st, SCHEMA)
    T = int(time.time()) // 3600 * 3600
    ms, meta = models()
    d = q(st, "SELECT symbol, ts, open, high, low, close, quote_volume FROM prices_1m WHERE ts >= ? AND ts < ?",
          (T - 3 * 3600, T))
    vol = q(st, "SELECT symbol, SUM(quote_volume) AS dv24 FROM prices_1m WHERE ts >= ? AND ts < ? GROUP BY symbol",
            (T - 86400, T))
    first = q(st, "SELECT symbol, MIN(ts) AS t0 FROM prices_1m WHERE ts >= ? GROUP BY symbol", (T - 4 * 86400,))
    dv = dict(zip(vol["symbol"], vol["dv24"].astype(float)))
    t0 = dict(zip(first["symbol"], first["t0"].astype(int)))
    made = 0
    for sym, g in d.groupby("symbol"):
        g = g.set_index("ts").sort_index()
        last, prev = T - 60, T - 3660
        if last not in g.index or prev not in g.index:
            continue
        r1 = float(g.at[last, "close"]) / float(g.at[prev, "close"]) - 1
        if r1 < 0.10 or dv.get(sym, 0) < 2e6 or t0.get(sym, T) > T - 72 * 3600 + 300:
            continue
        w = g.reindex(range(T - 3600, T, 60))
        w["close"] = w["close"].ffill().bfill()
        for k in ("open", "high", "low"):
            w[k] = w[k].fillna(w["close"])
        w["quote_volume"] = w["quote_volume"].fillna(0)
        img = image(*(w[k].to_numpy(float) for k in ("open", "high", "low", "close", "quote_volume")))
        x = torch.tensor(img.astype(np.float32)[None, None] / 255.0)
        with torch.no_grad():
            p = float(np.mean([torch.sigmoid(m(x)).item() for m in ms]))
        side = 1 if p >= meta["hi"] else -1 if p <= meta["lo"] else 0
        cost = 2 * (FEE + slip(dv[sym]))
        q(st, "INSERT INTO pump_cnn_paper (symbol, ts_signal, ret_1h, dv24, p_mean, side, cost, status, created) "
              "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
          (sym, T, r1, dv[sym], p, side, cost, "open" if side else "skip", int(time.time())))
        made += 1
    n = settle(st)
    print(time.strftime("%Y-%m-%d %H:%M"), f"T={T} pumps={made} settled={n}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
