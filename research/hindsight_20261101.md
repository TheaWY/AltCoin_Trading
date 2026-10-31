# Hindsight-optimal portfolios vs. my chosen books (pre-registered 2026-11-01)

Question from 유리: looking at the history, what should I have bought to make the most money?
Build several "optimal" portfolios under different concepts, compare them to the book I actually
chose (F19; also F17, F18b), and find out how to shrink the gap.

Status: methodology written and committed BEFORE any oracle numbers were computed.
Results section is empty at commit time and is filled in afterwards, in a separate commit.

## 0. Ground rules

- [ ] Paper only. Nothing here changes a live or paper service. Outputs are test candidates.
- [ ] Same engine everywhere: `scripts/b54_sweep100.py` `run(W)` with `B51_END = 2026-10-31`
      (completed daily candles only, Upbit KRW, open-to-open returns, weights decided at close t
      and applied from open t+1, cost = fee 5 bp + slippage tier by 30d median value).
- [ ] Oracles are allowed to look at the future. Benchmarks and my books are not. Every table
      says which side a row is on.
- [ ] Two windows. A = 2026-10-06..2026-10-30 (the forward paper window, 24 trading days).
      B = 2024-01-01..2026-10-30 (robustness; anything claimed from A must be checked on B).
- [ ] 24 days is a tiny sample. No concept is "better" from window A alone. Window A tells us
      where the money went; window B tells us whether a signal could have found it.
- [ ] Universe U30 = top-30 alts by 30d median KRW value with >= 180 days of history, plus BTC and
      ETH, plus cash. Same as the B54 sweep. Oracles that pick "any coin" pick from this set so
      that the comparison is fair (a 2-day-old listing is not something my books could own either).

## 1. Oracles (look-ahead allowed, upper bounds)

- [ ] O1  Best single coin. Buy-and-hold each coin in U30 over window A; report the top 5 and the
      bottom 5. Upper bound for "what should I have bought once".
- [ ] O2  Perfect daily switch. Each day, 100% in the coin (or cash) with the highest next-day
      return. Pure look-ahead; shows the total daily-selection money on the table.
- [ ] O3  Perfect BTC timing. BTC or cash each day based on BTC's next-day sign. Isolates the
      exposure/timing component for the simplest possible asset.
- [ ] O4  Perfect cash timing of F19. Keep F19's own weights but scale exposure to 1 or 0 by the
      sign of F19's next-day gross return. Measures how much of my loss was "right coins, wrong
      days" vs. "wrong coins".
- [ ] O5  Ex-post max-Sharpe static long-only weights over window A, with a max-drawdown cap of
      10%. A realistic-looking but still hindsight portfolio. Solved by grid/SLSQP over U30.
- [ ] O6  Best of the 100 pre-registered B54 strategies, chosen in hindsight on window A. This is
      the honest "if I had picked the right one of my own candidates" bound. Also report the
      median and worst so the selection effect is visible.

## 2. Ex-ante benchmarks (no look-ahead)

- [ ] O7  Momentum top-k. Each day hold equal weight in the k coins (k = 3, 5, 10) with the best
      trailing 20d return, computed from candles that were complete at decision time.
- [ ] My books as recomputed statelessly: F19 (chosen), F17, F18b. Plus BTC hold, EW30, cash.
- [ ] Record for every row: total return, max drawdown, daily Sharpe (annualized), number of
      rebalances, total cost paid, average exposure.

## 3. Gap decomposition (F19 vs. each oracle)

For each day t and portfolio P with weights W_P and gross returns R:

- [ ] Exposure/timing gap  = (exp_oracle - exp_F19) x R_market, where exp = sum of non-cash
      weights and R_market = EW30 return. Was I in the market on the wrong days?
- [ ] Selection gap        = sum over coins of (w_oracle_i - w_F19_i) x (R_i - R_market),
      scaled to F19's exposure. Did I pick the wrong coins given I was invested?
- [ ] Concentration gap    = selection gap of the oracle vs. an equal-weight version of the
      oracle's own holdings. How much came from betting big on one name?
- [ ] Cost gap             = cost_F19 - cost_oracle.
- [ ] Residual (interaction) should be small; report it.
- [ ] Output: one table per oracle, one row per day, plus column sums. And a one-line answer:
      which single bucket explains most of the gap.

## 4. Closability test (window B, ex-ante only)

The oracle tells us where the money was. This step asks whether anything knowable at close t-1
pointed at it.

- [ ] Build the daily cross-section on U30 over window B: next-day open-to-open return as target.
- [ ] Candidate signals, all computed from completed candles only: trailing 1d/5d/20d/60d return,
      20d volatility, 20d value z-score, distance to 20d high, SMA stack score (the F15 w),
      BTC trailing 5d return (market state), day of week.
- [ ] For each signal: Spearman IC per day, mean IC and t-stat over window B, quintile long-only
      spread net of the engine's cost model, and the same stats restricted to window A.
- [ ] Market timing: regress next-day EW30 return on BTC trailing return, realized vol and the
      F17 overlay state. Report whether any coefficient has |t| > 2 on window B.
- [ ] Decision rule, fixed now: a gap source is "closable" only if a signal has mean IC t-stat
      > 2.5 on window B AND the net quintile spread is positive in at least 60% of calendar
      months. Otherwise the gap is labelled "luck / not knowable" and no change is proposed.

## 5. Report

- [ ] Fill the Results section below. Oracle rows marked (hindsight), others (ex-ante).
- [ ] Recommendations only as pre-registered test candidates with their own B-series id, never
      as a change to a running book.
- [ ] Log to research/trial_ledger.csv. Korean summary to 유리.

## Results

(empty at pre-registration)
