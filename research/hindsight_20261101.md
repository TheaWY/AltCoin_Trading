# Hindsight-optimal portfolios vs. my chosen books (pre-registered 2026-11-01)

Question from 유리: looking at the history, what should I have bought to make the most money?
Build several "optimal" portfolios under different concepts, compare them to the book I actually
chose (F19; also F17, F18b), and find out how to shrink the gap.

Status: methodology written and committed BEFORE any oracle numbers were computed.
Results section is empty at commit time and is filled in afterwards, in a separate commit.

## 0. Ground rules

- [x] Paper only. Nothing here changes a live or paper service. Outputs are test candidates.
- [x] Same engine everywhere: `scripts/b54_sweep100.py` `run(W)` with `B51_END = 2026-10-31`
      (completed daily candles only, Upbit KRW, open-to-open returns, weights decided at close t
      and applied from open t+1, cost = fee 5 bp + slippage tier by 30d median value).
- [x] Oracles are allowed to look at the future. Benchmarks and my books are not. Every table
      says which side a row is on.
- [x] Two windows. A = 2026-10-06..2026-10-30 (the forward paper window, 24 trading days).
      B = 2024-01-01..2026-10-30 (robustness; anything claimed from A must be checked on B).
- [x] 24 days is a tiny sample. No concept is "better" from window A alone. Window A tells us
      where the money went; window B tells us whether a signal could have found it.
- [x] Universe U30 = top-30 alts by 30d median KRW value with >= 180 days of history, plus BTC and
      ETH, plus cash. Same as the B54 sweep. Oracles that pick "any coin" pick from this set so
      that the comparison is fair (a 2-day-old listing is not something my books could own either).

## 1. Oracles (look-ahead allowed, upper bounds)

- [x] O1  Best single coin. Buy-and-hold each coin in U30 over window A; report the top 5 and the
      bottom 5. Upper bound for "what should I have bought once".
- [x] O2  Perfect daily switch. Each day, 100% in the coin (or cash) with the highest next-day
      return. Pure look-ahead; shows the total daily-selection money on the table.
- [x] O3  Perfect BTC timing. BTC or cash each day based on BTC's next-day sign. Isolates the
      exposure/timing component for the simplest possible asset.
- [x] O4  Perfect cash timing of F19. Keep F19's own weights but scale exposure to 1 or 0 by the
      sign of F19's next-day gross return. Measures how much of my loss was "right coins, wrong
      days" vs. "wrong coins".
- [x] O5  Ex-post max-Sharpe static long-only weights over window A, with a max-drawdown cap of
      10%. A realistic-looking but still hindsight portfolio. Solved by grid/SLSQP over U30.
- [x] O6  Best of the 100 pre-registered B54 strategies, chosen in hindsight on window A. This is
      the honest "if I had picked the right one of my own candidates" bound. Also report the
      median and worst so the selection effect is visible.

## 2. Ex-ante benchmarks (no look-ahead)

- [x] O7  Momentum top-k. Each day hold equal weight in the k coins (k = 3, 5, 10) with the best
      trailing 20d return, computed from candles that were complete at decision time.
- [x] My books as recomputed statelessly: F19 (chosen), F17, F18b. Plus BTC hold, EW30, cash.
- [x] Record for every row: total return, max drawdown, daily Sharpe (annualized), number of
      rebalances, total cost paid, average exposure.

## 3. Gap decomposition (F19 vs. each oracle)

For each day t and portfolio P with weights W_P and gross returns R:

- [x] Exposure/timing gap  = (exp_oracle - exp_F19) x R_market, where exp = sum of non-cash
      weights and R_market = EW30 return. Was I in the market on the wrong days?
- [x] Selection gap        = sum over coins of (w_oracle_i - w_F19_i) x (R_i - R_market),
      scaled to F19's exposure. Did I pick the wrong coins given I was invested?
- [x] Concentration gap    = selection gap of the oracle vs. an equal-weight version of the
      oracle's own holdings. How much came from betting big on one name?
- [x] Cost gap             = cost_F19 - cost_oracle.
- [x] Residual (interaction) should be small; report it.
- [x] Output: one table per oracle, one row per day, plus column sums. And a one-line answer:
      which single bucket explains most of the gap.

## 4. Closability test (window B, ex-ante only)

The oracle tells us where the money was. This step asks whether anything knowable at close t-1
pointed at it.

- [x] Build the daily cross-section on U30 over window B: next-day open-to-open return as target.
- [x] Candidate signals, all computed from completed candles only: trailing 1d/5d/20d/60d return,
      20d volatility, 20d value z-score, distance to 20d high, SMA stack score (the F15 w),
      BTC trailing 5d return (market state), day of week.
- [x] For each signal: Spearman IC per day, mean IC and t-stat over window B, quintile long-only
      spread net of the engine's cost model, and the same stats restricted to window A.
- [x] Market timing: regress next-day EW30 return on BTC trailing return, realized vol and the
      F17 overlay state. Report whether any coefficient has |t| > 2 on window B.
- [x] Decision rule, fixed now: a gap source is "closable" only if a signal has mean IC t-stat
      > 2.5 on window B AND the net quintile spread is positive in at least 60% of calendar
      months. Otherwise the gap is labelled "luck / not knowable" and no change is proposed.

