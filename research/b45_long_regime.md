# B45 BTC trend filter for long-only trades (2026-10-04)

Trade only when BTC's previous daily close is above its N-day average. N chosen on the earlier era, judged on 2026.

## (a) F2 long signals (Binance, frozen CNN)

| set | exit | filter | 2024 n / mean | 2026 n / mean | 2026 95% CI |
|---|---|---|---|---|---|
| all longs | baseline 4h | none | 357 / +0.45% | 1183 / +0.28% | [-0.64%, +1.20%] |
| all longs | baseline 4h | BTC > MA20 | 186 / +0.35% | 621 / +0.31% | [-0.82%, +1.44%] |
| all longs | baseline 4h | BTC < MA20 | 171 / +0.55% | 562 / +0.24% | [-1.21%, +1.71%] |
| all longs | baseline 4h | BTC > MA50 | 216 / +0.45% | 669 / +0.15% | [-0.90%, +1.29%] |
| all longs | baseline 4h | BTC < MA50 | 141 / +0.45% | 514 / +0.44% | [-1.08%, +1.93%] |
| all longs | baseline 4h | BTC > MA100 | 264 / +0.70% | 343 / +0.33% | [-1.33%, +2.15%] |
| all longs | baseline 4h | BTC < MA100 | 93 / -0.26% | 840 / +0.26% | [-0.84%, +1.31%] |
| all longs | trail arm 15% give-back 10% | none | 357 / +0.83% | 1183 / +0.75% | [-0.01%, +1.54%] |
| all longs | trail arm 15% give-back 10% | BTC > MA20 | 186 / +1.04% | 621 / +0.60% | [-0.46%, +1.67%] |
| all longs | trail arm 15% give-back 10% | BTC < MA20 | 171 / +0.61% | 562 / +0.91% | [-0.33%, +2.12%] |
| all longs | trail arm 15% give-back 10% | BTC > MA50 | 216 / +1.03% | 669 / +0.75% | [-0.31%, +1.80%] |
| all longs | trail arm 15% give-back 10% | BTC < MA50 | 141 / +0.53% | 514 / +0.74% | [-0.48%, +1.89%] |
| all longs | trail arm 15% give-back 10% | BTC > MA100 | 264 / +1.16% | 343 / +1.44% | [-0.20%, +3.17%] |
| all longs | trail arm 15% give-back 10% | BTC < MA100 | 93 / -0.10% | 840 / +0.46% | [-0.47%, +1.40%] |
| high-confidence longs | baseline 4h | none | 165 / +1.55% | 531 / +0.95% | [-0.35%, +2.34%] |
| high-confidence longs | baseline 4h | BTC > MA20 | 90 / +0.71% | 275 / -0.04% | [-1.74%, +1.68%] |
| high-confidence longs | baseline 4h | BTC < MA20 | 75 / +2.55% | 256 / +2.01% | [+0.05%, +4.12%] |
| high-confidence longs | baseline 4h | BTC > MA50 | 102 / +1.17% | 290 / +0.36% | [-1.27%, +2.12%] |
| high-confidence longs | baseline 4h | BTC < MA50 | 63 / +2.15% | 241 / +1.65% | [-0.31%, +3.74%] |
| high-confidence longs | baseline 4h | BTC > MA100 | 132 / +1.73% | 158 / -0.14% | [-2.67%, +2.33%] |
| high-confidence longs | baseline 4h | BTC < MA100 | 33 / +0.83% | 373 / +1.41% | [-0.23%, +2.88%] |
| high-confidence longs | trail arm 15% give-back 10% | none | 165 / +1.76% | 531 / +0.65% | [-0.47%, +1.80%] |
| high-confidence longs | trail arm 15% give-back 10% | BTC > MA20 | 90 / +0.95% | 275 / -0.24% | [-1.78%, +1.36%] |
| high-confidence longs | trail arm 15% give-back 10% | BTC < MA20 | 75 / +2.73% | 256 / +1.60% | [-0.03%, +3.31%] |
| high-confidence longs | trail arm 15% give-back 10% | BTC > MA50 | 102 / +1.39% | 290 / -0.00% | [-1.50%, +1.56%] |
| high-confidence longs | trail arm 15% give-back 10% | BTC < MA50 | 63 / +2.36% | 241 / +1.43% | [-0.28%, +3.15%] |
| high-confidence longs | trail arm 15% give-back 10% | BTC > MA100 | 132 / +1.99% | 158 / +0.12% | [-2.14%, +2.33%] |
| high-confidence longs | trail arm 15% give-back 10% | BTC < MA100 | 33 / +0.83% | 373 / +0.87% | [-0.52%, +2.18%] |

