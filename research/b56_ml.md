# B56: ML/DL weekly prediction (prereg v7)

Run 2026-10-05 18:31, 5s. Rows 13749, OOS from 2019-01-06. F17 Sharpe disc 2.05 / hold 1.44; EW universe 0.66 / -0.67. BHY survivors 0.

| strategy | disc IC (t) | disc Sharpe | vs F17 p | disc CAGR / maxDD | hold IC (t) | hold Sharpe | hold CAGR / maxDD | verdict |
|---|---|---|---|---|---|---|---|---|
| M1_ridge_P | +0.088 (5.7) | 0.86 | 1.000 | +43% / -87% | +0.171 (7.1) | 0.22 | -1% / -67% | - |
| M1_ridge_PG | +0.088 (5.7) | 1.44 | 0.956 | +87% / -44% | +0.171 (7.1) | 0.62 | +17% / -53% | - |
| M2_lgbm_P | +0.075 (5.1) | 0.82 | 0.999 | +38% / -79% | +0.163 (8.0) | 0.16 | -6% / -72% | - |
| M2_lgbm_PG | +0.075 (5.1) | 1.09 | 0.997 | +57% / -47% | +0.163 (8.0) | 0.40 | +8% / -48% | - |
| M3_mlp_P | +0.077 (5.0) | 0.87 | 1.000 | +44% / -84% | +0.155 (7.1) | -0.31 | -25% / -73% | - |
| M3_mlp_PG | +0.077 (5.0) | 1.22 | 0.994 | +69% / -55% | +0.155 (7.1) | -0.28 | -15% / -54% | - |
| M4_lstm_P | +0.078 (5.5) | 0.50 | 1.000 | +6% / -92% | +0.120 (6.5) | 0.10 | -14% / -84% | - |
| M4_lstm_PG | +0.078 (5.5) | 0.83 | 0.999 | +36% / -61% | +0.120 (6.5) | 0.41 | +9% / -57% | - |
| M5_ens_P | +0.107 (7.0) | 1.15 | 0.995 | +82% / -70% | +0.191 (8.7) | 0.30 | +2% / -61% | - |
| M5_ens_PG | +0.107 (7.0) | 1.44 | 0.964 | +90% / -49% | +0.191 (8.7) | 0.46 | +11% / -49% | - |

## Reading (2026-10-05)
- Prediction skill is real and out of sample: weekly rank IC +0.08..+0.11 (2019-23) and +0.12..+0.19 (2024-26), t 5-9; still
  +0.17 among alts only. But it is mostly the low-volatility effect: rv30 alone has IC -0.10 / -0.20, min7 +0.08 / +0.12,
  and models rank BTC/ETH at the top (mean prediction percentile 0.85-0.92; they beat the universe by 1.3-1.7%/wk).
- Long-only top-5 portfolios do not beat F17 (0/10 survive): relative skill does not help when all alts fall together.
- Next: B57 comprehensive programme (prereg v8).