## 5. Report

- [x] Fill the Results section below. Oracle rows marked (hindsight), others (ex-ante).
- [x] Recommendations only as pre-registered test candidates with their own B-series id, never
      as a change to a running book.
- [x] Log to research/trial_ledger.csv. Korean summary to 유리.

## Results (filled 2026-11-01 00:20 KST, scripts/b_hindsight.py + b_hindsight_close.py, commits f0ce9fd..)

Full tables: research/hindsight_results_20261101.md, research/hindsight_close_20261101.md.

### What the money looked like in window A (10/06..10/29, 24 days)

- Every one of the 32 tradable coins (U30 + BTC + ETH) lost money buy-and-hold. Best: BTC -9.3%. Median -25.7%.
  Worst: FLOCK -44.4%, SOPH -44.0%.
- O5 (ex-post max-Sharpe, long-only, DD cap) returned 100% cash. There is NO static long-only portfolio of these
  coins that made money in October, with or without hindsight. "What should I have bought" has one answer: nothing.
- O6: 0 of the 100 pre-registered B54 strategies were positive. Best A_macd 0.0% (flat the whole month), median -9.0%,
  worst B_own_trend -41.2%. F19 at -7.3% is in the top 20 of my own candidate set.
- Money existed only in daily switching: O2 (perfect coin each day) +248%, O3 (perfect BTC on/off) +5.9%,
  O4 (F19 with perfect cash timing) +2.2%. Pure look-ahead.
- Ex-ante momentum top-k (O7) was the worst thing one could have done: -35% to -49%.

### Scoreboard (ex-ante rows)

F18b -6.2% (maxDD -4.5%) > F19 -7.3% (-7.2%) > F17 -8.7% > BTC -9.3% > ETH -17.0% > EW30 -28.9% > O7 -35..-49%.
Cash 0% beat all of them.

### Gap decomposition, F19 vs oracles (exact split, no residual)

| oracle | total gap | exposure/timing | selection | cost |
|---|---|---|---|---|
| O1 best coin (BTC hold) | -2.0% | -6.4% | +4.4% | 0 |
| O2 perfect daily switch | +136.9% | +2.3% | +139.4% | -4.8% |
| O3 perfect BTC timing | +13.3% | +14.4% | -0.1% | -0.9% |
| O4 F19 perfect cash timing | +9.7% | +10.1% | 0 | -0.4% |
| O5 / O6 (= cash) | +7.5% | +7.4% | 0 | +0.1% |

Reading: against every achievable-looking oracle (O3, O4, O5, O6) the whole gap is exposure. F19 held 44% average
exposure in a month where the market only went down. Coin selection inside F19 was fine (vs BTC hold it was +4.4%,
mainly because the F17 overlay cut exposure on the worst days). Cost is not the problem (0.1% over 24 days).
The only selection gap is O2, which is not a strategy; it is the sum of the best daily move in a 32-coin universe.

### Closability (window B, 2024-01..2026-10, ex-ante, 1033 days)

- Market timing: next-day EW30 return on BTC 5d/20d return, realized vol, F17 overlay state, alt-index 5d return.
  All |t| < 1. Nothing knowable at close t-1 predicted whether the alt market would rise the next day.
  => the exposure gap (the whole gap vs O3/O4/O5/O6) is labelled LUCK / NOT KNOWABLE. No change proposed.
- Cross-section: 1 of 9 signals passes the pre-registered rule. Low 20d volatility: IC -0.103, t = -12.2, net
  quintile spread vs EW30 +38%/yr, positive in 68% of months (23/34). In window A: +116%/yr relative (i.e. lost less).
  Everything else fails: short-term reversal signals lose 40-140%/yr after costs at daily rebalance (cost-dominated),
  SMA stack and 60d return have decent IC but under 60% positive months.
- Caveat on the one pass: it is RELATIVE to EW30. B_lowvol30/90 in B54 still lost 25% in window A, and B54's holdout
  already rejected the low-vol basket on absolute Sharpe. Low vol tells you which alts to hold IF you hold alts; it does
  not tell you whether to hold alts.

### Decision

- No live or paper change to F19/F17/F18b. The chosen book's loss was exposure in a down month, not a fixable mistake.
- The result that actually matters: cash beat all 100 candidates and all 32 coins. The forward test is answering the
  question "does the overlay cut exposure fast enough", and 44% average exposure at -7.3% says: partly.
- Pre-registered test candidates (new ids, backtest first on 2024-26, then forward):
  - B59_lowvol_sat: F19 with the 20% satellite restricted to the lowest-vol tercile of its own breakout candidates.
    Expected effect: smaller satellite drawdown, no change in exposure. Pass rule: holdout Sharpe >= F19 and maxDD lower.
  - B60_exposure_cap: F17 core capped at 50% when the EW30 20d return is < -10% (crude down-market exposure cap).
    This IS a timing rule, and section 4 says timing had no signal, so the prior is that it fails. It is listed so that
    the question "should I just hold less" gets a pre-registered answer instead of a feeling.
- Re-run this whole file on 2026-12-01 with window A = 10/06..11/30. 24 days decided nothing except that October was
  a month to be in cash.