## (b) Upbit long-only variants (B43) with the filter; N chosen on val per variant, judged on TEST 2026

| family | variant | best N (val) | val mean filtered | test n filtered | test mean filtered | test 95% CI | test mean unfiltered |
|---|---|---|---|---|---|---|---|
| F_crash | -10%/1h delay0h hold1h | MA50 | -0.70% | 166 | -0.48% | [-1.22%, +0.24%] | -0.40% |
| F_crash | -10%/1h delay0h hold4h | MA100 | -3.16% | 118 | -2.58% | [-3.93%, -1.11%] | -2.36% |
| F_crash | -10%/1h delay0h hold24h | MA100 | -3.55% | 107 | -3.74% | [-6.06%, -1.28%] | -5.03% |
| F_crash | -10%/1h delay0h hold72h | MA50 | +7.78% | 135 | -2.75% | [-8.07%, +4.12%] | -3.82% |
| F_crash | -10%/1h delay1h hold1h | MA20 | -0.68% | 139 | -1.06% | [-1.66%, -0.45%] | -1.34% |
| F_crash | -10%/1h delay1h hold4h | MA100 | +0.07% | 118 | -2.38% | [-3.54%, -0.96%] | -2.54% |
| F_crash | -10%/1h delay1h hold24h | MA100 | -2.60% | 107 | -3.38% | [-5.38%, -1.03%] | -4.87% |
| F_crash | -10%/1h delay1h hold72h | MA50 | +8.00% | 135 | -2.63% | [-7.68%, +3.11%] | -3.89% |
| F_crash | -10%/1h delay3h hold1h | MA20 | +0.81% | 138 | -0.50% | [-1.07%, +0.13%] | -0.29% |
| F_crash | -10%/1h delay3h hold4h | MA50 | +0.08% | 158 | -0.31% | [-1.48%, +1.04%] | -1.12% |
| F_crash | -10%/1h delay3h hold24h | MA100 | -3.55% | 107 | -1.47% | [-3.54%, +1.08%] | -3.12% |
| F_crash | -10%/1h delay3h hold72h | MA50 | -2.39% | 135 | -1.80% | [-6.24%, +3.03%] | -2.65% |
| F_dump24 | -25%/24h delay0h hold24h | MA20 | -2.49% | 47 | -3.52% | [-7.20%, -0.04%] | -2.15% |
| F_dump24 | -25%/24h delay0h hold72h | MA20 | +12.29% | 43 | +0.40% | [-8.54%, +13.09%] | -2.14% |
| F_pump | +10%/1h buy now hold1h | MA20 | -2.10% | 565 | -1.79% | [-2.31%, -1.26%] | -1.66% |
| F_pump | +10%/1h buy now hold4h | MA20 | -3.77% | 524 | -3.31% | [-4.17%, -2.43%] | -3.42% |
| F_pump | +10%/1h buy now hold24h | MA100 | -6.27% | 333 | -5.37% | [-7.31%, -3.36%] | -6.84% |
| F_fade | +10% pump, buy after 2h hold24h | MA100 | -3.95% | 333 | -4.07% | [-5.84%, -2.11%] | -5.54% |
| F_fade | +10% pump, buy after 2h hold72h | MA100 | -7.60% | 285 | -5.88% | [-7.84%, -3.64%] | -8.14% |
| F_fade | +10% pump, buy after 6h hold24h | MA100 | -4.22% | 333 | -3.21% | [-4.60%, -1.55%] | -4.21% |
| F_fade | +10% pump, buy after 6h if -10% from pump close, hold24h | MA20 | -4.42% | 158 | -3.13% | [-4.46%, -1.85%] | -3.14% |
| F_fade | +10% pump, buy after 6h hold72h | MA100 | -6.65% | 285 | -4.52% | [-6.46%, -2.26%] | -6.36% |
| F_fade | +10% pump, buy after 6h if -10% from pump close, hold72h | MA100 | -5.26% | 105 | -0.97% | [-4.68%, +3.92%] | -3.73% |
| F_fade | +10% pump, buy after 12h hold24h | MA100 | -3.63% | 332 | -2.33% | [-3.61%, -0.78%] | -3.14% |
| F_fade | +10% pump, buy after 12h if -10% from pump close, hold24h | MA50 | -2.95% | 206 | -2.17% | [-3.54%, -0.69%] | -2.37% |
| F_fade | +10% pump, buy after 12h hold72h | MA100 | -5.67% | 284 | -3.17% | [-5.29%, -0.84%] | -4.60% |
| F_fade | +10% pump, buy after 12h if -10% from pump close, hold72h | MA100 | -5.78% | 137 | -1.88% | [-4.54%, +1.46%] | -3.73% |
| F_fade | +10% pump, buy after 24h hold24h | MA100 | -2.18% | 334 | -1.91% | [-3.14%, -0.63%] | -2.50% |
| F_fade | +10% pump, buy after 24h if -10% from pump close, hold24h | MA20 | -2.50% | 239 | -1.58% | [-2.76%, -0.32%] | -1.56% |
| F_fade | +10% pump, buy after 24h hold72h | MA20 | -4.03% | 390 | -1.76% | [-3.37%, +0.01%] | -2.91% |
| F_fade | +10% pump, buy after 24h if -10% from pump close, hold72h | MA20 | -2.34% | 226 | +0.05% | [-2.37%, +2.92%] | -1.24% |
| F_xs | top decile rev1d (lowest 1d return), hold 1d | MA100 | -0.99% | 90 | -0.45% | [-0.99%, +0.09%] | -0.96% |
| F_xs | top decile mom1d (highest 1d return), hold 1d | MA100 | -0.59% | 90 | -0.47% | [-1.37%, +0.46%] | -1.31% |
| F_xs | top decile mom7d (highest 7d return), hold 1d | MA100 | -0.68% | 90 | +0.04% | [-0.88%, +0.99%] | -0.90% |
| F_xs | top decile mom28d (highest 28d return), hold 1d | MA100 | -0.27% | 90 | +0.14% | [-0.66%, +0.96%] | -0.48% |
| F_xs | top decile vsurge (24h value / 30d avg), hold 1d | MA100 | -0.39% | 90 | -0.29% | [-1.19%, +0.65%] | -1.26% |
| F_xs | top decile lowvol (lowest 7d vol), hold 1d | MA100 | +0.04% | 90 | +0.15% | [-0.23%, +0.56%] | -0.01% |
| F_xs | top decile lowMAX (no lottery coins), hold 1d | MA100 | -0.05% | 90 | +0.06% | [-0.32%, +0.43%] | -0.12% |
| F_xs | top decile highMAX (lottery coins), hold 1d | MA100 | -0.84% | 90 | -0.64% | [-1.43%, +0.13%] | -1.12% |
| F_xs | benchmark: all liquid coins equal weight, hold 1d | MA100 | -0.19% | 90 | +0.09% | [-0.41%, +0.63%] | -0.30% |
| F_kimchi | top decile lowest premium (discount vs Binance), hold 1d | MA100 | -0.18% | 90 | +0.35% | [-0.40%, +1.09%] | -0.21% |
| F_kimchi | top decile highest premium, hold 1d | MA100 | -0.82% | 90 | -0.51% | [-1.11%, +0.09%] | -1.09% |
| F_kimchi | premium >= +10% -> buy, hold 24h | MA20 | -6.57% | 71 | -4.41% | [-6.76%, -1.90%] | -6.08% |
| F_season | hold alt basket 09-13 KST | MA100 | -0.48% | 91 | -0.51% | [-0.75%, -0.28%] | -0.63% |
| F_season | hold alt basket 13-18 KST | MA20 | -0.52% | 147 | -0.47% | [-0.65%, -0.28%] | -0.53% |
| F_season | hold alt basket 18-24 KST | MA100 | -0.65% | 90 | -0.52% | [-0.77%, -0.28%] | -0.52% |
| F_season | hold alt basket 00-09 KST | MA50 | -0.10% | 151 | -0.29% | [-0.52%, -0.07%] | -0.36% |
| F_breadth | many pumps in last 24h (top 20% days) -> alt basket hold 1d | MA50 | -1.10% | 91 | -0.13% | [-0.62%, +0.37%] | -0.26% |

Variants whose filtered TEST CI is above 0: 0
Note: many variants were tried (multiple testing); a lone CI > 0 among ~60 needs forward confirmation before trading.

## Reading (2026-10-04)

- The usual long-only rule "only buy when BTC is above its average" does NOT help: no Upbit variant passes with it, and F2
  longs are not better when BTC trends up.
- Unexpected and consistent in both out-of-sample eras: high-confidence F2 longs do BETTER when BTC is BELOW its 20-day
  average (2024 +2.55%, 2026 +2.01%, 2026 CI [+0.05%, +4.12%]) and worse above it (-0.04% in 2026). A plausible reason:
  pumps in a weak market are coin-specific news, pumps in a strong market are beta that reverses. This is one of ~60
  comparisons, so it is registered as forward hypothesis F2W (judged on the F2b shadow table), not traded on its own yet.
