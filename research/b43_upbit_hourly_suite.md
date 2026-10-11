# B43 Upbit-native long-only hourly suite (2026-10-04)

289 Upbit KRW markets, hourly, 2024-01-01..now. Long only; net after Upbit fees + slippage. Eras: train 2024-01..2025-06, val 2025-07..12, TEST 2026. Per family the best-validation variant is judged once on TEST. Mirror = what the opposite (short) side would have made after costs: not executable on Upbit, shown as information / avoid filter.

## F_crash

| variant | train n / mean | val n / mean | test n / mean | test mirror |
|---|---|---|---|---|
| -10%/1h delay0h hold1h | 312 / +12.53% | 180 / -1.03% | 308 / -0.40% | -0.11% |
| -10%/1h delay0h hold4h | 305 / +17.75% | 168 / -2.65% | 291 / -2.36% | +1.85% |
| -10%/1h delay0h hold24h | 284 / +28.64% | 156 / -3.56% | 264 / -5.03% | +4.50% |
| -10%/1h delay0h hold72h | 274 / +24.15% | 147 / -0.41% | 250 / -3.82% | +3.29% |
| -10%/1h delay1h hold1h | 312 / +2.80% | 180 / -0.75% | 308 / -1.34% | +0.83% |
| -10%/1h delay1h hold4h | 305 / +4.17% | 168 / -0.45% | 291 / -2.54% | +2.03% |
| -10%/1h delay1h hold24h | 284 / +10.66% | 156 / -2.55% | 264 / -4.87% | +4.34% |
| -10%/1h delay1h hold72h | 274 / +7.85% | 147 / +0.48% | 250 / -3.89% | +3.36% |
| -10%/1h delay3h hold1h | 312 / +0.65% | 180 / -0.75% | 308 / -0.29% | -0.21% |
| -10%/1h delay3h hold4h | 305 / +2.40% | 168 / +1.04% | 291 / -1.12% | +0.61% |
| -10%/1h delay3h hold24h | 284 / +5.28% | 156 / -2.22% | 264 / -3.12% | +2.59% |
| -10%/1h delay3h hold72h | 274 / +4.28% | 147 / +1.67% | 250 / -2.65% | +2.12% |

## F_dump24

| variant | train n / mean | val n / mean | test n / mean | test mirror |
|---|---|---|---|---|
| -25%/24h delay0h hold24h | 223 / +30.02% | 130 / -1.93% | 117 / -2.15% | +1.71% |
| -25%/24h delay0h hold72h | 218 / +26.26% | 127 / +10.00% | 111 / -2.14% | +1.71% |
| -25%/24h delay3h hold24h | 223 / +6.39% | 130 / -1.84% | 117 / -1.91% | +1.47% |
| -25%/24h delay3h hold72h | 218 / +6.45% | 127 / +7.22% | 111 / -1.43% | +1.00% |

## F_pump

| variant | train n / mean | val n / mean | test n / mean | test mirror |
|---|---|---|---|---|
| +10%/1h buy now hold1h | 925 / -0.68% | 470 / -1.89% | 1109 / -1.66% | +1.08% |
| +10%/1h buy now hold4h | 890 / -0.89% | 444 / -3.56% | 1028 / -3.42% | +2.83% |
| +10%/1h buy now hold24h | 756 / +0.57% | 370 / -6.33% | 870 / -6.84% | +6.21% |

## F_fade

