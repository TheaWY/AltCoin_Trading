# B57x Binance flow -> Upbit next-day relative return (prereg v9)

Universe: top-40 Upbit KRW coins with a Binance perp (30d median value, >= 90d listed). Decision 00:00 UTC,
trade Upbit 01:00 -> 01:00 UTC. Dev 2024-03-15..2025-09-30 (565 days), sealed 2025-10-01..2026-09-24 (opened once).
Script scripts/b57x_flow.py, numbers research/b57x_results.json.

## Univariate (dev, daily rank IC vs demeaned next-day return; BHY q<=0.10 over 8)
| feature | dev IC | p | BHY | sealed IC (info) |
|---|---|---|---|---|
| X8 Binance volume share vs Upbit | +0.081 | <0.001 | pass | +0.081 |
| X2 funding 24h | +0.024 | 0.006 | pass | +0.036 |
| X7 Binance volume surge | -0.013 | 0.10 | fail | +0.012 |
| X5 kimchi premium (rel.) | -0.011 | 0.17 | fail | -0.041 |
| X6 premium change 24h | +0.011 | 0.18 | fail | -0.002 |
| X1 taker buy share 24h | -0.008 | 0.27 | fail | +0.022 |
| X3 Binance-Upbit 24h gap | -0.007 | 0.37 | fail | +0.001 |
| X4 Binance-Upbit 1h gap | -0.002 | 0.82 | fail | -0.001 |

## Composite (selected ridge, dev IC 0.063 vs LightGBM 0.024)
- (a) Sealed composite IC +0.077, p < 0.001 -> PASS (information is real out of sample).
- (b) Long top-5 book vs EW universe (both gated by BTC trend): Sharpe -0.76 vs -0.59, diff -0.17, p=0.64 -> FAIL.
  Sealed period was weak for alts (F17 BTC/ETH: Sharpe 0.41, CAGR +5.8%, maxDD -19%).

## Reading
- Cross-venue lead (Binance moves first) and order-flow (taker share) show nothing at a 1-day horizon.
- The one strong signal is X8: coins whose trading is concentrated on Upbit relative to Binance underperform
  the next day; coins traded relatively more on Binance outperform. Quintile means (bps/day, low->high X8):
  dev -28, -5, +6, +1, +12; sealed -44, -37, -28, -12, -8. Monotone in both periods.
  Interpretation: Korea-specific retail concentration (local pumps) mean-reverts.
- The edge is mostly on the AVOID side (bottom quintile), which a top-5 long book does not exploit, and long
  exposure to alts lost money in the sealed year regardless.
- Usable form for a long-only trader: an avoid filter on alt exposure (drop the bottom X8 quintile). That rule
  was NOT pre-registered; the sealed data is now spent, so it can only be validated forward (paper).
