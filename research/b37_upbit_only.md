# B37 Upbit-only information study (2026-10-03 11:07 KST)

Question: using nothing but what Upbit itself publishes (its notices, its KRW prices and turnover), is there a long-only Upbit spot strategy that makes money after Upbit costs? Every number is Upbit-executed. Short history -> 60/40 time split, holdout decides.

## A. Upbit notices, executed on Upbit (long only, cost 0.20% round trip)

142 notice x market events with a KRW market and 1m candles (last 400 days). Entry = open of the minute after the notice (listings: first traded minute). Returns net of cost.

### delisting (n=2)

| set | n | mean | median | 95% CI | hit |
|---|---|---|---|---|---|
| raw +5m | 2 | -4.38% | -4.38% | [+nan, +nan] | 50% |
| raw +15m | 2 | -5.04% | -5.04% | [+nan, +nan] | 50% |
| raw +1h | 2 | -8.71% | -8.71% | [+nan, +nan] | 0% |
| raw +4h | 2 | -8.07% | -8.07% | [+nan, +nan] | 0% |
| raw +24h | 2 | -7.78% | -7.78% | [+nan, +nan] | 0% |
| F13 momentum rule (confirmed 1/2) | 1 | -3.96% | -3.96% | [+nan, +nan] | 0% |

### listing (n=85)

| set | n | mean | median | 95% CI | hit |
|---|---|---|---|---|---|
| raw +5m | 85 | +38.96% | +16.81% | [+28.82, +49.83] | 82% |
| raw +15m | 85 | +37.69% | +12.77% | [+26.13, +49.67] | 78% |
| raw +1h | 85 | +27.41% | +7.13% | [+16.82, +37.66] | 69% |
| raw +4h | 85 | +18.12% | +4.05% | [+9.57, +26.64] | 60% |
| raw +24h | 85 | +10.85% | -0.90% | [+1.88, +19.59] | 45% |
| F13 listing rule (60m, -5% stop) | 85 | +28.13% | +7.13% | [+17.82, +38.38] | 68% |
| open lag (min) median | 125 | | | | |
| F13 momentum rule (confirmed 11/85) | 11 | -6.05% | -5.12% | [-8.39, -4.47] | 0% |

### other (n=32)

| set | n | mean | median | 95% CI | hit |
|---|---|---|---|---|---|
| raw +5m | 32 | +23.28% | +11.43% | [+12.80, +35.07] | 78% |
| raw +15m | 32 | +18.76% | +6.31% | [+9.84, +28.51] | 66% |
| raw +1h | 32 | +14.06% | +3.80% | [+6.02, +23.09] | 62% |
| raw +4h | 32 | +8.06% | +0.29% | [-0.24, +18.56] | 50% |
| raw +24h | 32 | -1.80% | -6.67% | [-8.84, +6.16] | 25% |
| F13 momentum rule (confirmed 1/32) | 1 | -8.54% | -8.54% | [+nan, +nan] | 0% |

### warning (n=17)

| set | n | mean | median | 95% CI | hit |
|---|---|---|---|---|---|
| raw +5m | 17 | -4.11% | -2.93% | [-6.20, -2.10] | 12% |
| raw +15m | 17 | -6.40% | -5.65% | [-9.50, -3.47] | 18% |
| raw +1h | 17 | -9.62% | -11.06% | [-12.67, -6.49] | 6% |
| raw +4h | 17 | -10.93% | -10.29% | [-14.81, -7.05] | 6% |
| raw +24h | 17 | -10.20% | -8.49% | [-15.42, -5.53] | 29% |
| F13 momentum rule (confirmed 2/17) | 2 | -3.33% | -3.33% | [+nan, +nan] | 0% |

### warning_lifted (n=6)

| set | n | mean | median | 95% CI | hit |
|---|---|---|---|---|---|
| raw +5m | 6 | +0.01% | -0.14% | [-5.17, +4.94] | 50% |
| raw +15m | 6 | -0.53% | -0.31% | [-8.18, +5.91] | 33% |
| raw +1h | 6 | -1.03% | -1.91% | [-7.32, +7.07] | 33% |
| raw +4h | 6 | -1.34% | -3.24% | [-11.08, +11.74] | 17% |
| raw +24h | 6 | -1.72% | -4.30% | [-13.44, +12.92] | 33% |
| F13 momentum rule (confirmed 2/6) | 2 | +8.72% | +8.72% | [+nan, +nan] | 50% |

### listing: first 60% vs last 40% (time split)

