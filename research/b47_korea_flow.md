# B47 / U6: Upbit share and Korea-led vs Binance-led flows (long only)

Upbit KRW hourly + Binance spot hourly. Net after Upbit fees + slippage. Eras train 2024-01..2025-06, val 2025-07..12, TEST 2026. Best-validation variant per family judged once on TEST. Mirror = short side after costs (not executable).

## K_share

| variant | train n / mean | val n / mean | test n / mean | test mirror |
|---|---|---|---|---|
| top decile share surge, hold 1d | 377 / -0.29% | 184 / -0.70% | 276 / -0.76% | +0.29% |
| bottom decile share surge, hold 1d | 377 / -0.20% | 184 / -0.74% | 276 / -0.40% | -0.18% |
| top decile share level (Korea-heavy), hold 1d | 387 / -0.64% | 184 / -1.02% | 276 / -0.84% | +0.45% |
| bottom decile share level (Binance-heavy), hold 1d | 387 / -0.02% | 184 / -0.24% | 276 / -0.05% | -0.19% |
| benchmark: all coins with a Binance pair, EW hold 1d | 387 / -0.10% | 184 / -0.38% | 276 / -0.24% | +0.09% |

## K_lag

| variant | train n / mean | val n / mean | test n / mean | test mirror |
|---|---|---|---|---|
| Binance +3%/1h, Upbit lags >= 2% -> buy, hold 1h | 194 / +0.29% | 225 / -0.97% | 275 / -0.97% | +0.43% |
| Binance +3%/1h, Upbit lags >= 2% -> buy, hold 4h | 184 / +0.26% | 202 / -1.24% | 232 / -2.79% | +2.24% |
| Binance +3%/1h, Upbit lags >= 2% -> buy, hold 24h | 164 / +0.94% | 165 / -3.12% | 170 / -6.88% | +6.31% |
| Binance +3%/1h, Upbit lags >= 4% -> buy, hold 1h | 60 / +0.25% | 128 / -0.82% | 159 / -1.56% | +1.05% |
| Binance +3%/1h, Upbit lags >= 4% -> buy, hold 4h | 58 / -0.10% | 118 / -1.05% | 130 / -3.89% | +3.37% |
| Binance +3%/1h, Upbit lags >= 4% -> buy, hold 24h | 51 / -1.16% | 102 / -2.23% | 100 / -8.06% | +7.53% |
| Binance +5%/1h, Upbit lags >= 2% -> buy, hold 1h | 115 / +0.10% | 127 / -1.00% | 185 / -0.91% | +0.41% |
| Binance +5%/1h, Upbit lags >= 2% -> buy, hold 4h | 111 / -0.15% | 115 / -1.54% | 160 / -3.47% | +2.96% |
| Binance +5%/1h, Upbit lags >= 2% -> buy, hold 24h | 101 / +0.70% | 100 / -3.74% | 122 / -8.11% | +7.56% |
| Binance +5%/1h, Upbit lags >= 4% -> buy, hold 1h | 42 / -0.51% | 87 / -0.61% | 125 / -1.31% | +0.81% |
| Binance +5%/1h, Upbit lags >= 4% -> buy, hold 4h | 41 / -1.39% | 81 / -0.90% | 105 / -4.03% | +3.52% |
| Binance +5%/1h, Upbit lags >= 4% -> buy, hold 24h | 36 / -2.41% | 72 / -2.68% | 79 / -8.80% | +8.26% |

## K_lead

| variant | train n / mean | val n / mean | test n / mean | test mirror |
|---|---|---|---|---|
| Upbit +5%/1h, Binance <= +1% -> buy, hold 1h | 87 / +4.46% | 111 / -1.52% | 68 / -1.24% | +0.66% |
| Upbit +5%/1h, Binance <= +1% -> buy, hold 4h | 87 / +7.19% | 97 / -3.96% | 64 / -4.62% | +4.04% |
| Upbit +5%/1h, Binance <= +1% -> buy, hold 24h | 87 / +17.09% | 70 / -5.86% | 53 / -7.29% | +6.68% |

## K_prem

| variant | train n / mean | val n / mean | test n / mean | test mirror |
|---|---|---|---|---|
| premium fell >= 5% in 24h -> buy, hold 24h | 145 / +35.17% | 309 / +1.15% | 182 / -2.91% | +2.31% |
| premium fell >= 5% in 24h -> buy, hold 72h | 138 / +31.33% | 218 / +1.21% | 131 / -5.58% | +4.98% |
| premium fell >= 8% in 24h -> buy, hold 24h | 102 / +48.67% | 253 / +1.15% | 124 / -3.59% | +3.03% |
| premium fell >= 8% in 24h -> buy, hold 72h | 99 / +43.56% | 172 / +2.07% | 88 / -6.44% | +5.88% |

## Verdicts

| family | chosen variant | val mean | test n | test mean | test 95% CI | verdict |
|---|---|---|---|---|---|---|
| K_share | bottom decile share level (Binance-heavy), hold 1d | -0.24% | 276 | -0.05% | [-0.34%, +0.26%] | **FAIL** |
| K_lag | Binance +5%/1h, Upbit lags >= 4% -> buy, hold 1h | -0.61% | 125 | -1.31% | [-2.24%, -0.39%] | **FAIL** |
| K_lead | Upbit +5%/1h, Binance <= +1% -> buy, hold 1h | -1.52% | 68 | -1.24% | [-2.50%, +0.19%] | **FAIL** |
| K_prem | premium fell >= 8% in 24h -> buy, hold 72h | +2.07% | 88 | -6.44% | [-9.98%, -2.73%] | **FAIL** |

Runtime 4s.

## Reading (2026-10-04)

- 4/4 FAIL. Nothing in Upbit-vs-Binance flow gives a long edge on Upbit.
- Train-era +17 to +49% in K_lead/K_prem is the martial-law night (2024-12-03: 77 of 102 premium trades, 76 of 87 Korea-led trades). Without that day: premium fell >=8% -> +1.2% mean, median 0; Korea-led -6.3%. A one-off, not a signal.
- Binance pumps while Upbit lags: buying the Upbit catch-up LOSES (TEST -1 to -9%, worse the longer you hold). Mirror +0.4 to +8.3% needs a short. Avoid rule: do not buy an Upbit coin that lags a Binance pump.
- Korea-heavy coins (high Upbit share) underperform the benchmark by about 0.6%/day in every era; Binance-heavy coins are about flat. Mild avoid tilt, not tradeable long.
- Look-ahead: all features at hour i close, entry at open of i+1 (B43 helpers); premium uses same-hour closes on both venues.
