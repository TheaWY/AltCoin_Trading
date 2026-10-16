# B7 design: how quant researchers discover new variables, and what we do better (2026-09-27)

Registration: research/batch_B7.yaml (committed before any factor is computed).

## What the field does (survey, 2020-2026)
- **Formula alpha mining.**
  - Methods: GP (gplearn), AlphaGen (RL, reward = marginal IC gain of a factor pool, KDD 2023, arXiv 2306.12964), AlphaForge (AAAI 2025, rolling re-weighting, arXiv 2406.18394), AlphaAgent (LLM idea/factor/eval agents with an originality penalty, KDD 2025, arXiv 2502.16789), FactorMiner (2026, arXiv 2602.14670).
  - FactorMiner is the only paper in this family tested on crypto: 64 Binance coins at 10-minute bars. OOS IC was 3.8% for FactorMiner, 2.9% for AlphaAgent and 2.5% for GP. It modelled no costs.
  - Typical flaws: one fixed train/test split, IC-only fitness, no costs, no multiple-testing control.
- **Deep residual stat-arb** (Guijarro-Ordonez, Pelger, Zanotti, Management Science; arXiv 2106.04028).
  - Residuals from PCA/IPCA factor models feed a CNN + Transformer allocator trained to maximise Sharpe directly.
  - US daily OOS Sharpe: 3.4-4.2 before costs.
- **Lead-lag.**
  - Methods: Levy-area / signature lead-lag networks (Bennett, Cucuringu, Reinert 2022, arXiv 2201.08283) and lagged cross-asset order-flow imbalance (Cont, Cucuringu, Zhang 2023, arXiv 2112.13213).
  - In crypto, BTC-to-alt lags at the minute level die within about 3 minutes. At 1h and longer, only aggregated or slow lead-lag survives (Guo et al. JEDC 2024).
- **New crypto variables.**
  - Quarter-hour boundary order flow on Binance perps predicts returns 4-12h ahead OOS (arXiv 2607.09426).
  - Funding and carry predict crashes (BIS WP 1087).
  - OI and L/S ratios flip out of sample: use them only as conditioning variables (arXiv 2607.27070).
- **Foundation models** (Kronos, TS2Vec): weak for returns. An independent pre-registered test found Kronos 81% worse than a random walk on CRPS. Not used.

## Our improvements over the published protocols
- **Target.** BTC-beta-neutral 24h return, cross-sectionally ranked, on a tradable universe (>= $5M/day).
- **Validation.** Discovery 2024-04..2025-08, then a single untouched holdout 2025-09..2026-09. Purged folds inside discovery.
- **Cost-aware scoring.** A turnover penalty in the GP fitness; the final test is a net quintile long-short including actual funding.
- **Novelty.** Every factor must keep a positive partial IC after known factors (reversal, momentum, vol, liquidity, funding, MAX) are removed.
- **Pool decorrelation.** Maximum |rho| is 0.5 against the pool and against the known factors (AlphaGen/AlphaForge idea).
- **LLM leakage guard.** The mechanism factors (B7_A) are frozen in git before any data is touched.
- **Multiple testing.** Every GP formula is counted. Benjamini-Hochberg is applied across all factors sent to the holdout.
- **Joint value.** LightGBM LambdaRank run with and without the new variables shows whether they add information together.
- **Deep stat-arb adaptations.** PCA is fitted strictly on past data; the objective includes turnover cost; rebalancing is every 4h.