| set | n | mean | median | 95% CI | hit |
|---|---|---|---|---|---|
| first 60% +1h | 51 | +27.44% | +7.17% | [+14.15, +41.81] | 69% |
| last 40% +1h | 34 | +27.37% | +6.57% | [+12.60, +44.83] | 71% |
| first 60% +24h | 51 | +11.76% | -0.34% | [-0.06, +23.57] | 49% |
| last 40% +24h | 34 | +9.47% | -2.02% | [-2.71, +22.44] | 38% |

## B. Upbit-only hourly bursts (close >= +5% on >= 3x trailing-24h turnover), long at the burst close

2647 onsets, 269 coins, 2026-03-17..2026-10-03; in-sample = first 60% (1589), holdout = last 40% (1058). Net of 0.20%.

### forward 1h

| split | n in | mean in | CI in | n out | mean out | CI out | hit out |
|---|---|---|---|---|---|---|---|
| all | 1589 | -1.25% | [-1.54,-0.97] | 1058 | -1.11% | [-1.45,-0.79] | 36% |
| fresh r7d<=0 | 283 | -1.18% | [-1.74,-0.60] | 71 | -0.25% | [-1.25,+0.79] | 42% |
| extended r7d>0 | 1163 | -1.18% | [-1.52,-0.85] | 973 | -1.18% | [-1.52,-0.82] | 35% |
| KST day 09-18 | 1010 | -1.20% | [-1.54,-0.88] | 627 | -0.81% | [-1.23,-0.37] | 36% |
| KST night | 579 | -1.34% | [-1.83,-0.84] | 431 | -1.55% | [-2.10,-0.98] | 35% |
| no Binance perp | 397 | -2.04% | [-2.65,-1.44] | 311 | -1.31% | [-1.86,-0.73] | 34% |
| has Binance perp | 1192 | -0.99% | [-1.31,-0.67] | 747 | -1.03% | [-1.43,-0.61] | 37% |
| notice in prior 24h | 23 | -4.05% | [-7.45,-0.67] | 8 | +0.01% | [-5.93,+7.87] | 38% |
| no notice | 1566 | -1.21% | [-1.51,-0.93] | 1050 | -1.12% | [-1.47,-0.77] | 36% |
| turnover share > 1% | 1401 | -1.33% | [-1.63,-1.01] | 834 | -1.21% | [-1.62,-0.81] | 36% |
| turnover share <= 1% | 188 | -0.70% | [-1.25,-0.06] | 224 | -0.73% | [-1.25,-0.19] | 34% |
| fresh & no perp | 88 | -1.90% | [-2.86,-0.85] | 24 | +0.94% | [-1.48,+3.40] | 50% |
| fresh & no perp & night | 11 | -3.58% | [-5.22,-2.23] | 5 | -2.43% | [-9.28,+4.17] | 40% |

### forward 4h

| split | n in | mean in | CI in | n out | mean out | CI out | hit out |
|---|---|---|---|---|---|---|---|
| all | 1589 | -2.81% | [-3.19,-2.41] | 1058 | -2.23% | [-2.74,-1.73] | 30% |
| fresh r7d<=0 | 283 | -2.11% | [-2.86,-1.33] | 71 | -1.69% | [-3.04,-0.27] | 34% |
| extended r7d>0 | 1163 | -2.80% | [-3.29,-2.30] | 973 | -2.26% | [-2.77,-1.74] | 30% |
| KST day 09-18 | 1010 | -2.59% | [-3.09,-2.08] | 627 | -1.62% | [-2.24,-0.97] | 30% |
| KST night | 579 | -3.20% | [-3.89,-2.51] | 431 | -3.11% | [-3.90,-2.33] | 30% |
| no Binance perp | 397 | -4.38% | [-5.09,-3.66] | 311 | -3.50% | [-4.22,-2.77] | 22% |
| has Binance perp | 1192 | -2.29% | [-2.77,-1.79] | 747 | -1.70% | [-2.31,-1.03] | 33% |
| notice in prior 24h | 23 | -10.66% | [-14.62,-6.76] | 8 | -0.17% | [-10.13,+11.83] | 38% |
| no notice | 1566 | -2.70% | [-3.09,-2.29] | 1050 | -2.24% | [-2.75,-1.73] | 30% |
| turnover share > 1% | 1401 | -2.98% | [-3.42,-2.53] | 834 | -2.55% | [-3.15,-1.94] | 28% |
| turnover share <= 1% | 188 | -1.52% | [-2.28,-0.63] | 224 | -1.01% | [-1.72,-0.21] | 37% |
| fresh & no perp | 88 | -3.90% | [-5.10,-2.47] | 24 | -0.97% | [-3.19,+1.64] | 33% |
| fresh & no perp & night | 11 | -5.24% | [-6.80,-3.78] | 5 | -4.38% | [-7.92,-0.97] | 20% |

