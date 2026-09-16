# Cursor prompt — diagnose the paper result, then run the fixed research loop

Use this after pulling `claude/update-result-reporting-r8tq2a`.

Order matters. The dashboard currently shows -7.8% against alts -1.2%, or
-17.0% per unit of deployed capital. Diagnosing that comes before running
anything new, because the answer decides what the new run is even for.

Paste everything inside the block below into Cursor chat.

```text
You are Cursor running on my Mac mini. You have the local repo and terminal.
Run the commands yourself; I am not at that machine.

Repo path:
/Users/pc/Projects/AltCoin_Trading

Branch to pull:
claude/update-result-reporting-r8tq2a

Repository:
TheaWY/AltCoin_Trading

## Context

Paper trading is at -7.8% while alts are -1.2% and BTC is -2.2%. At 46%
average deployment that is -17.0% per unit of deployed capital, so the
strategy lost roughly 15.8 percentage points more than holding the same
market. That is not tracking a down market, it is destroying value, and the
first job is to find out on what.

Five changes landed in this branch:

1. Research universe now inherits the live TRADING_SYMBOLS instead of
   defaulting to BTC/USDT,ETH/USDT, which is what it had silently been
   validating on.
2. Perpetual funding now accrues on every position, not only delta-neutral
   carry. Directional positions held up to 720h previously paid none.
3. Walk-forward windows 18 -> 39 (2.96y -> 6.41y), trial budget 13 -> 100,
   search space 9,216 -> 64. The search is completable for the first time.
4. New CROSS_SECTIONAL_MODE axis replacing CATEGORY_STRATEGY_MODE.
5. New cost attribution in src/research/paper_analysis.py, which is what
   Step 2 below uses.

## Hard rules

- Money-related trading code. No change to strategy, PnL, risk, backtest,
  portfolio or promotion logic without adding or updating a test first.
- Do not commit or push unless tests pass locally.
- Do not enable LIVE_TRADING. The live execution layer still has no durable
  order journal and no startup reconciliation.
- NEVER run scripts/reset_paper_portfolio.py, and never clear paper_trades,
  shadow_entries, prices or funding_rates. Those are observations, and the
  paper_trades record is the only genuinely out-of-sample data this project
  has. Losing it cannot be undone by re-running anything.
- Do not commit .env or secrets. Local Mac mini Postgres only.
- Report numbers, do not tune toward them. If something looks wrong, say so
  rather than adjusting a threshold until it looks right.

## Step 1 — pull and verify

cd /Users/pc/Projects/AltCoin_Trading
source .venv/bin/activate
git fetch origin
git switch claude/update-result-reporting-r8tq2a
git pull origin claude/update-result-reporting-r8tq2a

bash scripts/run_safety_tests.sh

Expect 105 tests, all passing. New files are tests/test_funding_accrual.py,
tests/test_cross_section.py, tests/test_cost_attribution.py and
tests/test_suite_hermetic.py. Unlike the container this was written in, you
have ccxt and .venv, so everything must pass. If anything fails, stop and
report it. Do not continue on a red suite.

Run it through scripts/run_safety_tests.sh, not bare `python -m unittest`.
The script exports CONFIG_SKIP_DOTENV=1, without which config's import-time
load_dotenv() pulls this machine's live paper-trading .env into the test
process and the suite ends up asserting against your settings rather than
against the code. tests/test_suite_hermetic.py fails loudly if that happens,
so a bare unittest run on a box with a .env is expected to report that one
failure; the fix is to use the script, never to skip the check.

## Step 2 — diagnose the -7.8% BEFORE running anything new

python -m src.research.paper_analysis | tee /tmp/paper_baseline.txt

Read the `attribution` and `turnover` sections first and report them in full.
The verdict field says which of two different failures this is:

  entries_have_no_edge   gross price PnL is negative before any cost. Trading
                         less does not fix this; the setups have to change.
  friction_dominates     gross is positive, costs took more than it made.
                         The entries are fine and the churn is the problem.
  edge_too_thin          gross positive but under the cost hurdle.

Also report, from the same output:
- breakeven_win_rate against the realized win_rate, and the gap between them
- turnover_x, friction_pct_of_capital, trades_per_day, avg_hold_hours
- the exits section: pnl by exit_reason, and how many stopped-out trades later
  reached their take profit within 72h

One caveat to state explicitly in your report: these trades were recorded
before the funding fix, so no funding was charged on them. The gross-vs-
friction split is still exact, because it uses only the stored pnl and fees,
but the true friction was understated by whatever funding those positions
would have paid. Do not silently present the numbers as if funding were
included.

## Step 3 — report the config that actually produced this

Do not guess from the repo defaults; print what is really set.

grep -E 'MAX_OPEN_POSITIONS|ALLOW_LONG|COOLDOWN_HOURS_PER_SYMBOL|RISK_PER_TRADE_PCT|MAX_POSITION_PCT|ACTIVE_TRADING_SYMBOLS_LIMIT|TRADING_SYMBOLS_LIMIT|MIN_CONFIDENCE|ATR_STOP_MULT|ATR_TP_MULT|SWING_MAX_HOLD_HOURS' .env

The dashboard shows "5 / 50" for open/max positions, which suggests
MAX_OPEN_POSITIONS is 50 while the repo default is 5. Confirm the real value.

If it is 50, say so plainly and include this in your report: alt perps run
around 0.8 average pairwise correlation, so fifty simultaneous positions have
a Grinold-Kahn effective breadth of 50/(1+49*0.8) = 1.24. The book behaves as
roughly one bet while paying fifty round trips of friction. That is the worst
of both: maximum cost, minimum diversification.

Do NOT change it yet. Step 2's verdict decides whether that is the primary
problem or a secondary one.

## Step 4 — load the history the new windows need

Required, not optional. Windows now reach back to 2020-01-04. Without the
data those windows return zero trades and pollute the positive_windows count
that promotion gate 1 reads, in the permissive direction.

python scripts/load_history.py --start 2020-01

Resumable and chunked; re-run if it dies. Then check real coverage:

python -m src.research.data_quality

Report the earliest timestamp per symbol. Most alts will not have 2020
history, which is expected and real. What matters is knowing which symbols
thin out and when. If a symbol has under ~2 years, say so; we may cut
RESEARCH_SYMBOLS_LIMIT rather than pretend those windows are evidence.

## Step 5 — invalidate the old experiment results

Every experiment in the table ran on BTC/ETH, without funding costs, against
a 2.96-year window set. None of it is comparable to what runs next, and
leaving it would let promotion gate 1 mix the two.

bash ops/backup.sh

Then clear ONLY these three tables, in a transaction, reporting row counts
before and after: experiments, fresh_evals, promotions.

Again: paper_trades, shadow_entries, prices and funding_rates stay.

## Step 6 — regenerate and run

bash scripts/run_research_now.sh

Report from the decisions log and the experiments table:
- total_space, space_exceeds_budget, newly_queued from the generator
  (expect space 64, budget 100, space_exceeds_budget false)
- experiments completed, failed, and the failure reasons
- per experiment: trade_count, expectancy, profit_factor, positive_windows,
  total_funding, funding_trades_observed

Two coherence checks that matter more than the PnL:

- total_funding must be non-zero and funding_trades_observed above zero for
  any config that held positions. Funding of exactly 0.0 with closed trades
  means funding_rates has no coverage for those windows and the accrual fix
  is silently inert. That is a data problem to fix, not a result to report.
- For CROSS_SECTIONAL_MODE=funding_rank runs, compare cohort_bars against
  cohort_skipped_bars. If nearly every bar was skipped, the cohort never met
  CROSS_MIN_COHORT=8 or CROSS_MIN_DISPERSION=0.0002 and the axis was never
  actually tested. Report the ratio. Do NOT widen those thresholds to force
  legs; that is fitting the gate to the data.

Also report cohort_one_legged_bars. Any config with ALLOW_LONG=false plus
CROSS_SECTIONAL_MODE=funding_rank is a short-only book wearing a
cross-sectional label, because dropping the long leg brings back the beta it
was supposed to cancel. Read those as directional, not relative value.

## Step 7 — stop and report

Do not apply config_overrides.json, do not change CROSS_SECTIONAL_MODE or
MAX_OPEN_POSITIONS in .env, do not touch CAPITAL_STAGE.

This is the first run where the search is completable and the costs are
honest. Whether the numbers are coherent comes before which config won.

## Known-open, do not fix in this pass

- Live execution has no durable order journal, no deterministic client order
  id, no startup reconciliation. LIVE_TRADING stays false.
- The live universe rotates every 6 hours by 24h dollar volume while the
  backtest takes a fixed symbol list. Composition now matches, rotation does
  not. A coin that pumps gains volume, enters the top 20, and becomes
  tradable, so the live system preferentially trades names right after a
  volume event and the backtest cannot see that effect. Likely fix is a
  liquidity floor (24h volume >= $50M) instead of a rank cut, because a
  threshold universe is reproducible in a backtest. Needs its own design
  pass; do not start it.
- positioning_short needs ~90 days of L/S ratio and Binance serves ~30. It
  will take months to reach 30 trades. Expected, not a bug.
```

## Reading the answer when it comes back

The Step 2 verdict decides everything downstream.

`friction_dominates` means the entries were net right and the book bled on
costs. Then MAX_OPEN_POSITIONS=50 is the primary suspect, and the repairs are
slot count, cooldown, and target width. Cross-sectional helps here too, since
it cuts position count while raising effective breadth.

`entries_have_no_edge` means none of those repairs matter. The setups
themselves are wrong and the research run in Step 6 is the thing that finds
out which, if any, of the four strategy families carries signal on the real
alt universe.

If funding comes back as zero across the board, the funding backfill jumps
the queue and nothing else is trustworthy until it is done.
