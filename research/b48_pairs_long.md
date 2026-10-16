# B48 / U7: Upbit pairs divergence, long one leg only

Rebalances 31; universe/pairs per rebalance median 15/40. Net after Upbit fees + slippage. Mirror = short side (not executable).

| variant | train n / mean | val n / mean | test n / mean | test win | test mirror |
|---|---|---|---|---|---|
| rich leg, exit zrule | 3306 / -0.29% | 1469 / -0.07% | 831 / -0.78% | 47% | +0.21% |
| rich leg, exit 24h | 4420 / -0.19% | 1944 / +0.01% | 1068 / -0.68% | 44% | +0.10% |
| rich leg, exit 72h | 2310 / -0.51% | 961 / +0.23% | 520 / -1.54% | 39% | +0.96% |
| cheap leg, exit zrule | 3306 / +0.06% | 1469 / -0.20% | 831 / -0.67% | 43% | +0.11% |
| cheap leg, exit 24h | 4420 / +0.00% | 1944 / -0.13% | 1068 / -0.43% | 44% | -0.13% |
| cheap leg, exit 72h | 2310 / -0.09% | 961 / -0.35% | 520 / -1.36% | 39% | +0.79% |

## Verdict

| chosen | val mean | test n | test mean | 95% CI | verdict |
|---|---|---|---|---|---|
| rich leg, exit 72h | +0.23% | 520 | -1.54% | [-3.18%, +0.25%] | **FAIL** |

Runtime 4s.

## Reading (2026-10-04)

- FAIL. With one leg only, a pair trade is mostly a bet on the alt market: both the rich and the cheap leg lose in 2026 (-0.4 to -1.5%) while alts fell. The hedge is the edge; without the short leg it is gone.
- Upbit's liquid universe is small (median 15 coins >= 8M USD/day; 5-8 in 2026), so few pairs qualify.
- Mirrors are near zero (-0.1 to +1.0%): no strong avoid rule here either.
- Look-ahead: beta and pair choice from the 90d select window before each rebalance; z uses a rolling window shifted by one hour; entry/exit at next open.
