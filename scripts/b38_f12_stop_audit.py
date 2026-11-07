"""B38 (2026-10-03): why is F12 stopping out 7 of 7? Audit of the stop rule that was added at registration but never back-tested.

B35 measured the "fresh burst" (r7d <= 0) follow-through as a raw 4h return (+3.7% mean) with NO stop. F12 then added a
-3% hard stop checked on 1-minute LOWS. A coin that just moved +5..10% in 30 minutes has 1-minute wicks that routinely
exceed 3%, so the stop may be converting a positive raw expectation into a stream of -3.3% exits.
This script replays the F12 trigger over the last 14 days of prices_1m (5-min grid, same thresholds) and reports, for the
r7d <= 0 subset, the raw 4h return vs the F12 exit rule under stop widths 3/5/7/10% on lows and on closes.
Memory-light: 5-min aggregates come from SQL; 1-minute lows are fetched per burst. Output research/b38_f12_stop_audit.md.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from src.data.storage import get_storage  # noqa: E402
from oi_drop_short_paper import q  # noqa: E402
from pairs_divergence_paper import slip  # noqa: E402
from fresh_burst_paper import R30, VOLX, R7D_MAX, DV_MIN, FEE, crypto_only  # noqa: E402

DAYS, HOLD = 14, 4 * 3600
OUT = ROOT / "research/b38_f12_stop_audit.md"


def ci(x, n=2000):
    x = np.asarray(x, float); x = x[np.isfinite(x)]
    if len(x) < 5:
        return (np.nan, np.nan)
    rng = np.random.default_rng(7); b = [x[rng.integers(0, len(x), len(x))].mean() for _ in range(n)]
    return float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))


def main():
    t0 = time.time(); st = get_storage(); now = int(time.time()); start = now - (DAYS + 8) * 86400
    c5 = q(st, "SELECT DISTINCT ON (symbol, b) symbol, (ts / 300) * 300 AS b, close FROM prices_1m WHERE ts >= %s ORDER BY symbol, b, ts DESC", (start,))
    v5 = q(st, "SELECT symbol, (ts / 300) * 300 AS b, sum(quote_volume) AS qv FROM prices_1m WHERE ts >= %s GROUP BY 1, 2", (start,))
    d = c5.merge(v5, on=["symbol", "b"]).sort_values(["symbol", "b"])
    P = d.pivot(index="b", columns="symbol", values="close").sort_index()
    V = d.pivot(index="b", columns="symbol", values="qv").sort_index().fillna(0.0)
    idx = np.arange(P.index.min(), P.index.max() + 300, 300); P = P.reindex(idx).ffill(limit=3); V = V.reindex(idx).fillna(0.0)
    r30 = P / P.shift(6) - 1
    v30 = V.rolling(6).sum(); vbase = V.shift(6).rolling(24).sum() / 24 * 6     # prior 2h average per 30 min
    volx = v30 / vbase.replace(0, np.nan)
    r7d = P / P.shift(2016) - 1
    dv24 = V.rolling(288).sum()
    cand = ((r30 >= R30) & (volx >= VOLX) & (dv24 >= DV_MIN)).values & (P.index.values >= now - DAYS * 86400)[:, None]
    okc = crypto_only(list(P.columns))
    recs = []
    last_sym = {}
    for i, j in zip(*np.where(cand)):
        t, s = int(P.index[i]), P.columns[j]
        if s not in okc or t - last_sym.get(s, 0) < HOLD:
            continue
        last_sym[s] = t
        px = q(st, "SELECT ts, low, close FROM prices_1m WHERE symbol = %s AND ts > %s AND ts <= %s ORDER BY ts", (s, t, t + HOLD))
        if px is None or len(px) < 60:
            continue
        e = float(P.iat[i, j]); cost = 2 * (FEE + slip(float(dv24.iat[i, j])))
        rec = {"symbol": s, "ts": t, "r30": float(r30.iat[i, j]), "r7d": float(r7d.iat[i, j]), "raw4h": float(px.close.iloc[-1] / e - 1 - cost),
               "maxdd_low": float(px.low.min() / e - 1), "t_to_3pct": np.nan}
        hit = px[px.low <= e * 0.97]
        if len(hit):
            rec["t_to_3pct"] = (int(hit.ts.iloc[0]) - t) / 60
        for w in (0.03, 0.05, 0.07, 0.10):
            for basis in ("low", "close"):
                h = px[px[basis] <= e * (1 - w)]
                exit_px = e * (1 - w) if len(h) else float(px.close.iloc[-1])
                rec[f"stop{int(w * 100)}_{basis}"] = exit_px / e - 1 - cost
        recs.append(rec)
    e = pd.DataFrame(recs)
    e.to_parquet(ROOT / "data/cache/b38_bursts.parquet", index=False)
    f = e[e.r7d <= R7D_MAX]
    L = [f"# B38 F12 stop audit ({time.strftime('%Y-%m-%d %H:%M')} KST)", "",
         f"F12 trigger replayed on the last {DAYS} days of 1-minute data (5-min grid): {len(e)} bursts, {len(f)} with r7d <= 0 (the F12 subset).",
         "Returns net of fee + slippage. 'low' = stop checked on 1-minute lows (what F12 does live); 'close' = on 1-minute closes.", "",
         "| exit rule | n | mean | median | 95% CI | hit | stopped |", "|---|---|---|---|---|---|---|"]
    for name in ["raw4h"] + [f"stop{w}_{b}" for w in (3, 5, 7, 10) for b in ("low", "close")]:
        x = f[name].dropna(); lo, hi = ci(x)
        stopped = (f[name] < f["raw4h"] - 1e-9).mean() if name != "raw4h" else 0.0
        L.append(f"| {name} | {len(x)} | {x.mean() * 100:+.2f}% | {x.median() * 100:+.2f}% | [{lo * 100:+.2f}, {hi * 100:+.2f}] | {(x > 0).mean() * 100:.0f}% | {stopped * 100:.0f}% |")
    L += ["", f"Share of F12-subset bursts whose 1-minute low touches -3% within 4h: {f.t_to_3pct.notna().mean() * 100:.0f}%; "
          f"median time to touch {f.t_to_3pct.median():.0f} min. Median max drawdown on lows {f.maxdd_low.median() * 100:+.1f}%.", "",
          "## all bursts (no r7d filter), same table", "", "| exit rule | n | mean | 95% CI | hit |", "|---|---|---|---|---|"]
    for name in ["raw4h", "stop3_low", "stop5_low", "stop5_close", "stop10_close"]:
        x = e[name].dropna(); lo, hi = ci(x)
        L.append(f"| {name} | {len(x)} | {x.mean() * 100:+.2f}% | [{lo * 100:+.2f}, {hi * 100:+.2f}] | {(x > 0).mean() * 100:.0f}% |")
    L += ["", f"_runtime {time.time() - t0:.0f}s_"]
    OUT.write_text("\n".join(L)); print(OUT, f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
