#!/usr/bin/env python3
"""Can you wait until the move shows its direction and THEN get in?

Events: every day at 00:00 UTC, the 10 liquid coins with the highest 24h
volatility (the 'will move a lot' list), over the whole 6 months.
Walk the next 24h minute by minute from the 1m archive. For each trigger X
(+3%, +5%, +7%, +10% up; the same down for shorts), enter the first minute
the price has moved X from the 00:00 price, in that direction, and then:
  - chance of going another +10% before giving back 5% (from the late entry)
  - chance of going another +10% before coming back to the 00:00 price
  - trade: take-profit +10% / stop -5% from the late entry, else exit at
    the 24h mark; 0.3% round trip; if both are touched in one minute the
    stop is assumed
Compared with entering at 00:00 blind. Writes data/reports/direction/late_entry.{json,md}.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from src.research import deep_search as ds  # noqa: E402

OUT = PROJECT_ROOT / "data" / "reports" / "direction"
COST = 0.003


def path_trade(h, l, c, i0, side, tp=0.10, sl=0.05):
    e = c[i0]
    for j in range(i0 + 1, len(c)):
        up, dn = h[j] / e - 1, l[j] / e - 1
        if side > 0:
            if dn <= -sl:
                return -sl
            if up >= tp:
                return tp
        else:
            if up >= sl:
                return -sl
            if dn <= -tp:
                return tp
    return side * (c[-1] / e - 1)


def main() -> int:
    p = pd.read_parquet(ds.PANEL, columns=["symbol", "ts", "rv_24h", "dv24_log"])
    p = p[(p["ts"] % 86400 == 0) & (p["dv24_log"] >= np.log1p(2e6))]
    ev = p.sort_values("rv_24h", ascending=False).groupby("ts").head(10)
    trig = (0.0, 0.03, 0.05, 0.07, 0.10)
    rows = []
    for sym, g in ev.groupby("symbol"):
        f = ds.K1M / (sym.replace("/", "") + ".parquet")
        if not f.exists():
            continue
        m = pd.read_parquet(f, columns=["ts", "o", "h", "l", "c"]).set_index("ts")
        for t0 in g["ts"]:
            w = m.loc[t0:t0 + 86400 - 60]
            if len(w) < 1200:
                continue
            o0 = float(w["o"].iloc[0])
            h, l, c = w["h"].to_numpy(), w["l"].to_numpy(), w["c"].to_numpy()
            for side in (1, -1):
                for x in trig:
                    if x == 0:
                        i0 = 0
                        c0 = np.r_[o0, c[1:]]
                    else:
                        hit = np.where((h / o0 - 1 >= x) if side > 0 else (l / o0 - 1 <= -x))[0]
                        if not len(hit):
                            continue
                        i0 = int(hit[0])
                        c0 = c.copy()
                        c0[i0] = o0 * (1 + side * x)          # fill at the trigger price
                    r = path_trade(h, l, c0, i0, side)
                    e = c0[i0]
                    after = slice(i0 + 1, None)
                    fav = (h[after] / e - 1) if side > 0 else -(l[after] / e - 1)
                    adv = -(l[after] / e - 1) if side > 0 else (h[after] / e - 1)
                    # first passage: +10% more vs back to the 00:00 price
                    back = (l[after] <= o0) if side > 0 else (h[after] >= o0)
                    more = fav >= 0.10
                    fb = np.argmax(back) if back.any() else 10**9
                    fm = np.argmax(more) if more.any() else 10**9
                    rows.append({"ts": t0, "symbol": sym, "side": side, "x": x, "minute": i0,
                                 "trade": r - COST, "more10_before_back": int(fm < fb),
                                 "more10": int(more.any()), "adv_max": float(adv.max()) if len(adv) else 0.0})
    d = pd.DataFrame(rows)
    days = d["ts"].nunique()
    out = []
    for (side, x), g in d.groupby(["side", "x"]):
        # per-event mean and a day-clustered t for the trade
        daily = g.groupby("ts")["trade"].mean()
        out.append({"side": int(side), "x": float(x), "events": int(len(g)), "reach_share": float(len(g) / (10 * days)),
                    "median_minutes": float(g["minute"].median()), "more10": float(g["more10"].mean()),
                    "more10_before_back": float(g["more10_before_back"].mean()),
                    "adv_median": float(g["adv_max"].median()), "trade": float(g["trade"].mean()),
                    "trade_t": float(daily.mean() / daily.std() * np.sqrt(len(daily)))})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "late_entry.json").write_text(json.dumps(out))
    L = ["# 방향이 보인 뒤 올라타기 (매일 00시 변동성 상위 10개, 6개월)", "",
         "| 방향 | 진입 조건 | 도달 비율 | 도달까지 (중앙값) | 진입 후 추가 +10% | 00시 가격 되돌아오기 전에 +10% | 진입 후 최대 역행 (중앙값) | 거래 +10%/-5% (비용 포함) | t |",
         "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for o in out:
        lab = "00시 바로" if o["x"] == 0 else f"{'+' if o['side'] > 0 else '-'}{o['x'] * 100:.0f}% 도달 후"
        L.append(f"| {'롱' if o['side'] > 0 else '숏'} | {lab} | {o['reach_share'] * 100:.0f}% | {o['median_minutes'] / 60:.1f}시간 | "
                 f"{o['more10'] * 100:.0f}% | {o['more10_before_back'] * 100:.0f}% | {o['adv_median'] * 100:.1f}% | "
                 f"{o['trade'] * 100:+.2f}% | {o['trade_t']:+.1f} |")
    (OUT / "late_entry.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
