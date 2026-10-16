"""B6 SHAP: what drives P(loss) in the last LightGBM fold, on its own out-of-sample rows (tree process, no torch)."""
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lossml_data import FEATURES  # noqa: E402
from lossml_folds import folds  # noqa: E402

f = folds()[6]
T = pd.read_parquet(ROOT / "data/cache/lossml_tab.parquet", columns=["ts"] + FEATURES)
X = T.loc[(T["ts"] >= f["test0"]) & (T["ts"] < f["test1"]), FEATURES].sample(60000, random_state=1)
C = lgb.Booster(model_file=str(ROOT / "data/models/lossml/lgb_f6.txt")).predict(X.to_numpy(np.float32), pred_contrib=True)[:, :-1]
rows = []
for j, c in enumerate(FEATURES):
    x = X[c].to_numpy(float)
    ok = np.isfinite(x)
    rows.append(dict(feature=c, mean_abs_shap=np.abs(C[:, j]).mean(),
                     direction=np.corrcoef(x[ok], C[ok, j])[0, 1] if ok.sum() > 100 and x[ok].std() > 0 else np.nan))
S = pd.DataFrame(rows).sort_values("mean_abs_shap", ascending=False).round(4)
S.to_csv(ROOT / "data/reports/b6/shap_lgb_f6.csv", index=False)
print(S.head(15).to_string())
