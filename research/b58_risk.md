# B58 Predict risk, not return (prereg v9) — FAIL

BTC/ETH sleeve = F15 trend weight x risk overlay; walk-forward forecasts refit each January; discovery 2019-2023,
holdout 2024-01..2026-10-04. Script scripts/b58_risk.py, numbers research/b58_results.json.

| rule | dev Sharpe | dev maxDD | hold Sharpe | hold maxDD | hold worst 30d |
|---|---|---|---|---|---|
| F17 (benchmark) | 2.06 | -36.3% | 1.44 | -25.9% | -14.2% |
| R1 HAR vol target + brake | 2.08 | -35.5% | 1.35 | -28.3% | -14.3% |
| R2 LGBM vol target + brake | 2.07 | -37.7% | 1.32 | -28.5% | -15.5% |
| R3 F17 + logistic crash cut | 2.12 | -33.9% | 1.54 | -25.2% | -14.2% |
| R4 F17 + LGBM crash cut | 2.05 | -34.9% | 1.47 | -26.4% | -14.9% |
| R5 R1 + logistic crash cut (SELECTED) | 2.14 | -33.8% | 1.44 | -27.6% | -14.3% |

Selected by the committed rule (best dev Sharpe with maxDD not worse than F17): R5.
Holdout: Sharpe diff +0.00 (p=0.49), maxDD 1.7 pts WORSE -> neither PASS branch. FAIL.

## Reading
- Volatility IS forecastable: HAR explains 22% of next-week log-vol in dev (12% holdout) vs 5% / -10% for the
  20-day realised vol F17 uses (QLIKE 0.85 vs 1.00). LightGBM did not beat simple HAR.
- Better vol forecasts did not make a better book. F17's crude vol scaling plus its crash brake already capture
  what matters; refining the vol number moves weights a little and adds turnover.
- Crash probability (next 7d < -10%) is NOT forecastable from these features in dev (AUC 0.45, worse than coin
  flip); holdout AUC 0.63 is likely luck given dev. R3 looked best in the holdout (Sharpe 1.54) but it was not
  the pre-committed pick, and its dev crash model had no skill, so it is not adopted.
- Conclusion: F17 stays. Risk side is already near what simple rules can extract on daily data.
