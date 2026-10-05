# Pre-registration v5: 100-rule long-only sweep on Upbit daily data (written 2026-10-05 15:40 KST, BEFORE running)

Asked by 유리: "make ~100 more tests and test them all". Running 100 rules at once is a multiple-testing problem, so the
protocol is a two-stage discovery/holdout design, fixed here and in the committed script (scripts/b54_sweep100.py,
STRATEGIES registry = the exact list; the commit hash before the first run is the registration).

## Data, execution, costs
Upbit KRW daily candles (data/upbit_db/d1), 2017-09..2026-10-04, current listings (survivorship biases alt families up,
for strategy and benchmark alike). Every rule outputs target weights decided at the close of day t (long only, sum <= 1,
rest KRW cash), held from the open of t+1 (open-to-open returns). Costs per unit of turnover: 0.05% fee + slippage tier
by 30d median daily value (as v2). No leverage, no shorts.

## Families (rule counts) and benchmarks
A  BTC/ETH timing alternatives to F17 (30): single SMA, EMA crosses, Donchian channels, time-series momentum, dual
   momentum, vol targeting, dip-in-uptrend, RSI, MACD, equity-drawdown brakes. Benchmark = F17.
B  Altcoin cross-section, weekly (25): momentum, skip-momentum, reversal, low volatility, low MAX, size, 52-week-high
   proximity, volume trend, BTC-gated baskets. Benchmark = EW top-30 alt basket (daily-rebalanced, same universe).
C  Alt-season rotation (10): hold the alt basket when the alt index beats BTC on trend, else F17. Benchmark = F17.
D  Breakout/surge portfolios (20): breakout window x volume multiple x exit rule, regime-gated. Benchmark = EW top-30.
E  Calendar (15): day-of-week, turn-of-month, month-of-year filters applied to F17. Benchmark = F17.
Total 100.

## Stage 1 discovery (2018-01-01 .. 2023-12-31)
Statistic: Sharpe(rule) - Sharpe(benchmark) on daily net returns; one-sided p from a centred stationary bootstrap
(mean block 20 days, 2,000 draws). Multiple testing: Benjamini-Hochberg-Yekutieli across all 100 at q <= 0.10.
Survivors = BHY-significant AND Sharpe diff > 0 AND maxDD not worse than benchmark by more than 10 points.

## Stage 2 holdout (2024-01-01 .. 2026-10-04), touched once, survivors only
PASS = Sharpe diff > 0 with one-sided bootstrap p < 0.05 / (number of survivors) AND maxDD not worse than benchmark by
more than 10 points. Holdout numbers for non-survivors are printed for transparency but cannot produce a PASS.

## After
PASS -> forward paper test on live data. Everything else logged in the ledger as one batch (B54, 100 trials) and not
re-tuned. Note: the project ledger already holds ~1,110 trials; the DSR with M = 1,210 is reported for any PASS.
