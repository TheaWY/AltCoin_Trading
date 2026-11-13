"""B34 (2026-10-02): "would the OPPOSITE have done better?" One clean number per paper book: realised P&L as traded vs the
same trades with the side flipped, same entry/exit prices, same costs (costs are paid either way; funding flips sign).
Books: main paper book (paper_trades + archive), every forward paper test table. Bootstrap CI on the mean per-trade
difference so a sign flip on a handful of trades is not mistaken for evidence."""
from __future__ import annotations
import sys, time
import numpy as np, pandas as pd
sys.path.insert(0, "."); sys.path.insert(0, "scripts")
from src.data.storage import get_storage
from oi_drop_short_paper import q

st = get_storage()
BOOK_START = 1790553600  # 2026-09-28 00:00 UTC


def ci(x, B=2000, seed=0):
    x = np.asarray(x, float); x = x[np.isfinite(x)]
    if len(x) < 3:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed); m = [x[rng.integers(0, len(x), len(x))].mean() for _ in range(B)]
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


rows = []


def add(name, gross, cost, funding=None, unit="%"):
    gross = np.asarray(gross, float); cost = np.nan_to_num(np.asarray(cost, float))
    fund = np.nan_to_num(np.asarray(funding, float)) if funding is not None else np.zeros_like(gross)
    ok = np.isfinite(gross); gross, cost, fund = gross[ok], cost[ok], fund[ok]
    if not len(gross):
        rows.append({"book": name, "n": 0}); return
    net = gross + fund - cost; inv = -gross - fund - cost
    lo, hi = ci(inv - net)
    rows.append({"book": name, "n": int(len(gross)), "as_traded": float(net.sum()), "opposite": float(inv.sum()), "diff_mean": float((inv - net).mean()),
                 "diff_ci_lo": lo, "diff_ci_hi": hi, "hit_as_traded": float((net > 0).mean()), "hit_opposite": float((inv > 0).mean()), "unit": unit})


# main paper book (USDT): pnl is net of fees; opposite = -(pnl + fees) - fees
for name, tab, since in (("main book since 09-28 (USDT)", "paper_trades", BOOK_START), ("main book all history (USDT)", "paper_trades", 0),
                         ("main book archive (USDT)", "paper_trades_archive", 0)):
    d = q(st, f"SELECT pnl, fees, closed_at, strategy FROM {tab} WHERE status LIKE %s AND pnl IS NOT NULL", ("closed%",))
    if d is not None and len(d):
        d["t"] = pd.to_numeric(d["closed_at"], errors="coerce")
        d = d[d["t"] >= since]
        add(name, d["pnl"].to_numpy() + np.nan_to_num(d["fees"].to_numpy()), np.nan_to_num(d["fees"].to_numpy()), unit="USDT")
        if since == 0 and tab == "paper_trades":
            for strat, g in d.groupby("strategy"):
                add(f"  main book by strategy: {strat} (USDT)", g["pnl"].to_numpy() + np.nan_to_num(g["fees"].to_numpy()), np.nan_to_num(g["fees"].to_numpy()), unit="USDT")

# forward paper tests: gross / funding / cost in return units
for name, tab, fcol in (("F2 pump CNN", "pump_cnn_paper", None), ("F3 crash rebound", "crash_rebound_paper", None), ("F4 spot-led", "spot_led_paper", "funding"),
                        ("F5 unlock short", "unlock_short_paper", "funding"), ("F6 OI-drop short", "oi_drop_short_paper", "funding"),
                        ("F7 Upbit share weekly (legs)", "f7_vshare_paper", "funding"), ("F8 late-session", "f8_latesession_paper", None),
                        ("AR28741da3 upbit_share_24h (legs)", "fa_ar28741da3", "funding"), ("ARa200b4d5 upbit_share_7d (legs)", "fa_ara200b4d5", "funding"),
                        ("ARd5d71ff6 korea_share_24h FM (legs)", "fa_ard5d71ff6", "funding")):
    cols = "gross, cost" + (f", {fcol} AS funding" if fcol else "")
    d = q(st, f"SELECT {cols} FROM {tab} WHERE status LIKE %s AND gross IS NOT NULL", ("closed%",))
    if d is None or not len(d):
        rows.append({"book": name, "n": 0}); continue
    add(name, d["gross"], d["cost"], d["funding"] if fcol else None)
d = q(st, "SELECT net60 FROM upbit_notice_paper WHERE net60 IS NOT NULL")
if d is not None and len(d):
    add("F1 Upbit notice (net60)", d["net60"], np.zeros(len(d)))

stamp = time.strftime("%F %T")
L = [f"# B34 opposite-side audit - {stamp} KST", "",
     "Same trades, same prices, same costs, side flipped. diff = opposite - as traded, per trade, with a bootstrap 95% CI; "
     "a CI that includes 0 means the flip is not distinguishable from noise. Caveat for the main book: exits were stops, z-stops, "
     "rebalances and manual resets taken on the ORIGINAL side; an inverted position would have met different exits, so the "
     "'opposite' number is an upper bound on what a flipped book could have earned, not a tradable result.", "",
     "| book | n | as traded | opposite | mean diff/trade | diff 95% CI | hit as traded | hit opposite |", "|---|---|---|---|---|---|---|---|"]
for r in rows:
    if r.get("n", 0) == 0:
        L.append(f"| {r['book']} | 0 | – | – | – | – | – | – |"); continue
    u = r["unit"]; f = (lambda v: f"{v:+.2f} {u}") if u == "USDT" else (lambda v: f"{v * 100:+.2f}%")
    L.append(f"| {r['book']} | {r['n']} | {f(r['as_traded'])} | {f(r['opposite'])} | {f(r['diff_mean'])} | [{f(r['diff_ci_lo'])}, {f(r['diff_ci_hi'])}] | {r['hit_as_traded']:.2f} | {r['hit_opposite']:.2f} |")
open("research/b34_opposite_" + time.strftime("%F") + ".md", "w").write("\n".join(L) + "\n")
print("\n".join(L))
