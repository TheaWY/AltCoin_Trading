# B24_REGIME: predict bad days/weeks and pick the strategy (OOS 2025-01..2026-09)

## H1 (daily rebalance)

| selector | bp/day [CI] | Sharpe | 2026 bp/day | 2026 Sharpe | pass |
|---|---|---|---|---|---|
| EW | -11.4 [-15.6, -7.0] | -4.02 | -14.6 | -4.24 |  |
| WINNER | -15.2 [-74.8, +43.0] | -0.39 | -32.3 | -0.81 |  |
| RIDGE | -20.6 [-72.1, +30.5] | -0.60 | -33.2 | -0.98 | False |
| LGBM | -1.2 [-69.5, +63.0] | -0.03 | -12.5 | -0.30 | False |
| GATE | -5.0 [-7.8, -2.2] | -2.76 | -6.7 | -3.12 | False |

Single strategies: MOM -3.7bp (S -0.51), REV -18.3bp (S -2.84), LOWVOL -14.1bp (S -1.67), VSH +14.3bp (S 1.34), BTC -0.2bp (S -0.01), PFOLLOW -28.8bp (S -0.60), PFADE -28.7bp (S -0.59)
Picks: {'RIDGE': {'PFADE': 246, 'BTC': 116, 'PFOLLOW': 115, 'VSH': 82, 'REV': 29, 'MOM': 29, 'LOWVOL': 10, 'CASH': 4}, 'LGBM': {'PFADE': 223, 'PFOLLOW': 169, 'BTC': 115, 'VSH': 47, 'REV': 26, 'LOWVOL': 25, 'MOM': 22, 'CASH': 4}}
Gate: cash 63% of periods, AUC for 'EW loses next period' 0.49955263322166504
Per-strategy AUC (forecast vs realised win/lose): RIDGE: MOM 0.50, REV 0.51, LOWVOL 0.54, VSH 0.53, BTC 0.50, PFOLLOW 0.50, PFADE 0.51; LGBM: MOM 0.50, REV 0.53, LOWVOL 0.54, VSH 0.56, BTC 0.45, PFOLLOW 0.50, PFADE 0.51

## H7 (weekly rebalance)

| selector | bp/day [CI] | Sharpe | 2026 bp/day | 2026 Sharpe | pass |
|---|---|---|---|---|---|
| EW | -11.4 [-15.7, -7.1] | -4.02 | -14.6 | -4.24 |  |
| WINNER | -32.1 [-90.7, +25.1] | -0.83 | -59.9 | -1.54 |  |
| RIDGE | +0.1 [-53.7, +59.4] | 0.00 | -41.9 | -1.44 | False |
| LGBM | -9.9 [-64.7, +44.0] | -0.27 | -33.2 | -0.82 | False |
| GATE | -0.9 [-2.8, +1.0] | -0.68 | -0.8 | -0.60 | False |

Single strategies: MOM -3.7bp (S -0.51), REV -18.3bp (S -2.84), LOWVOL -14.1bp (S -1.67), VSH +14.3bp (S 1.34), BTC -0.2bp (S -0.01), PFOLLOW -28.8bp (S -0.60), PFADE -28.7bp (S -0.59)
Picks: {'RIDGE': {'PFOLLOW': 21, 'BTC': 20, 'PFADE': 18, 'VSH': 15, 'MOM': 8, 'LOWVOL': 5, 'REV': 4}, 'LGBM': {'PFOLLOW': 29, 'PFADE': 23, 'BTC': 18, 'VSH': 17, 'MOM': 4}}
Gate: cash 79% of periods, AUC for 'EW loses next period' 0.5516826923076923
Per-strategy AUC (forecast vs realised win/lose): RIDGE: MOM 0.50, REV 0.47, LOWVOL 0.56, VSH 0.54, BTC 0.45, PFOLLOW 0.43, PFADE 0.39; LGBM: MOM 0.53, REV 0.55, LOWVOL 0.60, VSH 0.56, BTC 0.47, PFOLLOW 0.50, PFADE 0.51

