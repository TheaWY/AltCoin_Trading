"""B57 model zoo with Optuna search spaces (prereg v8). Each fit(params, Xtr, ytr, Xte) -> predictions.
Tabular models get 2D arrays; sequence models (lstm/gru/transformer) get (n, 60, 4) arrays."""
from __future__ import annotations

import numpy as np

SEQ_MODELS = {"lstm", "gru", "transformer"}
TRIALS = {"ridge": 30, "enet": 40, "lgbm": 60, "xgb": 60, "cat": 40, "mlp": 25, "lstm": 15, "gru": 15, "transformer": 15}


def space(name, t):
    if name == "ridge":
        return {"alpha": t.suggest_float("alpha", 1e-2, 1e3, log=True)}
    if name == "enet":
        return {"alpha": t.suggest_float("alpha", 1e-5, 1e-1, log=True), "l1_ratio": t.suggest_float("l1_ratio", 0.05, 0.95)}
    if name == "lgbm":
        return {"n_estimators": t.suggest_int("n_estimators", 100, 800), "learning_rate": t.suggest_float("lr", 0.01, 0.1, log=True),
                "num_leaves": t.suggest_int("num_leaves", 7, 63), "min_child_samples": t.suggest_int("mcs", 20, 300),
                "subsample": t.suggest_float("subsample", 0.5, 1.0), "colsample_bytree": t.suggest_float("colsample", 0.3, 1.0),
                "reg_lambda": t.suggest_float("reg_lambda", 1e-3, 10, log=True)}
    if name == "xgb":
        return {"n_estimators": t.suggest_int("n_estimators", 100, 800), "learning_rate": t.suggest_float("lr", 0.01, 0.1, log=True),
                "max_depth": t.suggest_int("max_depth", 2, 8), "min_child_weight": t.suggest_float("mcw", 1, 50, log=True),
                "subsample": t.suggest_float("subsample", 0.5, 1.0), "colsample_bytree": t.suggest_float("colsample", 0.3, 1.0),
                "reg_lambda": t.suggest_float("reg_lambda", 1e-3, 10, log=True)}
    if name == "cat":
        return {"iterations": t.suggest_int("iterations", 200, 800), "learning_rate": t.suggest_float("lr", 0.01, 0.1, log=True),
                "depth": t.suggest_int("depth", 3, 8), "l2_leaf_reg": t.suggest_float("l2", 1, 10, log=True)}
    if name == "mlp":
        return {"hidden": t.suggest_int("hidden", 32, 256, log=True), "layers": t.suggest_int("layers", 1, 3),
                "dropout": t.suggest_float("dropout", 0.0, 0.5), "lr": t.suggest_float("lr", 1e-4, 3e-3, log=True),
                "wd": t.suggest_float("wd", 1e-6, 1e-2, log=True), "epochs": t.suggest_int("epochs", 10, 40)}
    if name in ("lstm", "gru"):
        return {"hidden": t.suggest_int("hidden", 16, 64, log=True), "dropout": t.suggest_float("dropout", 0.0, 0.4),
                "lr": t.suggest_float("lr", 3e-4, 3e-3, log=True), "epochs": t.suggest_int("epochs", 5, 20)}
    if name == "transformer":
        return {"d_model": t.suggest_categorical("d_model", [16, 32, 64]), "heads": t.suggest_categorical("heads", [2, 4]),
                "layers": t.suggest_int("layers", 1, 2), "lr": t.suggest_float("lr", 3e-4, 3e-3, log=True),
                "epochs": t.suggest_int("epochs", 5, 20)}
    raise KeyError(name)


def fit(name, p, Xtr, ytr, Xte, seed=1):
    if name == "ridge":
        from sklearn.linear_model import Ridge
        return Ridge(alpha=p["alpha"]).fit(Xtr, ytr).predict(Xte)
    if name == "enet":
        from sklearn.linear_model import ElasticNet
        return ElasticNet(alpha=p["alpha"], l1_ratio=p["l1_ratio"], max_iter=5000).fit(Xtr, ytr).predict(Xte)
    if name == "lgbm":
        import lightgbm as lgb
        return lgb.LGBMRegressor(**p, subsample_freq=1, verbose=-1, random_state=seed, n_jobs=8).fit(Xtr, ytr).predict(Xte)
    if name == "xgb":
        import xgboost as xgb
        return xgb.XGBRegressor(**p, random_state=seed, n_jobs=8, tree_method="hist").fit(Xtr, ytr).predict(Xte)
    if name == "cat":
        from catboost import CatBoostRegressor
        return CatBoostRegressor(**p, random_seed=seed, verbose=0, thread_count=8).fit(Xtr, ytr).predict(Xte)
    return _torch(name, p, Xtr, ytr, Xte, seed)


def _net(name, p, k):
    import torch.nn as nn
    if name == "mlp":
        layers, d = [], k
        for _ in range(p["layers"]):
            layers += [nn.Linear(d, p["hidden"]), nn.ReLU(), nn.Dropout(p["dropout"])]; d = p["hidden"]
        return nn.Sequential(*layers, nn.Linear(d, 1))
    if name in ("lstm", "gru"):
        class R(nn.Module):
            def __init__(self):
                super().__init__()
                cell = nn.LSTM if name == "lstm" else nn.GRU
                self.rnn = cell(k, p["hidden"], batch_first=True); self.dp = nn.Dropout(p["dropout"]); self.head = nn.Linear(p["hidden"], 1)

            def forward(self, x):
                o, _ = self.rnn(x)
                return self.head(self.dp(o[:, -1]))
        return R()

    class T(nn.Module):
        def __init__(self):
            super().__init__()
            self.inp = nn.Linear(k, p["d_model"])
            layer = nn.TransformerEncoderLayer(p["d_model"], p["heads"], dim_feedforward=2 * p["d_model"], dropout=0.1, batch_first=True)
            self.enc = nn.TransformerEncoder(layer, p["layers"]); self.head = nn.Linear(p["d_model"], 1)

        def forward(self, x):
            return self.head(self.enc(self.inp(x))[:, -1])
    return T()


def _torch(name, p, Xtr, ytr, Xte, seed):
    import torch
    torch.set_num_threads(8); torch.manual_seed(seed); np.random.seed(seed)
    k = Xtr.shape[-1]; net = _net(name, p, k)
    opt = torch.optim.Adam(net.parameters(), lr=p["lr"], weight_decay=p.get("wd", 1e-5))
    X = torch.tensor(Xtr, dtype=torch.float32); y = torch.tensor(ytr, dtype=torch.float32); n = len(X)
    for _ in range(p["epochs"]):
        net.train(); perm = torch.randperm(n)
        for b0 in range(0, n, 256):
            b = perm[b0:b0 + 256]
            opt.zero_grad(); loss = torch.nn.functional.mse_loss(net(X[b]).squeeze(-1), y[b]); loss.backward(); opt.step()
    net.eval(); out = []
    with torch.no_grad():
        Xt = torch.tensor(Xte, dtype=torch.float32)
        for b0 in range(0, len(Xt), 4096):
            out.append(net(Xt[b0:b0 + 4096]).squeeze(-1).numpy())
    return np.concatenate(out) if out else np.zeros(0)
