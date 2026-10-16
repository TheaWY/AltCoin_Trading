"""B7 EXPLORATORY (not registered): can the strong-rank-IC B7_A factors be monetised with standard portfolio construction?
Variants on the holdout, sign from discovery: (a) registered equal-weight 24h, (b) inverse-vol weights 24h,
(c) inverse-vol weights, 72h staggered hold (9 books). No pass credit; anything promising goes to a forward test.
Out: data/reports/b7/b7_explore_ls.csv"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b7_lib as L  # noqa: E402
from b7_factors import factors  # noqa: E402

A = pd.read_csv(ROOT / "data/reports/b7/b7_A.csv").set_index("factor")
rows = L.eval_rows(L.HOLD)
out = []
for name, F in factors().items():
    s = A.loc[name, "sign"]
    for tag, kw in (("ew24", {}), ("iv24", {"vol_scaled": True}), ("iv72", {"vol_scaled": True, "nbooks": 9})):
        ls = L.long_short(s * F, rows, **kw)
        day = ls.groupby(ls.index // 86400).sum()
        ci = L.boot_ci(day["net"].to_numpy())
        out.append(dict(factor=name, variant=tag, net_day=day["net"].mean(), ci_lo=ci[0], ci_hi=ci[1],
                        gross_day=day["gross"].mean(), hedged_day=day["net_hedged"].mean(), turnover=day["turnover"].mean(),
                        sharpe=day["net"].mean() / day["net"].std() * np.sqrt(365)))
        r = out[-1]
        print(f"{name:24s} {tag} net={r['net_day']:+.5f} [{ci[0]:+.5f},{ci[1]:+.5f}] gross={r['gross_day']:+.5f} "
              f"hedged={r['hedged_day']:+.5f} to={r['turnover']:.2f} sh={r['sharpe']:+.2f}", flush=True)
pd.DataFrame(out).to_csv(ROOT / "data/reports/b7/b7_explore_ls.csv", index=False)
