# B22 walk-back of the paper portfolio

All closed trades: 278, total P&L $-245.04, fees $15.57

## By era and strategy

| era | strategy | trades | P&L | wins | worst | fees |
|---|---|---|---|---|---|---|
| jul | failed_pump_short | 8 | +0.18 | 1 | -10.56 | 1.05 |
| jul | funding_rate | 5 | -15.25 | 2 | -11.11 | 0.59 |
| jul | mean_reversion | 5 | -6.40 | 1 | -3.21 | 0.64 |
| jul | mean_reversion_long | 37 | +0.00 | 0 | +0.00 | 0.00 |
| jul | mean_reversion_short | 6 | +0.00 | 0 | +0.00 | 0.00 |
| jul | momentum | 37 | -3.57 | 14 | -24.74 | 8.93 |
| jul | pairs_statarb | 55 | -89.55 | 6 | -62.10 | 0.03 |
| sep | core_btc | 4 | -35.85 | 0 | -20.66 | 2.67 |
| sep | f2_pump_cnn | 7 | -16.44 | 2 | -11.53 | 0.95 |
| sep | pairs_statarb | 31 | -84.69 | 6 | -13.23 | 0.19 |
| sep | signal_xs | 83 | +6.53 | 48 | -6.59 | 0.51 |

## Worst symbols

| symbol | trades | P&L |
|---|---|---|
| DEXE/USDT | 6 | -88.48 |
| AKE/USDT | 5 | -53.23 |
| BTC/USDT | 3 | -32.69 |
| HYPE/USDT | 10 | -18.63 |
| DOT/USDT | 5 | -16.35 |
| ARB/USDT | 11 | -15.71 |
| 1000XEC/USDT | 7 | -11.90 |
| SOON/USDT | 3 | -11.63 |

## Counterfactual risk rules

| rule | P&L | saved | trades removed |
|---|---|---|---|
| R1_cooldown72h | -72.66 | +172.38 | 78 |
| R2_one_open | -197.39 | +47.65 | 8 |
| R3_hedge_cap2x | -188.07 | +56.97 | 8 |
| R4_age60d | -246.18 | -1.14 | 5 |
| R5_losscap10 | -130.01 | +115.03 | 0 |
| ALL_R1_R5 | +7.64 | +252.68 | 86 |
| R0_admission | +0.00 | +245.04 | 278 |
