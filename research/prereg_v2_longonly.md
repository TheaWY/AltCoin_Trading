# Pre-registration v2: long-only Upbit allocation edges (written 2026-10-05 10:30 KST, BEFORE any data is pulled)

Why a v2: the ledger holds ~1,100 trials, almost all on 2024-05..2026-09 (~2.5 years, one regime cycle) and almost all
asking "does signal X predict the next 1h-1w return of an altcoin". After that many looks, any new short-horizon
variant that passes is more likely luck than edge. So v2 changes three things at once:
1. Longer, independent data: Upbit KRW DAILY candles from 2017 (four bear/bull cycles: 2018, 2020-21, 2022, 2024-26).
2. A different question: long-only ALLOCATION (when to hold coins vs KRW cash, and which coins), judged on risk-adjusted
   return against buy-and-hold, not per-trade alpha. This is the use a Korean long-only account can actually make.
3. Tiny family, no tuning: K = 3 hypotheses, each with parameters fixed here from the literature, ensembles instead of
   picking one lookback. Nothing below may be changed after the first result is seen.

## Common rules
- Data: Upbit /v1/candles/days, KRW markets, 2017-10-01 .. 2026-10-04 (UTC daily close). Stablecoins (USDT, USDC) excluded.
- Signal uses closes up to day t; position is taken at the OPEN of day t+1 (1-bar lag). No negative/forward indices.
- Costs per side: 0.05% fee + slippage tier by 30d median daily KRW value (>=1e11: 0.02%, >=1e10: 0.05%, >=1e9: 0.10%, else 0.20%).
- Cash earns 0.
- Eras for consistency: E1 2017-10..2019-12, E2 2020-01..2021-12, E3 2022-01..2022-12, E4 2023-01..2024-12, E5 2025-01..2026-10.
- Inference: stationary block bootstrap (Politis-Romano, mean block 20 days, 5,000 draws) on the DAILY return
  difference / Sharpe difference vs the benchmark. Bonferroni across K=3: one-sided p < 0.0167.
- Also reported: Deflated Sharpe Ratio of the strategy with M = 1,100 prior trials (Bailey & Lopez de Prado 2014), so the
  result is read against everything the project has already tried.
- Survivorship: Upbit lists only current markets, so dead coins are missing. This biases altcoin results UP for both the
  strategy and its benchmark. H1 (BTC, ETH) is not affected. H2/H3 are reported with this warning and are only adopted
  if the relative edge survives in every era.
- Final arbiter: a PASS is registered as a forward paper test (F15+) on live Upbit data from 2026-10-05; no capital
  decision is made on the backtest alone.

## H1 trend allocation on BTC and ETH (Moskowitz-Ooi-Pedersen 2012; Liu & Tsyvinski 2021 crypto TSMOM)
Per coin, weight w_t = mean over L in {20, 50, 100, 200} of 1[close_t > SMA_L(t)]  (0, .25, .5, .75 or 1).
Portfolio = 50/50 BTC/ETH sleeves, each sleeve holds w_t in the coin and 1 - w_t in cash. Rebalance daily, trade only if
|w change| >= 0.25 (it always is when it changes). Benchmark: 50/50 BTC/ETH buy-and-hold rebalanced monthly.
PASS = Sharpe(H1) - Sharpe(BH) > 0 with bootstrap p < 0.0167 AND max drawdown lower than BH in >= 4 of 5 eras.

## H2 weekly cross-sectional momentum, long-only (Liu, Tsyvinski & Wu 2022: 3-week momentum)
Universe each Monday: KRW coins with >= 180 days of history and 30d median daily value >= 1e9 KRW, top 30 by that value.
Rank by 21-day return ending Sunday close; hold the top tercile equal-weight Monday open to next Monday open.
Benchmark: equal-weight the same 30 coins, same schedule. Edge = H2 minus benchmark weekly return.
PASS = mean edge > 0 with bootstrap p < 0.0167 AND edge > 0 in >= 4 of 5 eras.

## H3 trend-gated altcoin basket (H1 rule applied coin by coin to the H2 universe)
Each of the 30 universe coins gets weight (1/30) x w_t(coin) with the same 4-lookback ensemble; rest in cash; daily.
Benchmark: equal-weight 30-coin buy-and-hold rebalanced weekly.
PASS = same as H1 (Sharpe difference p < 0.0167 AND lower max drawdown in >= 4 of 5 eras).

## What happens after
PASS -> forward paper test on live Upbit data, reviewed at 90 days. FAIL -> logged, not re-tuned.
Any extra analysis (other lookbacks, other universes) is labelled EXPLORATORY and cannot produce a PASS.
