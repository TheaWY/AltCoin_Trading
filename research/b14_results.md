# B14 results: ML/DL layer on the new data sources (2026-09-28)

Registered in research/batch_B14.yaml before the tensor was built.
- Holdout: 2025-09..2026-09, used once.
- Slice rules: regime (3-state HMM), liquidity tercile, and year thirds.
- Result: 0 of 7 experiments pass. B14_8 (robustness / Deflated Sharpe) was not needed.

| # | Experiment | Holdout result | Verdict |
|---|---|---|---|
| 1 | LambdaRank with 4 nested feature sets | Price-only IC -0.037 (CI [-0.047, -0.026]); full set IC -0.002. The full set's gain over price-only (+0.035, CI > 0) comes only from price-only going negative. Quintile L/S net +0.04%/day, CI crosses 0. | FAIL |
| 1 (secondary) | top-10 long-only | price+positioning+Korean+spot: +0.61%/day (CI [0.13, 1.09]%); full: +0.49%/day (CI [0.06, 0.94]%); price-only +0.34%/day, CI crosses 0. Long-only carries alt beta. | lead only |
| 2 | Meta-labeling v2 (5 primaries) | No primary reaches filter improvement CI > 0. Crash rebound AUC 0.60, improvement +1.25pp, CI crosses 0. | FAIL |
| 3 | Quantile regression q10/q50/q90 | Pinball loss beats the baseline in 4/4 quarters; median-forecast IC 0.128 (CI [0.118, 0.138]); 10-90 coverage 75%. The asymmetric-confidence trade nets ~0.00%/day. | FAIL |
| 4 | GRU + TCN (3 seeds) + tree blend | Pump-in-6h: blend AUC 0.906 vs tree 0.852; PR-AUC 0.095 vs 0.068 (gain CI > 0). Precision in the top 0.5% of scores: 18% vs a 0.5% base rate. Top-5 long -0.14%/24h. Sign of 24h move: AUC 0.52. | FAIL (as a trade) |
| 5 | Regime HMM | 3 states; mean run 6.7 days. The holdout spent 255 of 390 days in state 2. Diagnostic only. | done |
| 6 | IPCA K=2/3/4 | Predictive R2 3.6% vs 2.6% for the observable 4-factor model. Tangency net Sharpe -1.2 to -1.6. | FAIL |
| 7 | PCMCI+ causal lead-lag | 2 links into the alt residual (BTC 1h at lag 24, alt residual at lag 16). Both lose on holdout. | FAIL |

## Findings
1. **Regime flip.** Price/volume factors that ranked coins well in 2024-04..2025-08 rank them *backwards* in 2025-09..2026-09 (IC -0.037, significant). The ranking structure of the market changed. Any single-period model inherits this risk.
2. **Magnitude and timing of pumps are predictable, direction and dollar P&L are not.**
   - The sequence models flag pump-prone coins with AUC 0.91 and 36x lift in the top 0.5%.
   - The quantile model forecasts the typical move (IC 0.13).
   - Neither pays after costs, because the mean is set by rare extremes (same mechanism as B7).
3. **What the models use.** Positioning (OI, L/S, funding) is the most-used new source (18% of model gain). Korean + spot data add 7%. On-chain, depth, calendar and size add 1-4% each.
4. **Lead.** The top-10 long-only picks from the price+positioning+Korean+spot model were positive with CI > 0 and held in all slices. This was a secondary metric and it carries market beta, so it needs its own registration and a forward test before it counts.