| variant | train n / mean | val n / mean | test n / mean | test mirror |
|---|---|---|---|---|
| +10% pump, buy after 2h hold24h | 756 / +0.11% | 370 / -4.06% | 870 / -5.54% | +4.91% |
| +10% pump, buy after 2h if -10% from pump close, hold24h | 63 / +0.99% | 55 / -2.14% | 164 / -4.75% | +4.17% |
| +10% pump, buy after 2h hold72h | 694 / -2.27% | 342 / -8.11% | 775 / -8.14% | +7.50% |
| +10% pump, buy after 2h if -10% from pump close, hold72h | 63 / -3.83% | 55 / -7.94% | 159 / -4.91% | +4.32% |
| +10% pump, buy after 6h hold24h | 756 / -0.55% | 370 / -3.73% | 870 / -4.21% | +3.58% |
| +10% pump, buy after 6h if -10% from pump close, hold24h | 112 / -1.39% | 108 / -1.96% | 319 / -3.14% | +2.57% |
| +10% pump, buy after 6h hold72h | 694 / -2.61% | 342 / -7.02% | 775 / -6.36% | +5.72% |
| +10% pump, buy after 6h if -10% from pump close, hold72h | 110 / -4.35% | 103 / -5.46% | 305 / -3.73% | +3.15% |
| +10% pump, buy after 12h hold24h | 756 / -1.77% | 369 / -4.21% | 870 / -3.14% | +2.51% |
| +10% pump, buy after 12h if -10% from pump close, hold24h | 171 / -1.98% | 162 / -4.07% | 412 / -2.37% | +1.79% |
| +10% pump, buy after 12h hold72h | 694 / -2.43% | 341 / -6.88% | 775 / -4.60% | +3.96% |
| +10% pump, buy after 12h if -10% from pump close, hold72h | 169 / -3.29% | 157 / -6.40% | 393 / -3.73% | +3.14% |
| +10% pump, buy after 24h hold24h | 756 / -2.44% | 368 / -2.90% | 870 / -2.50% | +1.88% |
| +10% pump, buy after 24h if -10% from pump close, hold24h | 238 / -2.76% | 198 / -3.11% | 498 / -1.56% | +0.98% |
| +10% pump, buy after 24h hold72h | 694 / -2.89% | 340 / -5.60% | 773 / -2.91% | +2.27% |
| +10% pump, buy after 24h if -10% from pump close, hold72h | 232 / -2.71% | 193 / -4.72% | 474 / -1.24% | +0.66% |

## F_xs

| variant | train n / mean | val n / mean | test n / mean | test mirror |
|---|---|---|---|---|
| top decile rev1d (lowest 1d return), hold 1d | 544 / -0.75% | 184 / -1.26% | 276 / -0.96% | +0.28% |
| top decile mom1d (highest 1d return), hold 1d | 544 / -0.59% | 184 / -1.14% | 276 / -1.31% | +0.71% |
| top decile mom7d (highest 7d return), hold 1d | 539 / -0.37% | 184 / -0.83% | 276 / -0.90% | +0.62% |
| top decile mom28d (highest 28d return), hold 1d | 518 / -0.27% | 184 / -0.64% | 276 / -0.48% | +0.31% |
| top decile vsurge (24h value / 30d avg), hold 1d | 537 / -0.75% | 184 / -1.00% | 276 / -1.26% | +0.79% |
| top decile lowvol (lowest 7d vol), hold 1d | 542 / +0.09% | 184 / -0.19% | 276 / -0.01% | -0.14% |
| top decile lowMAX (no lottery coins), hold 1d | 542 / +0.01% | 184 / -0.31% | 276 / -0.12% | -0.14% |
| top decile highMAX (lottery coins), hold 1d | 542 / -0.42% | 184 / -1.06% | 276 / -1.12% | +0.93% |
| benchmark: all liquid coins equal weight, hold 1d | 544 / -0.06% | 184 / -0.40% | 276 / -0.30% | +0.15% |

## F_kimchi

| variant | train n / mean | val n / mean | test n / mean | test mirror |
|---|---|---|---|---|
| top decile lowest premium (discount vs Binance), hold 1d | 388 / -0.23% | 184 / -0.39% | 276 / -0.21% | -0.55% |
| top decile highest premium, hold 1d | 388 / -0.73% | 184 / -1.06% | 276 / -1.09% | +0.45% |
| premium <= -3% -> buy, hold 24h | 117 / +43.33% | 25 / +1.60% | 135 / -4.18% | +3.50% |
| premium <= -5% -> buy, hold 24h | 97 / +52.09% | 13 / +0.79% | 93 / -2.98% | +2.29% |
| premium >= +10% -> buy, hold 24h | 23 / -4.82% | 278 / -5.28% | 118 / -6.08% | +5.45% |

## F_season

