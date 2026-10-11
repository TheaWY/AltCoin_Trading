# B39 F2 pump-CNN loss audit (2026-10-04)

Frozen ensemble replayed on 5728 traded +10%/1h pump events with 1-minute exit paths. Out-of-sample eras: 2024 (before training) and 2026-01..09 (after validation); in-sample 2025-03..12 shown for reference only. Net = per-trade return after round-trip fee+slippage (stop fills pay one extra slippage leg).

## Baseline by era

| era | n | mean | median | win | p10 | worst | maxDD (sum of nets) |
|---|---|---|---|---|---|---|---|
| 2024 | 721 | +0.68% | +0.79% | 55% | -9.3% | -62.0% | 1.31 |
| insample | 2325 | +1.32% | +2.12% | 59% | -15.1% | -224.5% | 6.15 |
| 2026 | 2682 | +0.46% | +0.65% | 52% | -16.5% | -150.8% | 5.85 |

## Exit rules (paired vs baseline, same trades)

| rule | 2024 mean | 2024 Δ | 2026 mean | 2026 Δ | 2026 Δ 95% CI | 2026 p10 | 2026 worst | 2026 maxDD | robust |
|---|---|---|---|---|---|---|---|---|---|
| baseline 4h | +0.68% | +0.00% | +0.46% | +0.00% | [+0.00%, +0.00%] | -16.5% | -150.8% | 5.85 |  |
| hold 1h | -0.05% | -0.73% | -0.09% | -0.55% | [-1.04%, -0.07%] | -10.3% | -123.5% | 8.19 |  |
| hold 2h | +0.49% | -0.20% | +0.08% | -0.38% | [-0.79%, +0.01%] | -12.7% | -135.8% | 9.87 |  |
| stop 3% intrabar | +0.12% | -0.56% | -0.24% | -0.70% | [-1.24%, -0.16%] | -3.5% | -3.7% | 8.89 |  |
| stop 5% intrabar | -0.00% | -0.69% | -0.23% | -0.69% | [-1.20%, -0.18%] | -5.5% | -5.7% | 8.86 |  |
| stop 8% intrabar | +0.13% | -0.55% | -0.10% | -0.56% | [-1.01%, -0.11%] | -8.5% | -8.7% | 8.06 |  |
| stop 12% intrabar | +0.35% | -0.33% | -0.03% | -0.49% | [-0.84%, -0.13%] | -12.3% | -12.7% | 6.53 |  |
| stop 20% intrabar | +0.59% | -0.09% | +0.16% | -0.30% | [-0.60%, +0.02%] | -20.2% | -20.7% | 6.18 |  |
| stop 5% on close | -0.06% | -0.74% | -0.00% | -0.46% | [-0.93%, +0.00%] | -7.3% | -26.1% | 6.16 |  |
| stop 8% on close | +0.20% | -0.48% | +0.03% | -0.43% | [-0.84%, -0.01%] | -9.9% | -26.1% | 5.28 |  |
| stop 12% on close | +0.52% | -0.16% | +0.12% | -0.34% | [-0.67%, -0.00%] | -13.5% | -29.0% | 4.96 |  |
| take-profit 8% | +0.46% | -0.22% | +0.17% | -0.29% | [-0.72%, +0.11%] | -13.6% | -150.8% | 5.03 |  |
| take-profit 15% | +0.59% | -0.09% | +0.41% | -0.05% | [-0.41%, +0.29%] | -15.3% | -150.8% | 4.45 |  |
| take-profit 25% | +0.63% | -0.05% | +0.58% | +0.12% | [-0.14%, +0.41%] | -15.9% | -150.8% | 5.01 |  |
| trail arm 5% give-back 5% | +0.55% | -0.13% | +0.05% | -0.41% | [-0.79%, -0.05%] | -12.4% | -150.8% | 4.75 |  |
| trail arm 10% give-back 7% | +0.67% | -0.01% | +0.28% | -0.18% | [-0.51%, +0.15%] | -14.8% | -150.8% | 5.48 |  |
| trail arm 15% give-back 10% | +0.87% | +0.19% | +0.61% | +0.15% | [-0.13%, +0.45%] | -15.5% | -150.8% | 4.03 |  |
| stop 8% + TP 15% | +0.13% | -0.55% | -0.07% | -0.53% | [-1.03%, -0.04%] | -8.5% | -8.7% | 6.12 |  |
| stop 12% + TP 25% | +0.31% | -0.37% | +0.07% | -0.39% | [-0.81%, +0.06%] | -12.3% | -12.7% | 5.07 |  |

