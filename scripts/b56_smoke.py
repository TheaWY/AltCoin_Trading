"""smoke test for b56 model fitters (random data, no results)."""
import sys
import time

import numpy as np

sys.path.insert(0, "scripts")
order = sys.argv[1:] or ["ridge", "lgb", "mlp"]
import b56_ml as M  # noqa: E402

X = np.random.randn(2000, 40); y = np.random.randn(2000)
for name in order:
    t = time.time()
    f = {"ridge": M.fit_ridge, "lgb": M.fit_lgb, "mlp": M.fit_mlp}[name]
    print(name, f(X, y, X[:10]).shape, round(time.time() - t, 2), flush=True)