| variant | train n / mean | val n / mean | test n / mean | test mirror |
|---|---|---|---|---|
| hold alt basket 09-13 KST | 544 / -0.66% | 184 / -0.54% | 277 / -0.63% | -0.26% |
| hold alt basket 13-18 KST | 544 / -0.40% | 184 / -0.64% | 277 / -0.53% | -0.35% |
| hold alt basket 18-24 KST | 544 / -0.44% | 184 / -0.54% | 276 / -0.52% | -0.36% |
| hold alt basket 00-09 KST | 544 / -0.07% | 184 / -0.31% | 276 / -0.36% | -0.52% |
| hold alt basket Mon (UTC day) | 78 / -0.02% | 26 / -1.63% | 39 / -0.13% | -0.16% |
| hold alt basket Tue (UTC day) | 77 / -0.50% | 27 / -0.55% | 39 / -0.98% | +0.73% |
| hold alt basket Wed (UTC day) | 77 / +0.35% | 27 / +0.15% | 39 / -0.37% | +0.12% |
| hold alt basket Thu (UTC day) | 78 / -0.06% | 26 / -1.20% | 40 / -0.61% | +0.37% |
| hold alt basket Fri (UTC day) | 78 / +0.24% | 26 / -0.27% | 40 / +0.79% | -1.03% |
| hold alt basket Sat (UTC day) | 78 / +0.32% | 26 / +0.32% | 40 / -0.12% | -0.12% |
| hold alt basket Sun (UTC day) | 78 / -0.85% | 26 / +0.04% | 39 / -1.14% | +0.84% |

## F_btclead

| variant | train n / mean | val n / mean | test n / mean | test mirror |
|---|---|---|---|---|
| BTC +1.5%/1h on Binance -> buy Upbit alt basket, hold 1h | 168 / -0.16% | 21 / -0.42% | 53 / -0.48% | -0.40% |
| BTC +1.5%/1h on Binance -> buy Upbit alt basket, hold 4h | 168 / +0.13% | 21 / -0.76% | 53 / -0.59% | -0.29% |
| BTC +1.5%/1h on Binance -> buy Upbit alt basket, hold 24h | 168 / +1.54% | 21 / -0.83% | 53 / -0.13% | -0.05% |
| BTC -1.5%/1h on Binance -> buy Upbit alt basket, hold 1h | 176 / -0.29% | 34 / -0.66% | 43 / -0.45% | -0.43% |
| BTC -1.5%/1h on Binance -> buy Upbit alt basket, hold 4h | 176 / -0.02% | 34 / -1.20% | 43 / -0.32% | -0.55% |
| BTC -1.5%/1h on Binance -> buy Upbit alt basket, hold 24h | 176 / +1.09% | 34 / -0.06% | 43 / +0.65% | -0.81% |

## F_breadth

| variant | train n / mean | val n / mean | test n / mean | test mirror |
|---|---|---|---|---|
| many pumps in last 24h (top 20% days) -> alt basket hold 1d | 122 / +0.58% | 75 / -1.03% | 180 / -0.26% | +0.08% |
| few pumps in last 24h (bottom 20%) -> alt basket hold 1d | 236 / -0.35% | 41 / +0.36% | 17 / +0.76% | -1.02% |

## Verdicts (variant chosen on validation, judged on 2026 TEST)