## Entry filters (baseline exit; cut point = 2024-era median, then applied unchanged to 2026)

| filter | 2024 kept n | 2024 kept mean | 2024 dropped mean | 2026 kept n | 2026 kept mean | 2026 dropped mean | 2026 kept-minus-all 95% CI | robust |
|---|---|---|---|---|---|---|---|---|
| longs only | 357 | +0.45% | +0.91% | 1183 | +0.28% | +0.60% | [-0.98%, +0.63%] |  |
| shorts only | 364 | +0.91% | +0.45% | 1499 | +0.60% | +0.28% | [-0.50%, +0.75%] |  |
| margin >= 0.023 | 361 | +0.90% | +0.46% | 1373 | +0.73% | +0.17% | [-0.35%, +0.90%] |  |
| ldv >= 18.9 | 361 | +1.64% | -0.28% | 823 | +0.10% | +0.62% | [-1.40%, +0.64%] |  |
| ldv < 18.9 | 360 | -0.28% | +1.64% | 1859 | +0.62% | +0.10% | [-0.29%, +0.62%] |  |
| ret_1h < 0.124 | 360 | +0.60% | +0.77% | 1008 | +0.31% | +0.55% | [-0.82%, +0.55%] |  |
| ret_1h >= 0.124 | 361 | +0.77% | +0.60% | 1674 | +0.55% | +0.31% | [-0.33%, +0.49%] |  |
| rv24 < 0.142 | 360 | +0.47% | +0.89% | 776 | +0.06% | +0.62% | [-1.31%, +0.60%] |  |
| pumps_30d < 1 | 275 | +0.70% | +0.67% | 620 | +0.33% | +0.50% | [-1.01%, +0.75%] |  |
| btc_ret_1h < 0.00241 | 360 | +0.63% | +0.73% | 2026 | +0.42% | +0.57% | [-0.40%, +0.37%] |  |
| btc_ret_1h >= 0.00241 | 361 | +0.73% | +0.63% | 656 | +0.57% | +0.42% | [-1.12%, +1.22%] |  |
| upwick_1h < 0.118 | 360 | +0.04% | +1.32% | 1187 | +0.10% | +0.74% | [-0.98%, +0.27%] |  |
| fund24 < 0.0001 | 274 | +0.21% | +0.97% | 1266 | +0.01% | +0.86% | [-1.10%, +0.22%] |  |
| dhi30 < -0.11 | 307 | +0.32% | +0.95% | 1361 | +0.57% | +0.35% | [-0.48%, +0.71%] |  |
| taker_1h >= 0.518 | 361 | +0.75% | +0.62% | 1085 | +0.70% | +0.29% | [-0.54%, +1.00%] |  |

Robust exit rules (improve both OOS eras, 2026 paired CI > 0): none.
Runtime 86s.

## Reading (2026-10-04)

- F2's out-of-sample edge is about +0.5-0.7% per trade (2024 +0.68%, 2026 +0.46%), not the +2.6% of the first 48 live trades.
- No stop helps. Every fixed stop from 3% to 50% (intrabar or on close) lowers the mean in 2026; pumped coins whipsaw and most stopped trades would have recovered by the 4h exit.
- Losses come from a fat tail: 6% of trades lose more than 20% and together cost -69 units against a total of +17. Most are shorts into squeezes (worst -151% AKE, -147% TAIKO). This is a sizing problem, not an exit problem.
- Inverse-volatility sizing is worse in both eras.
- Consistent in both eras but not significant: trailing exit (arm +15%, give back 10%) +0.19% / +0.15% per trade and lower drawdown (4.03 vs 5.85); high-confidence filter (CNN margin >= 2024 median) +0.90% / +0.73% kept vs +0.46% / +0.17% dropped; combined +1.00% / +0.59%.
- Candidate F2b (shadow forward test, not applied to the main book): margin filter + trail 15/10.
