"""B44 (2026-10-04, Upbit queue U3): Upbit notice events traded LONG-ONLY on Upbit (re-run of F1/F11/F13 on Upbit data).

Events: Upbit notices 2022-01..now from exchange_notices (kinds listing, warning, warning_lifted, delisting, other-KRW-add).
Prices: Upbit 1-minute candles from notice-1h to notice+96h per KRW market (data/upbit_db/notice_m1, fetched here).
New KRW markets: trading start = first 1m candle after the notice (no candle before it).
Entries (long): listing/KRW-add: market open +1m (2nd minute, the first is not fillable), +5m, +15m, +60m, +240m;
warning / delisting / warning_lifted: notice +1m, +60m, +240m, +24h. Exits: hold 1h, 4h, 24h, 72h.
Costs: 2 x 0.05% fee + slippage (0.2% per side for the first day of a new listing, else by 24h value tier).
Eras by entry time: train < 2025-07 | val 2025-07..12 | TEST 2026. Small samples: variant chosen on val needs n >= 10.
Mirror (opposite side, not executable on Upbit) reported as in B43. Output research/b44_upbit_notices.md.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import upbit_db as U  # noqa: E402
from b43_upbit_hourly_suite import boot, era, slip  # noqa: E402
from scripts.upbit_listing_watcher import db  # noqa: E402

OUT = U.DB / "notice_m1"; OUT.mkdir(parents=True, exist_ok=True)
FEE = 0.0005
NEW_KINDS = ("listing", "krw_add")


def notices():
    d = pd.DataFrame(db("SELECT notice_id, ts, kind, title, symbols FROM exchange_notices WHERE source='upbit' AND ts >= 1640995200", ()))
    d.loc[(d.kind == "other") & d.title.str.contains("KRW", na=False) & d.title.str.contains("추가", na=False), "kind"] = "krw_add"
    d = d[d.kind.isin(["listing", "krw_add", "warning", "warning_lifted", "delisting"])]
    if "listing" in set(d.kind):
        d = d[(d.kind != "listing") | d.title.str.contains("KRW", na=False)]
    rows = []
    for r in d.itertuples(index=False):
        for s in str(r.symbols or "").split(","):
            s = s.strip().upper()
            if s:
                rows.append((int(r.notice_id), int(r.ts), r.kind, s, r.title))
    return pd.DataFrame(rows, columns=["nid", "ts", "kind", "sym", "title"]).drop_duplicates(["nid", "sym"])


def fetch(n):
    now = int(time.time())
    for r in n.itertuples(index=False):
        f = OUT / f"{r.nid}_{r.sym}.parquet"
        if f.exists() or r.ts + 96 * 3600 > now:
            continue
        U.candles(f"KRW-{r.sym}", "minutes/1", r.ts - 3600, r.ts + 96 * 3600).to_parquet(f, index=False)


def trades(n):
    out = []
    for r in n.itertuples(index=False):
        f = OUT / f"{r.nid}_{r.sym}.parquet"
        if not f.exists():
            continue
        w = pd.read_parquet(f)
        if len(w) < 30:
            continue
        w = w.set_index("ts").sort_index()
        new = r.kind in NEW_KINDS
        if new:
            if (w.index < r.ts).any():                      # market already traded before the notice: not a fresh open
                new = False
                t_ref = r.ts
            else:
                t_ref = int(w.index[0])                      # first traded minute = market open
        else:
            t_ref = r.ts // 60 * 60 + 60
        idx = np.arange(t_ref, t_ref + 96 * 3600, 60)
        g = w.reindex(idx)
        g["c"] = g["c"].ffill(); g["o"] = g["o"].fillna(g["c"]); g["v"] = g["v"].fillna(0)
        o = g["o"].to_numpy()
        v = g["v"].to_numpy()
        entries = (1, 5, 15, 60, 240) if new else (1, 60, 240, 1440)
        for d in entries:
            if d >= len(o) or np.isnan(o[d]):
                continue
            v24 = v[max(d - 1440, 0):d].sum() * (1440 / max(min(d, 1440), 1))
            sl = 0.002 if new and d < 1440 else float(slip(v24))
            cost = 2 * (FEE + sl)
            for h, hn in ((60, "1h"), (240, "4h"), (1440, "24h"), (4320, "72h")):
                x = d + h
                if x >= len(o) or np.isnan(o[x]):
                    continue
                kind = ("new KRW market" if new else r.kind)
                out.append((kind, f"{kind}: buy {'open' if new else 'notice'}+{d}m, hold {hn}", r.sym, int(t_ref + d * 60), o[x] / o[d] - 1, cost))
    T = pd.DataFrame(out, columns=["family", "variant", "market", "t", "gross", "cost"])
    T["net"], T["mirror"] = T.gross - T.cost, -T.gross - T.cost
    T["era"], T["day"] = era(T.t.to_numpy()), T.t // 86400
    return T


def main():
    t0 = time.time()
    n = notices()
    print("notice-symbols", len(n), n.kind.value_counts().to_dict(), flush=True)
    fetch(n)
    T = trades(n)
    T.to_parquet(U.DB / "b44_trades.parquet", index=False)
    L = ["# B44 Upbit notices, long-only on Upbit (2026-10-04)", "",
         f"{n.nid.nunique()} Upbit notices since 2022 ({len(n)} notice-symbol pairs). Net after Upbit fee + slippage. "
         "Eras by entry: train < 2025-07, val 2025-07..12, TEST 2026. Mirror = opposite side (not executable on Upbit).", ""]
    verdicts = []
    for fam, F in T.groupby("family"):
        L += [f"## {fam}", "", "| variant | all n / mean / win | train | val | test | test mirror |", "|---|---|---|---|---|---|"]
        best = None
        for var, g in F.groupby("variant", sort=False):
            c = {er: (f"{(g.era == er).sum()} / {g[g.era == er].net.mean():+.2%}" if (g.era == er).any() else "-") for er in ("train", "val", "test")}
            te = g[g.era == "test"]
            L.append(f"| {var} | {len(g)} / {g.net.mean():+.2%} / {(g.net > 0).mean():.0%} | {c['train']} | {c['val']} | {c['test']} | "
                     f"{te.mirror.mean():+.2%} |" if len(te) else f"| {var} | {len(g)} / {g.net.mean():+.2%} / {(g.net > 0).mean():.0%} | {c['train']} | {c['val']} | - | - |")
            va = g[g.era == "val"]
            if len(va) >= 10 and (best is None or va.net.mean() > best[1]):
                best = (var, va.net.mean())
        if best:
            g = F[(F.variant == best[0]) & (F.era == "test")]
            lo, hi = boot(g.net.to_numpy(), g.day.to_numpy()) if len(g) >= 5 else (np.nan, np.nan)
            verdicts.append((fam, best[0], best[1], len(g), g.net.mean() if len(g) else np.nan, lo, hi,
                             "PASS" if len(g) >= 10 and lo > 0 else "FAIL"))
        L.append("")
    L += ["## Verdicts (chosen on validation, judged on 2026 TEST)", "", "| family | variant | val mean | test n | test mean | 95% CI | verdict |", "|---|---|---|---|---|---|---|"]
    for v in verdicts:
        L.append(f"| {v[0]} | {v[1]} | {v[2]:+.2%} | {v[3]} | {v[4]:+.2%} | [{v[5]:+.2%}, {v[6]:+.2%}] | **{v[7]}** |")
    L += ["", f"Runtime {time.time() - t0:.0f}s."]
    (ROOT / "research/b44_upbit_notices.md").write_text("\n".join(L) + "\n")
    print("\n".join(L[-12:]))


if __name__ == "__main__":
    main()
