# Pre-registration v9: B57i (interpretation), B57x (Binance flow -> Upbit), B58 (predict risk, not return)
(written 2026-10-05 23:10 KST, before any data for these studies was opened; long only, Upbit KRW, paper)

유리 asked for the recommended plan (light B57 interpretation + B57x) plus a different perspective.
Every study so far tried to predict DIRECTION from Upbit daily data. The two new angles are:
(1) a different information source (Binance perp order flow, funding, cross-venue lead, kimchi premium), and
(2) a different target: forecast RISK (volatility, crash probability) and size exposure with it, which is
    usually far more predictable than returns and serves 유리's stated priority (smaller drawdowns).

## B57i interpretation (no tests, no trials counted)
Dev period 2023-01..2025-06 only. Univariate daily rank IC of each B57 feature vs yd7; correlation of the
ensemble prediction with -rank(rv30) and with BTC/ETH membership. Question: is the ML mostly "low vol + majors"?

## B57x Binance flow -> Upbit next-day relative return
Data: Binance USDT-M hourly panel (data/cache/b2_hourly_2024 + b2_hourly; 2024-03..2026-09), Upbit hourly
(data/upbit_db/h1, KRW-USDT for FX).
Universe each day: Upbit KRW coins with a Binance perp, top 40 by Upbit 30d median KRW value, >= 90d on Upbit.
Decision 00:00 UTC (Upbit day start). Features use only bars that closed by 00:00 UTC:
 X1 taker buy share 24h (Binance), X2 funding 24h, X3 Binance 24h return minus Upbit 24h return (lead gap),
 X4 Binance last-1h return minus Upbit last-1h return, X5 kimchi premium vs cross-sectional median,
 X6 premium change over 24h, X7 Binance volume surge, X8 Binance 24h volume share vs Upbit (log ratio, demeaned).
Execution 01:00 UTC Upbit hourly open (1h safety lag) to 01:00 next day; target demeaned across the universe.
Split: development 2024-03-15..2025-09-30, SEALED 2025-10-01..2026-09-24 (opened once).
Tests (dev): per-feature mean daily rank IC, day-block bootstrap p (two-sided), BHY q <= 0.10 over 8.
Composite: ridge and LightGBM on X1..X8 (+ cross-sectional ranks), walk-forward monthly retrain,
expanding window, starting 2024-09-01. Book: equal-weight top 5 by composite, scaled by BTC F15 trend
weight, daily, costs v2 (fee 0.05% + slippage tier).
Selection: composite model with higher dev IC. Sealed PASS: (a) composite mean daily rank IC > 0 with
day-block bootstrap p < 0.025 [information finding]; (b) book Sharpe - EW-universe gated book Sharpe > 0 with
p < 0.025 [usable selection edge]. Reported vs F17 too, but F17 is not the bar for (b) (different exposure).

## B58 Predict risk, not return (BTC/ETH sleeve, replaces F17's realised-vol scaling)
Data: Upbit daily 2017-09..2026-10. Base = F15 trend weight per coin (unchanged).
Forecasts, walk-forward, refit every January on all data before it:
 V0 F17's own: median365(rv20)/rv20 (benchmark), V1 HAR-RV (log rv1, rv7, rv30 -> log rv next 7d, OLS),
 V2 LightGBM on HAR terms + downside semivol + returns 1/7/30 + drawdown from 60d high + distance to SMA50/200
    + volume surge + BTC-ETH 30d correlation, V3 crash classifier (P[next-7d return < -10%]) logistic regression
    on the same features, V4 LightGBM classifier for the same event.
Rules (each = trend weight x overlay, rebalanced daily, costs v2):
 R1 vol target with V1: min(1, median365(V1 forecast)/V1 forecast) + F17 crash brake
 R2 same with V2
 R3 F17 + crash cut with V3: weight x 0.5 when crash prob > its 80th pct over the trailing 365d
 R4 same with V4
 R5 R1 + R3 combined.
Split: discovery 2019-01..2023-12 (training starts 2017-12), holdout 2024-01..2026-10-04 (same as B51).
Caveat: F17 itself was confirmed on that holdout; the new rules are compared to it on equal terms.
Forecast quality reported: QLIKE and R2 of log-vol vs V0, AUC/Brier for crash models (dev).
Selection in discovery only: the rule with best Sharpe among those with maxDD >= F17 maxDD (not worse).
Holdout PASS (one rule): EITHER Sharpe(rule) - Sharpe(F17) > 0 with p < 0.025, OR [maxDD better by >= 3 pts
AND worst-30d better AND Sharpe(rule) - Sharpe(F17) > -0.10]. The second branch encodes 유리's preference for
a smaller drawdown at a modest cost in return.

All results appended to research/trial_ledger.csv; scripts committed before running.
