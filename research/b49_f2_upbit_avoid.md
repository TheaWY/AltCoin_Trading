# B49: Upbit avoid rules as filters on F2 high-confidence longs (Binance)

B39 replay, side > 0, margin >= 0.0230188, baseline 4h net. Flags use Upbit hours closed before the signal.

| era | flag | flagged n / mean | kept n / mean | all n / mean |
|---|---|---|---|---|
| 2024 | upbit_listed | 28 / +1.43% | 137 / +1.57% | 165 / +1.55% |
| 2026 | upbit_listed | 71 / -1.76% | 460 / +1.37% | 531 / +0.95% |
| insample | upbit_listed | 55 / +1.72% | 398 / +0.94% | 453 / +1.03% |
| 2024 | upbit_pump24 | 27 / +1.90% | 138 / +1.48% | 165 / +1.55% |
| 2026 | upbit_pump24 | 62 / -2.46% | 469 / +1.40% | 531 / +0.95% |
| insample | upbit_pump24 | 44 / +1.68% | 409 / +0.96% | 453 / +1.03% |
| 2024 | new72 | 0 / +nan% | 165 / +1.55% | 165 / +1.55% |
| 2026 | new72 | 6 / -3.08% | 525 / +1.00% | 531 / +0.95% |
| insample | new72 | 9 / +2.75% | 444 / +1.00% | 453 / +1.03% |
| 2024 | prem10 | 0 / +nan% | 165 / +1.55% | 165 / +1.55% |
| 2026 | prem10 | 0 / +nan% | 531 / +0.95% | 531 / +0.95% |
| insample | prem10 | 0 / +nan% | 453 / +1.03% | 453 / +1.03% |
| 2024 | any_avoid | 27 / +1.90% | 138 / +1.48% | 165 / +1.55% |
| 2026 | any_avoid | 64 / -2.47% | 467 / +1.42% | 531 / +0.95% |
| insample | any_avoid | 47 / +1.73% | 406 / +0.95% | 453 / +1.03% |

| filter (drop flagged) | adopt? | kept 2026 n / mean | 95% CI |
|---|---|---|---|
| upbit_listed | no | 460 / +1.37% | [-0.04%, +2.82%] |
| upbit_pump24 | no | 469 / +1.40% | [+0.02%, +2.92%] |
| new72 | no | 525 / +1.00% | [-0.29%, +2.28%] |
| prem10 | no | 531 / +0.95% | [-0.31%, +2.27%] |
| any_avoid | no | 467 / +1.42% | [+0.02%, +2.95%] |

## Reading (2026-10-05)

- Not adopted under the pre-registered rule. In 2026, F2 longs on coins that pumped on Upbit in the prior 24h lost -2.5% (n=62) and dropping them lifts the rest to +1.40% (CI [+0.02, +2.92]). But in 2024 and in-sample the flagged trades did BETTER (+1.9%, +1.7%). The sign flips by era, so this is a 2026 regime effect, not a stable rule.
- new72 and prem10 almost never fire on F2 longs (0-9 trades).
- Kept as an observation: the forward check can compare F2L with and without the Upbit-pump24 flag once F2L has >= 60 closed trades.
