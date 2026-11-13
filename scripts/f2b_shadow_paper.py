"""Forward shadow test F2b (registered 2026-10-04 after B39, approved by 유리): F2 signal variants, paper only.

Every F2 signal (pump_cnn_paper, side != 0) created after REG is copied here and, once its 4h window has closed, settled
from prices_1m under two exits:
  net_base  = F2 as traded (entry open T+60s, exit open T+60s+4h)
  net_trail = trailing exit: armed once a 1m CLOSE is +15% in our favour, exit at the next minute's open when the close
              gives back 10 points from its peak; otherwise the 4h exit. Trail exits pay one extra slippage leg.
Flags fixed at registration (B39, 2024-era median, never re-tuned): margin_hi = CNN margin beyond its threshold
>= 0.0230188; long = side > 0.
Registered variants (research/forward.yaml):
  F2b = margin_hi & net_trail       (both sides)
  F2L = long & net_trail            (long only; spot-executable, no short-squeeze tail)
Judged against F2 on the same signals. Never places orders; LIVE_TRADING is not read.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from src.data.storage import get_storage  # noqa: E402

REG = 1791104400                     # 2026-10-04 18:00 KST: signals at/after this are the forward sample
MARGIN_CUT, ARM, GIVE, H = 0.0230188, 0.15, 0.10, 240
META = json.loads((ROOT / "data/models/pump_cnn/meta.json").read_text())
SCHEMA = """CREATE TABLE IF NOT EXISTS f2b_shadow_paper (
  symbol TEXT NOT NULL, ts_signal BIGINT NOT NULL, side INTEGER, p_mean DOUBLE PRECISION, margin DOUBLE PRECISION,
  margin_hi BOOLEAN, cost DOUBLE PRECISION, entry_px DOUBLE PRECISION, net_base DOUBLE PRECISION,
  net_trail DOUBLE PRECISION, trail_exit_min INTEGER, status TEXT, created BIGINT, PRIMARY KEY (symbol, ts_signal))"""


def q(st, sql, params=()):
    with st._connect() as c:  # noqa: SLF001
        cur = c.raw.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        if cur.description is None:
            return None
        return pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])


def settle(st, r):
    t0 = int(r.ts_signal) + 60
    px = q(st, "SELECT ts, open, close FROM prices_1m WHERE symbol=? AND ts >= ? AND ts <= ? ORDER BY ts", (r.symbol, t0, t0 + H * 60))
    if px is None or not len(px):
        return None
    w = px.set_index("ts").reindex(range(t0, t0 + (H + 1) * 60, 60))
    if pd.isna(w["open"].iloc[0]) or pd.isna(w["open"].iloc[-1]):
        return None
    w["close"] = w["close"].ffill(); w["open"] = w["open"].fillna(w["close"])
    e0, s, cost = float(w["open"].iloc[0]), int(r.side), float(r.cost)
    ro = s * (w["open"].to_numpy() / e0 - 1); rc = s * (w["close"].to_numpy() / e0 - 1)
    base = ro[H] - cost
    trail, k_exit, pk = base, None, -1e9
    for k in range(H):
        pk = max(pk, rc[k])
        if pk >= ARM and rc[k] <= pk - GIVE:
            trail, k_exit = ro[k + 1] - cost - cost / 2, k + 1
            break
    return e0, base, trail, k_exit


def main() -> int:
    st = get_storage()
    q(st, SCHEMA)
    now = int(time.time())
    new = q(st, "SELECT symbol, ts_signal, side, p_mean, cost FROM pump_cnn_paper WHERE side <> 0 AND ts_signal >= ?", (REG,))
    for r in (new.itertuples(index=False) if new is not None else []):
        m = float(r.p_mean) - META["hi"] if r.side > 0 else META["lo"] - float(r.p_mean)
        q(st, "INSERT INTO f2b_shadow_paper (symbol, ts_signal, side, p_mean, margin, margin_hi, cost, status, created) "
              "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
          (r.symbol, int(r.ts_signal), int(r.side), float(r.p_mean), m, bool(m >= MARGIN_CUT), float(r.cost), "open", now))
    op = q(st, "SELECT symbol, ts_signal, side, cost FROM f2b_shadow_paper WHERE status='open' AND ts_signal + 60 + ? * 60 + 120 <= ?", (H, now))
    n = 0
    for r in (op.itertuples(index=False) if op is not None else []):
        res = settle(st, r)
        if res is None:
            if now > int(r.ts_signal) + 60 + H * 60 + 3 * 3600:
                q(st, "UPDATE f2b_shadow_paper SET status='no_data' WHERE symbol=? AND ts_signal=?", (r.symbol, int(r.ts_signal)))
            continue
        e0, base, trail, k = res
        q(st, "UPDATE f2b_shadow_paper SET entry_px=?, net_base=?, net_trail=?, trail_exit_min=?, status='closed' WHERE symbol=? AND ts_signal=?",
          (e0, base, trail, k, r.symbol, int(r.ts_signal)))
        n += 1
    s = q(st, "SELECT count(*) n, avg(net_base) b, avg(net_trail) t, avg(CASE WHEN margin_hi THEN net_trail END) f2b, "
              "avg(CASE WHEN side > 0 THEN net_trail END) f2l FROM f2b_shadow_paper WHERE status='closed'")
    print(time.strftime("%Y-%m-%d %H:%M"), f"settled={n}", s.to_dict("records")[0] if s is not None else "", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
