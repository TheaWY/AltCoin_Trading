"""Descriptive (not a trading test): anatomy of every +10% hour across all coins, 2025-03..2026-09.

Splits pumps by what happened next (4h from the next-minute open): continued (>= +5%), faded (<= -5%), flat.
Reports per-feature AUC continued-vs-faded, medians, and the minute-by-minute average path before/after.
Out: data/reports/b2/pump_anatomy.md
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from b2_dataset import TAB  # noqa: E402


def auc(a, b):
    a, b = a[np.isfinite(a)], b[np.isfinite(b)]
    r = pd.Series(np.concatenate([a, b])).rank().to_numpy()
    return (r[:len(a)].sum() - len(a) * (len(a) + 1) / 2) / (len(a) * len(b))


def main():
    z = np.load(ROOT / "data/cache/b2_ds_pump.npz", allow_pickle=True)
    tab, g, seq, ts = z["tab"], z["gross"], z["seq"], z["ts"]
    up, dn = g >= 0.05, g <= -0.05
    L = ["# Anatomy of +10% hours (all coins, 2025-03..2026-09)", "",
         f"{len(g)} pump hours. Next 4h from the next-minute open: continued >= +5%: {up.mean():.0%}, "
         f"faded <= -5%: {dn.mean():.0%}, in between: {(~up & ~dn).mean():.0%}. Median 4h: {np.median(g)*100:+.2f}%, "
         f"mean {g.mean()*100:+.2f}%.", "",
         "## What distinguished continuation from fade (AUC 0.5 = no difference; split halves shown)", "",
         "| feature | median if continued | median if faded | AUC all | AUC 2025 | AUC 2026 |", "|---|---|---|---|---|---|"]
    mid = int(pd.Timestamp("2026-01-01").timestamp())
    rows = []
    for k, f in enumerate(TAB):
        x = tab[:, k].astype(float)
        a = auc(x[up], x[dn])
        a1 = auc(x[up & (ts < mid)], x[dn & (ts < mid)])
        a2 = auc(x[up & (ts >= mid)], x[dn & (ts >= mid)])
        rows.append((abs(a - 0.5), f, np.nanmedian(x[up]), np.nanmedian(x[dn]), a, a1, a2))
    for _, f, mu, md, a, a1, a2 in sorted(rows, reverse=True):
        L.append(f"| {f} | {mu:.4g} | {md:.4g} | {a:.3f} | {a1:.3f} | {a2:.3f} |")
    # minute path before the hour close (relative close), continued vs faded
    L += ["", "## Average path in the 120 minutes before the signal (close relative to the signal close)", "",
          "| minutes before | continued | faded |", "|---|---|---|"]
    for m in (120, 60, 30, 15, 5, 1):
        L.append(f"| {m} | {np.nanmean(seq[up, 0, -m])*100:+.2f}% | {np.nanmean(seq[dn, 0, -m])*100:+.2f}% |")
    L += ["", "## Taker-buy share in the last 10 minutes", "",
          f"continued {np.nanmean(seq[up, 4, -10:]):.3f}, faded {np.nanmean(seq[dn, 4, -10:]):.3f}"]
    (ROOT / "data/reports/b2/pump_anatomy.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
