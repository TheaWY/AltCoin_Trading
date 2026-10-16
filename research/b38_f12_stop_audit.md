# B38 F12 stop audit (2026-10-03 21:19 KST)

F12 trigger replayed on the last 14 days of 1-minute data (5-min grid): 360 bursts, 43 with r7d <= 0 (the F12 subset).
Returns net of fee + slippage. 'low' = stop checked on 1-minute lows (what F12 does live); 'close' = on 1-minute closes.

| exit rule | n | mean | median | 95% CI | hit | stopped |
|---|---|---|---|---|---|---|
| raw4h | 43 | -0.99% | -1.71% | [-3.48, +1.47] | 37% | 0% |
| stop3_low | 43 | -1.70% | -3.20% | [-2.65, -0.53] | 16% | 40% |
| stop3_close | 43 | -1.29% | -3.20% | [-2.35, -0.13] | 23% | 33% |
| stop5_low | 43 | -1.23% | -5.14% | [-2.88, +0.68] | 30% | 33% |
| stop5_close | 43 | -0.51% | -2.55% | [-2.24, +1.43] | 35% | 23% |
| stop7_low | 43 | -1.22% | -2.70% | [-3.12, +0.88] | 35% | 26% |
| stop7_close | 43 | -0.98% | -2.55% | [-2.89, +1.11] | 35% | 21% |
| stop10_low | 43 | -1.50% | -2.55% | [-3.70, +0.70] | 35% | 16% |
| stop10_close | 43 | -0.50% | -1.71% | [-2.62, +1.69] | 37% | 7% |

Share of F12-subset bursts whose 1-minute low touches -3% within 4h: 77%; median time to touch 2 min. Median max drawdown on lows -5.3%.

## all bursts (no r7d filter), same table

| exit rule | n | mean | 95% CI | hit |
|---|---|---|---|---|
| raw4h | 360 | -0.70% | [-1.72, +0.35] | 35% |
| stop3_low | 360 | -1.55% | [-2.00, -1.04] | 15% |
| stop5_low | 360 | -1.30% | [-1.96, -0.57] | 26% |
| stop5_close | 360 | -0.84% | [-1.52, -0.09] | 29% |
| stop10_close | 360 | -0.84% | [-1.76, +0.15] | 34% |

_runtime 31s_
## Reading (21:40 KST)

The stop is not the cause, it is an amplifier. Over the last 14 days the F12 subset (r7d <= 0) has a NEGATIVE raw 4h
expectation (-1.0%, n=43, CI [-3.5, +1.5]); no stop width or basis turns it positive (best: 10% on closes, -0.5%). The
+3.7% B35 found was carried by 5 trades (76% of the sum) over a window that overlaps this one by 10 days; it does not
replicate. Separately, 77% of these bursts touch -3% on 1-minute lows within a median of 2 minutes, so the 3%-on-lows
stop converts a small negative expectation into a near-certain -3.3% (live: 7 stops of 7). No F12b: there is nothing to
rescue. F12 runs to its pre-registered kill (30 events, mean <= 0). Lesson for the engine: a stop added at registration
must be replayed on 1-minute lows before the test starts; a 30-day in-sample split carried by 5 trades is not a rule.
