# Cursor prompt — deploy and validate the cross-sectional research loop

Use this after pulling `claude/update-result-reporting-r8tq2a`. It covers the
four changes in that branch (research universe, funding accrual, trial budget,
cross-sectional entries) and the work that has to happen on the Mac mini
before any of them produce trustworthy numbers.

Paste everything inside the block below into Cursor chat.

```text
You are Cursor running on my Mac mini. You have the local repo and terminal.
Run the commands yourself in Cursor's terminal; I am not at that machine.

Repo path:
/Users/pc/Projects/AltCoin_Trading

Branch to pull:
claude/update-result-reporting-r8tq2a

Repository:
TheaWY/AltCoin_Trading

## What changed and why (read before touching anything)

Four fixes landed. Three of them invalidate every experiment result currently
in the database, so the experiments table has to be cleared, not appended to.

1. RESEARCH UNIVERSE. RESEARCH_SYMBOLS defaulted to BTC/USDT,ETH/USDT and was
   set in no env file, so every walk-forward ran on two majors while the live
   cycle traded the top-20 alts. Setups gated on alt behaviour (failed_pump
   needs 7d >= +15%) barely fire on BTC/ETH, so their samples could never
   reach the 30-trade promotion gate. The default now inherits the live
   TRADING_SYMBOLS universe, truncated to RESEARCH_SYMBOLS_LIMIT=20.

2. FUNDING ACCRUAL. Funding reached PnL only for funding_carry trades in
   delta_neutral mode. Every other perpetual position accrued nothing while
   held up to 720h (90 settlements). At 0.01%/8h that is ~0.9% of notional
   per month, ~9% in a hot market, always omitted in the optimistic
   direction. src/engine/funding.py is now the single implementation for
   backtest, paper and live.

3. TRIAL BUDGET. research_space.yaml enumerated 9,216 combos against a
   MinBTL budget of 13, so the runner stopped after 13 and the "winner"
   would have been the best of an arbitrary queue prefix. Windows went
   18 -> 39 (2.96y -> 6.41y, budget 13 -> 100) and the space went
   9,216 -> 64. The generator now logs space_exceeds_budget if this recurs.

4. CROSS-SECTIONAL ENTRIES. New axis CROSS_SECTIONAL_MODE, replacing
   CATEGORY_STRATEGY_MODE. Alt perps at rho=0.8 give five directional
   positions an effective breadth of 1.19, so the book is one bet cut into
   five pieces. funding_rank ranks the cohort by funding and takes the
   extremes against each other so market beta cancels. Default is off.

## Hard rules (unchanged)

- Money-related trading code. Do not change strategy, PnL, risk, backtest,
  portfolio or promotion logic without adding or updating a test first.
- Do not commit or push unless tests pass locally.
- Do not enable LIVE_TRADING. The live execution layer still has no durable
  order journal and no startup reconciliation (audit P0 #3, still open).
- Do not commit .env or secrets.
- Local Mac mini Postgres only. Do not expose it publicly.
- Small, auditable changes over rewrites.

## Step 1 — pull and verify the branch

cd /Users/pc/Projects/AltCoin_Trading
source .venv/bin/activate
git fetch origin
git switch claude/update-result-reporting-r8tq2a
git pull origin claude/update-result-reporting-r8tq2a

bash scripts/run_safety_tests.sh

Expect 86 tests. The two new files are tests/test_funding_accrual.py (20
checks) and tests/test_cross_section.py (20 checks). Everything must pass on
the Mac mini — unlike the container this was written in, you have ccxt and
.venv, so there is no excuse for an error. If anything fails, stop and report
it. Do not proceed to Step 2 with a red suite.

Then confirm the invariants directly:

python -c "
from src.research import runner
from src.research.robust_stats import max_trials_for_history
import yaml
y=((runner.WINDOW_COUNT-1)*runner.WINDOW_STEP_DAYS+runner.WINDOW_TEST_DAYS)/365.0
n=1
for v in yaml.safe_load(open('research_space.yaml'))['axes'].values(): n*=len(v)
print('symbols:', runner.SYMBOLS)
print('history: %.2fy  budget: %d  space: %d  fits: %s' % (y, max_trials_for_history(y), n, n<=max_trials_for_history(y)))
"

Expect roughly 20 symbols, 6.41 years, budget 100, space 64, fits True.

## Step 2 — load the history the new windows need

This is required, not optional. The windows now reach back to 2020-01-04. If
the data is not there, those windows return zero trades and pollute the
positive_windows count that promotion gate 1 reads, which would make the gate
meaningless in the permissive direction.

python scripts/load_history.py --start 2020-01

It is resumable and chunked, so re-run it if it dies. When it finishes, check
actual coverage per symbol rather than assuming it worked:

python -m src.research.data_quality

Report back the earliest timestamp per symbol. Most alts will not have 2020
history — that is expected and real, not a bug. What matters is that you know
which symbols thin out and when, because early windows will be BTC/ETH-heavy
whether we like it or not. If a symbol has less than ~2 years, say so; we may
cut RESEARCH_SYMBOLS_LIMIT rather than pretend those windows are evidence.

## Step 3 — invalidate the old experiments

Every experiment currently in the table was run on BTC/ETH, without funding
costs, against a 2.96-year window set. None of it is comparable to what runs
next, and leaving it there would let promotion gate 1 mix the two.

Back up first, then clear experiments, fresh_evals and promotions. Do NOT
clear paper_trades, prices, funding_rates, or shadow_entries — those are
observations, not results, and shadow_entries in particular is the data that
answers MIN_CONFIDENCE without spending budget.

bash ops/backup.sh
# then clear the three result tables, in a transaction, and report row counts
# before and after.

## Step 4 — regenerate and run

bash scripts/run_research_now.sh

Then report, from the decisions log and the experiments table:
- total_space, space_exceeds_budget, newly_queued from the generator
- how many experiments completed, how many failed, and the failure reasons
- for each completed experiment: trade_count, expectancy, profit_factor,
  positive_windows, total_funding, funding_trades_observed

Two sanity checks that matter more than the PnL numbers:

- total_funding must be non-zero and funding_trades_observed must be greater
  than zero for any config that held positions. If funding is exactly 0.0
  with trades closed, the funding_rates table has no coverage for those
  windows and the accrual fix is silently inert — that is a data problem to
  fix, not a result to report.
- For CROSS_SECTIONAL_MODE=funding_rank runs, check cohort_bars against
  cohort_skipped_bars. If almost every bar was skipped, the cohort never met
  CROSS_MIN_COHORT=8 or CROSS_MIN_DISPERSION=0.0002 and the axis is not
  actually being tested. Report the ratio; do not tune the thresholds to
  force legs without telling me.

Also report cohort_one_legged_bars. Any config with ALLOW_LONG=false and
CROSS_SECTIONAL_MODE=funding_rank is a short-only book wearing a
cross-sectional label — the beta it was supposed to cancel is back. Those
configs must be read as directional, not as relative value.

## Step 5 — do not promote anything yet

Report the results and stop. Do not apply config_overrides.json, do not
change CROSS_SECTIONAL_MODE in .env, and do not touch CAPITAL_STAGE.

This is the first run where the search is completable and the costs are
honest. The first thing to look at is whether the numbers are coherent, not
which config won.

## Known-open, do not try to fix in this pass

- Live execution has no durable order journal, no deterministic client order
  id, no startup reconciliation. LIVE_TRADING stays false.
- The live universe rotates every 6 hours by 24h dollar volume while the
  backtest takes a fixed symbol list. The composition now matches; the
  rotation does not. A coin that pumps gains volume, enters the top 20, and
  becomes tradable, so the live system preferentially trades names right
  after a volume event and the backtest cannot see that effect. The likely
  fix is a liquidity floor (e.g. 24h volume >= $50M) instead of a rank cut,
  because a threshold universe is reproducible in a backtest and is not
  driven by short-term volume spikes. Do not implement it yet; it needs its
  own design pass.
- positioning_short needs ~90 days of L/S ratio and Binance serves ~30. It
  will take months to reach 30 trades. Expected, not a bug.
```

## After Cursor reports back

The decision points, in order:

1. If funding shows as zero across the board, the funding backfill is the next
   task and nothing else matters until it is done.
2. If cross-sectional skipped nearly every bar, the cohort thresholds need a
   design pass, not a tuning pass. Widening them to force legs would be
   fitting the gate to the data.
3. If the numbers are coherent, the universe rotation problem is next. It is
   the last known backtest/live mismatch.
