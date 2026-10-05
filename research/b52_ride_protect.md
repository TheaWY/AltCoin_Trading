# B52: prereg v3 surge ride (R) and loss protection (P)

Prereg research/prereg_v3_ride_protect.md (commit cae2f8f, before running). Run 2026-10-05 12:39. Upbit KRW daily 2017-09-25..2026-10-04, 283 current markets (survivorship: alts biased up). Family K=2, one-sided alpha 0.025.

## R: surge ride

| era | trades | mean net | win | median hold d | best | worst |
|---|---|---|---|---|---|---|
| E1 2017-19 | 65 | +4.70% | 46% | 14 | +79% | -23% |
| E2 2020-21 | 200 | +28.24% | 44% | 16 | +1048% | -42% |
| E3 2022 | 48 | -2.72% | 29% | 14 | +88% | -30% |
| E4 2023-24 | 189 | +5.95% | 37% | 13 | +334% | -32% |
| E5 2025-26 | 134 | -2.31% | 33% | 12 | +120% | -47% |

All 636 trades: mean net +10.44% (day-clustered 95% CI [+5.10%, +16.85%]), win 39%, median -5.10%. Random-entry control (same universe, BTC regime on, same exit, same trade count, 200 reps): mean +4.52% (5-95% +1.84%..+8.32%); p(control >= real) = 0.015. Eras positive 3/5. **FAIL**

Portfolio view (information only):

| book | Sharpe | CAGR | maxDD |
|---|---|---|---|
| F15 (H1) alone | 1.55 | +65% | -41% |
| R alone (10 x 10% slots) | 1.16 | +52% | -56% |
| 50% F15 + 50% R | 1.50 | +61% | -31% |

## P: loss protection overlay on F15

### P (S) vs F15/H1 (B): per era (strategy vs benchmark)

| era | days | Sharpe S | Sharpe B | CAGR S | CAGR B | maxDD S | maxDD B |
|---|---|---|---|---|---|---|---|
| E1 2017-19 | 628 | 1.07 | 1.00 | +32% | +34% | -35% | -39% |
| E2 2020-21 | 731 | 3.09 | 2.60 | +247% | +248% | -17% | -34% |
| E3 2022 | 365 | -1.23 | -1.32 | -23% | -25% | -26% | -26% |
| E4 2023-24 | 731 | 2.11 | 2.10 | +73% | +86% | -26% | -26% |
| E5 2025-26 | 641 | 0.92 | 0.89 | +18% | +19% | -19% | -20% |

Full sample: Sharpe P 1.72 vs F15 1.55 (p that F15 is better = 0.921); Calmar 1.71 vs 1.60; maxDD -36% vs -41%; worst day -9.6% vs -13.3%; worst 30d -21.4% vs -29.0%; lower maxDD in 4/5 eras. **PASS**

## Verdicts

| R surge ride | **FAIL** |
|---|---|
| P loss protection | **PASS** |

Runtime 5s.