| family | chosen variant | val mean | test n | test mean | test 95% CI | verdict |
|---|---|---|---|---|---|---|
| F_crash | -10%/1h delay3h hold72h | +1.67% | 250 | -2.65% | [-5.64%, +0.99%] | **FAIL** |
| F_dump24 | -25%/24h delay0h hold72h | +10.00% | 111 | -2.14% | [-6.55%, +3.46%] | **FAIL** |
| F_pump | +10%/1h buy now hold1h | -1.89% | 1109 | -1.66% | [-2.11%, -1.18%] | **FAIL** |
| F_fade | +10% pump, buy after 6h if -10% from pump close, hold24h | -1.96% | 319 | -3.14% | [-4.41%, -1.87%] | **FAIL** |
| F_xs | top decile lowvol (lowest 7d vol), hold 1d | -0.19% | 276 | -0.01% | [-0.23%, +0.21%] | **FAIL** |
| F_kimchi | top decile lowest premium (discount vs Binance), hold 1d | -0.39% | 276 | -0.21% | [-0.60%, +0.20%] | **FAIL** |
| F_season | hold alt basket 00-09 KST | -0.31% | 276 | -0.36% | [-0.54%, -0.19%] | **FAIL** |
| F_btclead | BTC -1.5%/1h on Binance -> buy Upbit alt basket, hold 24h | -0.06% | 43 | +0.65% | [-0.79%, +2.23%] | **FAIL** |
| F_breadth | few pumps in last 24h (bottom 20%) -> alt basket hold 1d | +0.36% | 17 | +0.76% | [-0.18%, +1.72%] | **FAIL** |

## Strongly negative in ALL three eras (mean < -1% each): the minus is real, so its mirror is real too

| family | variant | mirror train | mirror val | mirror test | test n |
|---|---|---|---|---|---|
| F_fade | +10% pump, buy after 2h hold72h | +1.83% | +7.51% | +7.50% | 775 |
| F_fade | +10% pump, buy after 2h if -10% from pump close, hold72h | +3.44% | +7.39% | +4.32% | 159 |
| F_fade | +10% pump, buy after 6h if -10% from pump close, hold24h | +0.99% | +1.44% | +2.57% | 319 |
| F_fade | +10% pump, buy after 6h hold72h | +2.17% | +6.42% | +5.72% | 775 |
| F_fade | +10% pump, buy after 6h if -10% from pump close, hold72h | +3.95% | +4.93% | +3.15% | 305 |
| F_fade | +10% pump, buy after 12h hold24h | +1.34% | +3.63% | +2.51% | 870 |
| F_fade | +10% pump, buy after 12h if -10% from pump close, hold24h | +1.60% | +3.54% | +1.79% | 412 |
| F_fade | +10% pump, buy after 12h hold72h | +1.99% | +6.28% | +3.96% | 775 |
| F_fade | +10% pump, buy after 12h if -10% from pump close, hold72h | +2.90% | +5.87% | +3.14% | 393 |
| F_fade | +10% pump, buy after 24h hold24h | +2.01% | +2.32% | +1.88% | 870 |
| F_fade | +10% pump, buy after 24h if -10% from pump close, hold24h | +2.37% | +2.57% | +0.98% | 498 |
| F_fade | +10% pump, buy after 24h hold72h | +2.45% | +5.00% | +2.27% | 773 |
| F_fade | +10% pump, buy after 24h if -10% from pump close, hold72h | +2.32% | +4.17% | +0.66% | 474 |
| F_kimchi | premium >= +10% -> buy, hold 24h | +4.18% | +4.59% | +5.45% | 118 |

These are (a) AVOID rules for any long book, and (b) shorts that cannot be placed on Upbit; a long-only use needs a
different instrument (e.g. buying after the fade, F_fade) rather than inverting the trade.

Runtime 6s.

## Reading (2026-10-04)

- All 9 long-only families FAIL on the 2026 test. Nothing in this suite is a buy signal on Upbit.
- Train-era crash/dump/discount results (+12..+52%) are dominated by ONE night: 2024-12-03 14:00 UTC (Korean martial-law declaration), when dozens of Upbit coins crashed 30-50% and recovered within hours. It is real but a one-off; in 2025H2 and 2026 buying crashes loses (-2..-5% per trade).
- Strong, consistent minuses (all three eras < -1%), i.e. 유리's "big minus = big plus on the other side":
  * buying a coin in the 2-24h after a +10%/1h Upbit pump: -2.5..-8% (the decline lasts days, so "buy after the fade" does not work either);
  * buying a coin whose Upbit price is >= 10% above Binance (kimchi premium): -5..-6% over 24h.
  The opposite side earns +2..+7.5% after costs, but that side is a short and cannot be placed on Upbit.
- Use: AVOID rules for every long book: no Upbit buys within 72h after a +10%/1h pump, none when the coin's premium is >= +10%.
