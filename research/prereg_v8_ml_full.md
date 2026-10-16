# Pre-registration v8 (B57): comprehensive ML/DL programme, long only, Upbit
(written 2026-10-05 19:00 KST, BEFORE building results; code in scripts/b57_*.py is the implementation)

Asked by 유리: a comprehensive, day-or-two ML/DL study instead of quick tests. B56 showed real out-of-sample rank skill
(IC +0.1..+0.19) that did not translate into long-only profits. B57 asks: with more data, more features, tuned models and
three ways of using the predictions, can anything beat F17 (and F19) after strict validation?

## Time split (fixed)
- Tuning window: train 2018-04-01..2021-12-31, inner validation 2022-01-01..2022-12-31 (8-day purge). Hyperparameters
  are chosen here only, then frozen.
- Development OOS: 2023-01-01..2025-06-30, expanding walk-forward, retrain every 13 weeks, frozen hyperparameters.
  Model/usage selection happens here only.
- SEALED holdout: 2025-07-01..2026-10-04. Predictions are produced by the same walk-forward but written to a separate
  sealed file; it is opened exactly once, by scripts/b57_final.py, after the selection in development is committed.
  Caveat: earlier studies (B51-B56) already looked at 2024-26 market behaviour, though never at these models.

## Data and features
Universe each day: BTC, ETH + top-60 alts by 30d median value (>= 180d history), point in time; Upbit KRW daily candles.
Features (all at close t): returns 1..180d, skip-returns, realised vol 7/30/90/180, up/down semivol, skew and kurtosis 30d,
max/min daily return, distance to SMA 20/50/100/200 and 365d high/low, Amihud illiquidity, value level/trend, beta and
correlation to BTC (90d), idiosyncratic vol, 1d autocorrelation 30d, F15 trend weight, market block (BTC/ETH/alt-index
returns and vol, breadth, dispersion), cross-sectional percentile ranks of 12 key features.
Sequence models: last 60 days of (log return, log value change, BTC log return, alt-index log return), window z-scored.
Targets: forward log return over h in {1, 7, 28} days from open t+1, (a) demeaned across the universe (selection) and
(b) raw (timing, BTC/ETH only).

## Models (9) and tuning
Ridge, ElasticNet, LightGBM, XGBoost, CatBoost (Optuna 60 trials each), MLP (25 trials), LSTM, GRU, Transformer encoder
(15 trials each, small search spaces). Objective: mean daily rank IC on the inner validation year. Deep models: 3 seeds
averaged in walk-forward. Decision days: daily for h=1 (tabular models only), every 7th day for h=7, every 28th for h=28.
Plus rank-ensembles (all tabular; all models).

## Uses of the predictions (each model x horizon)
U1 tilt inside F17: keep F17's total exposure; spread it equally over the top-3 predicted among BTC, ETH and the 8 most
   liquid alts. U2 gated top-5: equal-weight top-5 predicted, scaled by the BTC F15 trend weight. U3 timing: BTC/ETH F17
   weights, halved when the raw-target prediction for that coin is negative. Rebalance at the horizon (1/7/28 days).
Costs as v2.

## Selection and tests
Development OOS: every config's daily returns are recorded. Report Sharpe, maxDD vs F17/F19, IC, turnover.
Overfitting control across ALL configs: CSCV probability of backtest overfitting (PBO, S=16) and Deflated Sharpe with
M = all project trials. One config is selected = best development Sharpe among configs with PBO-adjusted rank in the top
decile AND maxDD not worse than F17 by > 5 points; plus the all-model ensemble of the same usage as a second candidate.
Sealed holdout, opened once: PASS = Sharpe(candidate) - Sharpe(F17) > 0 with stationary-bootstrap p < 0.025 (2 candidates)
AND maxDD not worse than F17 by > 5 points. Feature importance (permutation / SHAP for GBMs) and ablations (drop
low-vol block, drop market block, alts-only universe) are reported as interpretation, not tests.

## Sub-study B57x (lower power, reported separately)
Binance perp flow features (taker ratio, funding, OI change) 2025-03..2026-09 hourly for coins listed on both venues,
predicting Upbit 24h relative return; development 2025-03..2025-12, sealed 2026-01..2026-09. Same rules.
