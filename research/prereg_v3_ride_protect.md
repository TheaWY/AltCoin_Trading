# Pre-registration v3: surge ride (R) and loss protection (P), built on F15 (written 2026-10-05 12:45 KST, BEFORE running)

Asked by 유리 after B51/F15: two separate modules on top of the trend allocation: (R) notice a surge early and ride it,
(P) cut losses. Same data (Upbit KRW daily, 2017-09..2026-10-04, research/upbit_db/d1), same costs, eras, 1-bar lag and
stationary bootstrap as prereg v2. Family K = 2 (R, P): Bonferroni one-sided p < 0.025. One pre-specified rule each,
no parameter search. Anything else is EXPLORATORY and cannot pass. Data note: daily BTC/ETH data was already seen for
H1 (only H1's own result); R's alt universe and P's overlay were not examined.

## R: surge ride (breakout with volume, regime-gated, trailing exit)
Universe each day: BTC, ETH + top 20 other KRW coins by 30d median daily value with >= 365 days of history (point in
time, data before the day); stablecoins excluded.
Entry signal at close of day t (all of these):
  1. close_t > max(close_{t-20..t-1})  (new 20-day closing high)
  2. KRW value_t >= 2 x median(value_{t-30..t-1})  (volume surge)
  3. BTC regime: F15 ensemble weight of KRW-BTC at close t >= 0.5
Buy at open t+1, 10% of capital per position, at most 10 open (signals ranked by value surge if more), rest cash.
Exit: first close below the lowest close of the previous 10 days (Donchian 10) -> sell at next open; or 60 days max.
Re-entry allowed after exit on a new signal. Costs as v2 (fee 0.05% + slippage tier) on both legs.
Control (to prove the SIGNAL matters, not just the exit): same universe, same days-in-market budget, entries on random
days drawn from the universe when BTC regime >= 0.5, same exit rule; 200 random replications.
PASS = (a) mean trade net > 0 with day-clustered 95% CI lower bound > 0, AND (b) mean trade net beats the random-entry
control with p < 0.025 (share of random replications with mean >= real), AND (c) mean trade net > 0 in >= 4 of 5 eras
(eras with >= 10 trades). Portfolio Sharpe/maxDD vs F15 alone and vs F15 + R (50/50 capital) reported for information.
Survivorship: alt universe = current listings only (biased up for both R and its control; (b) is the robust test).

## P: loss protection overlay on F15 (crash brake + volatility scaling)
Per coin, F15 weight w_t, then
  brake: if close_t <= 0.90 x close_{t-1} OR close_t <= 0.85 x max(close_{t-20..t}) -> brake on; brake stays on until
         close > SMA20; while on, weight = 0.
  vol scale: s_t = min(1, median(rv20 over t-365..t) / rv20_t), rv20 = 20-day std of daily log returns (past only).
  P weight = (0 if brake else w_t) x s_t.
Same execution as H1 (decided at close, traded next open, costs on turnover). Benchmark = F15/H1 itself.
PASS = maxDD(P) < maxDD(H1) in >= 4 of 5 eras AND full-sample Calmar (CAGR/|maxDD|) of P > H1 AND Sharpe(P) - Sharpe(H1)
not significantly negative (one-sided bootstrap p for "P worse" > 0.025, i.e. non-inferior) AND worst single day and
worst 30-day loss of P <= those of H1.
Prior evidence against: B26/B26b vol targeting did not help other books in 2025-26. Recorded here so a FAIL is expected
as plausible.

## After
PASS -> forward paper (F16 = R, F17 = P) on live Upbit daily data from 2026-10-06, alerts added to the daily 09:15 note.
FAIL -> logged, not re-tuned.
