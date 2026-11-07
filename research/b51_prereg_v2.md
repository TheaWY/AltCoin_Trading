# B51: pre-registered v2 long-only allocation tests (H1-H3)

Prereg: research/prereg_v2_longonly.md (commit 751343c, written before data). Run 2026-10-05 11:05. 283 Upbit KRW markets (current listings only: survivorship warning for H2/H3), daily 2017-09-25..2026-10-04. Benchmarks are daily-rebalanced and cost-free (slightly favours the benchmark).

## H1 trend allocation BTC+ETH

### H1 trend allocation BTC+ETH: per era (strategy vs benchmark)

| era | days | Sharpe S | Sharpe B | CAGR S | CAGR B | maxDD S | maxDD B |
|---|---|---|---|---|---|---|---|
| E1 2017-19 | 628 | 1.00 | -0.13 | +34% | -27% | -39% | -81% |
| E2 2020-21 | 731 | 2.60 | 2.23 | +248% | +296% | -34% | -54% |
| E3 2022 | 365 | -1.32 | -1.31 | -25% | -64% | -26% | -65% |
| E4 2023-24 | 731 | 2.10 | 2.03 | +86% | +118% | -26% | -38% |
| E5 2025-26 | 641 | 0.89 | -0.02 | +19% | -12% | -20% | -57% |

Days 3096 (2018-04-13..2026-10-03). Full-sample Sharpe 1.55 vs 0.80, CAGR +65% vs +35%, maxDD -41% vs -81%. Sharpe diff +0.75, stationary-bootstrap one-sided p = 0.0006 (need < 0.0167); lower maxDD in 5/5 eras (need 4); DSR(M=1100) = 0.90. **PASS**

## H2 weekly 3-week momentum, top tercile of top-30 (long only) vs EW top-30

| era | weeks | mean edge/wk | strategy mean/wk | bench mean/wk |
|---|---|---|---|---|
| E1 2017-19 | 12 | -0.00% | -6.84% | -6.84% |
| E2 2020-21 | 86 | +0.05% | +2.70% | +2.65% |
| E3 2022 | 52 | -0.07% | -2.63% | -2.56% |
| E4 2023-24 | 105 | -0.34% | +0.78% | +1.12% |
| E5 2025-26 | 90 | -0.52% | -1.98% | -1.47% |

Weeks 345; mean edge -0.235%/wk; stationary-bootstrap one-sided p = 0.8926 (need < 0.0167); eras with positive edge 1/5; DSR(strategy, M=1100) = 0.00. Strategy Sharpe -0.15 vs bench -0.00. **FAIL**

## H3 trend-gated top-30 altcoin basket

### H3 trend-gated top-30 altcoin basket: per era (strategy vs benchmark)

| era | days | Sharpe S | Sharpe B | CAGR S | CAGR B | maxDD S | maxDD B |
|---|---|---|---|---|---|---|---|
| E1 2017-19 | 84 | -4.56 | -5.11 | -87% | -98% | -40% | -62% |
| E2 2020-21 | 600 | 1.59 | 1.29 | +126% | +113% | -42% | -70% |
| E3 2022 | 365 | -1.70 | -1.65 | -32% | -79% | -36% | -80% |
| E4 2023-24 | 731 | 1.07 | 0.88 | +37% | +40% | -39% | -63% |
| E5 2025-26 | 642 | -1.14 | -1.08 | -28% | -58% | -58% | -87% |

Days 2422 (2018-04-30..2026-10-04). Full-sample Sharpe 0.40 vs -0.04, CAGR +8% vs -27%, maxDD -65% vs -96%. Sharpe diff +0.44, stationary-bootstrap one-sided p = 0.0192 (need < 0.0167); lower maxDD in 5/5 eras (need 4); DSR(M=1100) = 0.01. **FAIL**

## Verdicts (Bonferroni K=3)

| hypothesis | stat diff | p | verdict |
|---|---|---|---|
| H1 | +0.7528 | 0.0006 | **PASS** |
| H2 | -0.0024 | 0.8926 | **FAIL** |
| H3 | +0.4389 | 0.0192 | **FAIL** |

Runtime 3s.
