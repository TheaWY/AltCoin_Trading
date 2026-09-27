"""Shared fold definition for B6 (quarterly expanding walk-forward with purge + embargo)."""
import pandas as pd

D_ = 86400
TEST_STARTS = ["2024-12-01", "2025-03-01", "2025-06-01", "2025-09-01", "2025-12-01", "2026-03-01", "2026-06-01",
               "2026-09-01"]
END = "2026-09-26"


def folds():
    out = []
    for k, s in enumerate(TEST_STARTS):
        t0 = int(pd.Timestamp(s).timestamp())
        t1 = int(pd.Timestamp(TEST_STARTS[k + 1] if k + 1 < len(TEST_STARTS) else END).timestamp())
        cut = t0 - 3 * D_                       # 24h label + 2 day embargo
        va0 = cut - 30 * D_                     # last 30 days before the cut: early stopping + calibration
        tr1 = va0 - 1 * D_                      # purge the 24h label overlap between fit and validation
        out.append(dict(k=k, test0=t0, test1=t1, val0=va0, val1=cut, train1=tr1))
    return out
