# B50 / U9: Upbit multi-day selloff dip-buy with BTC regime gate (long only)

| variant | train n / mean | val n / mean | test n / mean | test mirror |
|---|---|---|---|---|
| 72h <= -20%, regime any, hold 24h | 1742 / +6.75% | 664 / +0.47% | 774 / +0.20% | -0.94% |
| 72h <= -20%, regime any, hold 72h | 1286 / +8.10% | 428 / +2.24% | 548 / -0.28% | -0.48% |
| 72h <= -20%, regime btc<ma20, hold 24h | 958 / +3.72% | 322 / +0.06% | 531 / +0.70% | -1.46% |
| 72h <= -20%, regime btc<ma20, hold 72h | 738 / +4.22% | 228 / +0.46% | 388 / +0.04% | -0.82% |
| 72h <= -20%, regime btc>ma20, hold 24h | 823 / +10.28% | 357 / +0.80% | 253 / -1.09% | +0.38% |
| 72h <= -20%, regime btc>ma20, hold 72h | 682 / +10.02% | 217 / +3.65% | 177 / -1.76% | +1.02% |
| 72h <= -30%, regime any, hold 24h | 252 / +16.96% | 166 / +1.56% | 174 / -1.06% | +0.42% |
| 72h <= -30%, regime any, hold 72h | 215 / +17.65% | 122 / +5.70% | 129 / -3.14% | +2.49% |
| 72h <= -30%, regime btc<ma20, hold 24h | 127 / +7.95% | 60 / -0.94% | 102 / -0.62% | -0.01% |
| 72h <= -30%, regime btc<ma20, hold 72h | 109 / +7.56% | 46 / -1.98% | 78 / -4.50% | +3.87% |
| 72h <= -30%, regime btc>ma20, hold 24h | 124 / +25.77% | 108 / +2.65% | 75 / -1.73% | +1.09% |
| 72h <= -30%, regime btc>ma20, hold 72h | 113 / +25.36% | 80 / +9.61% | 55 / -1.30% | +0.63% |

| chosen | val mean | test n | test mean | 95% CI | verdict |
|---|---|---|---|---|---|
| 72h <= -30%, regime btc>ma20, hold 72h | +9.61% | 55 | -1.30% | [-4.90%, +2.56%] | **FAIL** |

Runtime 1s.

## Reading (2026-10-05)

- FAIL. Selected on validation (72h <= -30%, BTC > MA20, hold 72h, val +9.6%) -> TEST -1.30% CI [-4.90, +2.56].
- Train-era gains are partly the martial-law crash and rebound, but mostly a decaying edge (2024 bull market -> 2025 val smaller -> 2026 negative): 8% of train trades fall in 2024-12-03..06; without that window train mean is +3.76%.
- The BTC < MA20 gate from B45 does not carry over to plain Upbit dip-buying (validation near 0 or negative). The best TEST cell (-20%, BTC<MA20, 24h, +0.70%, n=531) was not the validation pick and is not claimed.
- Look-ahead: regime uses the last completed daily close; signal at hour close, entry next open.
