# B41 F2 pump CNN on Upbit KRW pumps, long-only on Upbit (2026-10-04)

796 Upbit +10%/1h events, 2026-03-21..2026-10-04. Frozen F2 ensemble, unchanged thresholds. Net after 2x0.05% fee + slippage. Long = buy on Upbit at T+60s.

| set | n | mean 4h | median | win | p10 | worst | mean trail | trail win |
|---|---|---|---|---|---|---|---|---|
| all pumps (buy every one) | 796 | -3.89% | -5.56% | 24% | -14.2% | -42.0% | -3.22% | 29% |
| CNN says LONG | 164 | -4.95% | -5.80% | 23% | -15.8% | -33.4% | -4.85% | 24% |
| CNN says SHORT (avoid set) | 354 | -4.04% | -5.61% | 23% | -14.3% | -42.0% | -3.10% | 29% |
| CNN no trade | 278 | -3.07% | -5.15% | 27% | -13.2% | -24.4% | -2.42% | 31% |

CNN-LONG on Upbit: 4h mean 95% CI (day-clustered) [-6.49%, -3.32%]; trail [-6.35%, -3.20%].

Runtime 1199s.

## Reading (2026-10-04)

- Buying Upbit KRW pumps (+10% in 1h) at the next minute loses money: -3.9% mean over 4h, median -5.6%, only 24% winners (n=796, Mar-Oct 2026). The trailing exit only trims this to -3.2%.
- The F2 CNN does not transfer to Upbit. Its LONG picks are worse than buying every pump (-4.95%, 95% CI [-6.5%, -3.3%]); its SHORT and no-trade sets are no different from the average.
- Verdict: there is no Upbit-executable long version of F2. On Upbit the post-pump drift is strongly negative, which is a short signal Upbit spot cannot trade (cf. B37, F14). Long-only Upbit pump chasing should never be traded.
