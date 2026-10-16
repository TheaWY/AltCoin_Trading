# Pre-registration v7: machine-learning / deep-learning return prediction, long only, Upbit daily
(written 2026-10-05 18:40 KST, BEFORE running; scripts/b56_ml.py is the exact implementation)

Asked by 유리: "find other things that can go up - do quant ML/DL properly". Prior evidence: B42 (LightGBM on Upbit pumps)
and the AR/ctrend batches (2024-26 hourly) failed. This study differs: weekly horizon, 2017-2026 daily data, a model zoo
with fixed hyperparameters, purged walk-forward, and judgment against F17 (what 유리 holds).

## Data and target
Universe each Sunday close t: BTC, ETH + top-30 alts by 30d median value (>= 180d history), point in time.
Target: log(open[t+8] / open[t+1]) (Monday open to next Monday open), demeaned across the universe that week.
Features at close t (no future data): log returns 1/3/7/14/28/56/90/180d; realised vol 7/30/90d and ratios; max and min
daily return 7/30d; distance to SMA 20/50/200 and to the 365d high/low; log 30d median value, value 7d/90d ratio;
F15 trend weight; market block (BTC 7d/28d return, BTC trend weight, alt-index 7d return and 20d vol, breadth = share of
universe above SMA50); cross-sectional percentile ranks of 7 key features. Sequence models get the last 60 days of
(daily log return, log value change, BTC daily log return), z-scored per coin on the window.

## Models (hyperparameters fixed here, no tuning)
M1 Ridge (alpha 10, standardised). M2 LightGBM (300 trees, lr 0.03, 15 leaves, min 50 per leaf, 0.8 row/col sampling).
M3 MLP (64-32, dropout 0.2, Adam 1e-3, weight decay 1e-4, 30 epochs, 3 seeds averaged). M4 LSTM (hidden 32, 1 layer,
15 epochs, 3 seeds). M5 = mean of the cross-sectional ranks of M1-M4.

## Walk-forward
Retrain every 13 weeks on all rows whose target window ended before the retrain date (8-day purge), expanding window.
Out-of-sample predictions from 2019-01-06 onward. Discovery = 2019-01..2023-12, holdout = 2024-01..2026-10.

## Portfolios (10 strategies = 5 models x 2)
P  : equal weight top 5 predicted coins, held Monday open to Monday open.
PG : P scaled by the BTC F15 trend weight that Sunday (rest KRW cash).
Costs as v2 (fee + slippage tier) on weekly turnover.

## Tests
Information: weekly rank IC (Spearman of prediction vs target), mean and block-bootstrap t.
Decision: Sharpe(strategy) - Sharpe(F17) on daily returns. Discovery: centred stationary bootstrap p, BHY q <= 0.10
across the 10; survivor also needs maxDD not worse than F17 by > 10 points. Holdout: survivors only, p < 0.05 / n_surv.
Also reported against the EW universe basket. PASS -> forward paper. Everything logged as batch B56 (10 trials).