### forward 24h

| split | n in | mean in | CI in | n out | mean out | CI out | hit out |
|---|---|---|---|---|---|---|---|
| all | 1588 | -5.24% | [-5.83,-4.63] | 1048 | -3.19% | [-4.10,-2.28] | 30% |
| fresh r7d<=0 | 282 | -2.82% | [-3.94,-1.58] | 71 | -1.34% | [-3.78,+1.66] | 32% |
| extended r7d>0 | 1163 | -5.54% | [-6.26,-4.83] | 963 | -3.16% | [-4.08,-2.16] | 30% |
| KST day 09-18 | 1010 | -5.76% | [-6.37,-5.09] | 621 | -2.81% | [-4.03,-1.48] | 29% |
| KST night | 578 | -4.32% | [-5.53,-3.11] | 427 | -3.75% | [-4.95,-2.53] | 30% |
| no Binance perp | 397 | -6.89% | [-7.80,-5.93] | 310 | -4.48% | [-5.74,-3.17] | 26% |
| has Binance perp | 1191 | -4.68% | [-5.39,-3.93] | 738 | -2.65% | [-3.83,-1.39] | 31% |
| notice in prior 24h | 23 | -13.38% | [-19.35,-7.16] | 6 | -20.84% | [-28.38,-12.33] | 0% |
| no notice | 1565 | -5.12% | [-5.71,-4.49] | 1042 | -3.09% | [-3.97,-2.15] | 30% |
| turnover share > 1% | 1401 | -5.80% | [-6.44,-5.17] | 826 | -3.52% | [-4.60,-2.38] | 29% |
| turnover share <= 1% | 187 | -1.01% | [-2.24,+0.44] | 222 | -1.97% | [-3.12,-0.72] | 31% |
| fresh & no perp | 88 | -4.35% | [-6.33,-2.09] | 24 | +2.57% | [-2.42,+9.94] | 46% |
| fresh & no perp & night | 11 | -8.16% | [-11.37,-5.56] | 5 | -6.98% | [-8.35,-5.57] | 0% |

## C. Upbit long-only cross-section: top decile by signal vs equal-weight Upbit universe (hourly rebalanced, net 0.20%/trade)

| signal | hold | in-sample mean/trade | in t | holdout mean/trade | out t | out hit |
|---|---|---|---|---|---|---|
| mom_24h (+) | 4h | -0.637% | -8.2 | -0.692% | -5.0 | 36% |
| mom_24h (+) | 24h | -2.190% | -6.3 | -3.117% | -5.4 | 12% |
| rev_1h (-) | 4h | -0.132% | -2.1 | -0.265% | -3.2 | 41% |
| rev_1h (-) | 24h | -0.738% | -2.8 | -0.368% | -0.7 | 47% |
| turnover_share_chg (+) | 4h | -0.638% | -8.4 | -0.792% | -6.0 | 33% |
| turnover_share_chg (+) | 24h | -2.165% | -6.7 | -3.010% | -4.5 | 15% |
| rev_24h (-) | 4h | -0.319% | -6.0 | -0.307% | -4.3 | 38% |
| rev_24h (-) | 24h | -0.718% | -3.0 | -0.877% | -1.8 | 27% |

_runtime 289s; caches in data/cache/b37/_
## Reading (added 11:40 KST)

1. The listing "pop" is real but lives inside the first traded minute: minute-1 close/open +38% mean (+16% median). Re-entering
   at the open of minute 2 gives -0.4% at 15m and -6% at 1h (hit 22-26%); with the 60-min/-5%-stop rule, holdout -0.1% at best.
   Only a first-seconds fill captures it; F13 live (2-s polling, buys the first ask) will show whether that ask is already up.
2. "거래 유의 종목 지정" (new warning) is a clean negative on Upbit: -9.6% at 1h, -10.9% at 4h, hit 6%, n=17, CI tight. It is not
   tradable: Upbit cannot short, and the Binance perp of the same coin does not follow (n=10, 4h +1.1% against a short).
   Usable only as an exit rule for anything held on Upbit.
3. Upbit hourly bursts fade on holdout under every split (4h -2.2%, 24h -3.2%); worst for coins without a Binance perp (-3.5% /
   -4.5%). The one positive cell (fresh & no perp, +0.9% at 1h, n=24) is noise. Long-only cross-section loses in every form.
4. Conclusion: with Upbit-only information and long-only execution, nothing passes. Every robust Upbit effect is a fade, which
   needs a short -> registered F14 (short the Binance perp of a Korea-led burst). F13 stays as the live check of (1).
