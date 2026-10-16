# Loss-prediction ML system (batch B6): design, registered 2026-09-27 before any run

## Question
Which trades will lose? The goal is a calibrated P(loss) for any (coin, direction, entry time). It is used to skip or downsize trades. The real test is the 263 paper trades the system actually took (125 closed with a real exit, Jul-Sep 2026).

## What the field uses (evidence summary, see sources)
- **Gradient-boosted trees (LightGBM / CatBoost)** have the strongest record on noisy financial tables. They were used by the G-Research crypto Kaggle top 3 (2022) and by the Optiver 2023 winner (CatBoost base).
- **Small MLP / GRU networks** do well when retrained often and blended with the trees. Examples: Jane Street 2021 winner (autoencoder + MLP), Jane Street 2024 2nd place (GRU + online updates), Optiver 2023 winner (CatBoost + GRU + Transformer blend).
- **Transformers and time-series foundation models (Chronos, TimesFM, Moirai)** have weak evidence on returns. Rahimikia et al. 2025 found them poor zero-shot and fine-tuned. Not used.
- **Labeling / validation standard (López de Prado):**
  - triple-barrier labels
  - meta-labeling: a secondary model predicts whether a primary signal's trade loses
  - purged, embargoed walk-forward validation
  - sample-uniqueness weights
  - SHAP importance on out-of-sample folds
  - calibrated probabilities used for bet sizing
- **Evidence for meta-labeling** is real but modest (e.g. Hudson & Thames: trend-following precision 0.48 to 0.54 out of sample). The gains come mostly from taking fewer, better trades.

## Data and labels
- 30 months of Binance USDT perps (2024-03..2026-09), delisted included, 24h volume >= $2M, >= 72h listed. Entry times sampled at 00/08/16 UTC, both directions.
- **Features:**
  - direction-signed (multiplied by +1 long / -1 short): returns 1h..28d, taker imbalance 1h/24h, distance to 30d high/low, funding (positive = this trade pays), BTC 1h/24h
  - unsigned: vol 24h/7d, max hourly move 7d, liquidity, volume surge, last-hour range and upper wick, prior pumps, age, alt breadth, hour/weekday
  - cross-sectional ranks of 24h return and vol
  - GRU only: the last 48 hourly bars
- **Label A (primary):** loss24. Net after fees, volume slippage and funding over a 24h hold is below zero. This matches how the system holds (signal_xs rebalances every 24h without stops).
- **Label B:** triple barrier. Stop at -1 x the 24h vol, target at +1.5 x the 24h vol, limit 24h. Loss = stop hit first, or negative at the time limit.
- **Weights:** each row weighted by 1 / (number of overlapping rows for the same coin), for label uniqueness.

## Models and validation
- **Walk-forward:** expanding window, retrained every quarter. First test quarter starts 2024-12. A 2-day purge/embargo sits between train and test. The last 30 days of each train window are used for early stopping and calibration.
- **Models:** LightGBM, CatBoost, MLP (tabular) and GRU (48h sequence). Each is calibrated (isotonic on the validation window). The blend is the plain average of calibrated probabilities; no tuning of weights.
- All test predictions are strictly out-of-sample.

## Registered tests and pass rules
- **B6_1 discrimination:** out-of-sample AUC of P(loss) on loss24, 2024-12..2026-09. Pass if AUC > 0.55 with a day-block bootstrap CI above 0.53. Each single model's AUC is reported next to the blend.
- **B6_2 calibration:** reliability. Report only.
- **B6_3 meta-label filter on the research primaries** (B2 pump fade short, pump follow long, 30d breakout long, crash rebound long, all events in the out-of-sample window): skipping trades with P(loss) above the primary's median must raise the mean net per trade. Pass if the improvement is > 0 with a day-block bootstrap CI above 0 in at least 3 of 4 primaries.
- **B6_4 the real paper trades:** AUC of P(loss) against the realised sign of the 125 real-exit paper trades. Each is scored by the fold model trained before its entry date. Report the AUC with bootstrap CI and the P&L if trades with P(loss) > 0.6 had been skipped. Only 125 trades, so this is indicative, not a pass/fail.

## Sources
- G-Research crypto competition wrap-up: https://www.gresearch.com/news/wrapping-up-the-g-research-crypto-forecasting-competition/
- Optiver 2023 1st place: https://www.kaggle.com/competitions/optiver-trading-at-the-close/writeups/hyd-1st-place-solution
- Jane Street 2021 1st place: https://www.kaggle.com/competitions/jane-street-market-prediction/writeups/cats-trading-yirun-s-solution-1st-place-training-s
- Rahimikia et al. 2025, foundation models in finance: https://arxiv.org/abs/2511.18578
- Hudson & Thames, meta-labeling test: https://hudsonthames.org/does-meta-labeling-add-to-signal-efficacy-triple-barrier-method/
- Triple barrier + CUSUM on crypto (Financial Innovation 2025): https://link.springer.com/article/10.1186/s40854-025-00866-w
