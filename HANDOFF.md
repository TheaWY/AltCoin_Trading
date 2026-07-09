# HANDOFF — Self-Correcting Research Stack (커서에게 이 문서를 그대로 주세요)

You are receiving a pre-built, pre-tested research stack for this repo
(AltCoin_Trading, built against commit a21b509). Your job: apply the files,
verify, then implement the remaining tasks listed at the bottom. Do the tasks
IN ORDER, one commit each.

## What's in this drop (already built and tested — do not rewrite)

New modules (26 checks passing: scripts/test_promotion.py 8 + scripts/test_research_stack.py 18):

- `src/research/promotion.py` — champion-challenger promotion engine: 2-stage
  gates (walk-forward consistency + fresh-data), atomic config_overrides.json
  writes (whitelisted keys only), auto-rollback on live degradation, audit log.
- `src/research/event_study.py` — signal predictive-power validation: 5 signals,
  forward returns 4h/24h/72h vs baseline, bootstrap CI, regime split,
  de-clustering, PASS/fail verdicts persisted to event_study_results.
- `src/research/generator.py` + `research_space.yaml` — hypothesis space →
  deduplicated experiment queue with priority families and a nightly champion
  baseline entry.
- `src/research/runner.py` — nightly walk-forward runs via SUBPROCESS (config
  is import-time env, so each experiment runs in its own process with
  overrides injected as env vars), plus fresh evals (replaying done experiments
  over data collected after they were created).
- `src/research/paper_analysis.py` — confidence calibration, exit autopsy with
  MAE/MFE, "stopped trades that later hit TP" ratio, setup scorecard,
  regime split. Honest low-n flags.
- `src/research/data_quality.py` — freshness per source, gap scan into
  data_gaps, NTP offset check, single healthy/halt verdict.
- `scripts/run_backtest_once.py` — subprocess entrypoint printing compact JSON
  between markers.
- `ops/` — launchd plists (worker KeepAlive, research 01:00, watchdog 60s),
  nightly_research.sh (generate→run→promote→gap-scan→backup), watchdog.sh
  (restart.flag → kickstart + hourly health), backup.sh (sqlite → local+iCloud,
  keep 14), install.sh (pmset + plist install).

Modified files (additive only):

- `src/config.py` — overrides overlay after load_dotenv, whitelist enforced at
  read time too.
- `scripts/backtest.py` — result gains "closed_trade_pnls" (per-trade pnl/fees)
  so the runner can compute expectancy/PF. Nothing existing reads this key.

## Step 1 — Apply and verify

Unzip at repo root. Then:

    python scripts/test_promotion.py        # must print: All 8 checks passed.
    python scripts/test_research_stack.py   # must print: All 18 checks passed.
    python scripts/strategy_smoke.py        # existing smoke must still pass

If a modified file (config.py / backtest.py) conflicts with newer local
changes, re-apply the drop's additions onto the current version rather than
reverting local work. The additions are clearly commented.

## Step 2 — Remaining tasks (yours), in order

### Task A — Config knobs referenced by research_space.yaml
Add to config.py (env-backed) and wire into behavior:
- `SETUP_MEANREV_ENABLED`, `SETUP_BREAKOUT_ENABLED`, `SETUP_TSMOM_ENABLED`,
  `SETUP_FUNDING_ENABLED` (all default true): each _*_setup function in
  src/engine/evaluation.py returns None immediately when its flag is off.
- `MIN_CONFIDENCE` (default: current behavior): evaluation/paper_trader gate.
- `COOLDOWN_HOURS_PER_SYMBOL` (default 0 = off): paper trader refuses a new
  entry for a symbol within N hours of that symbol's last entry.
- `FEE_MODE` ("taker"|"maker"): the backtest portfolio and paper trader use
  maker rates (0.02% futures) when "maker". Keep taker as default.
These MUST affect both live and backtest identically (shared code path).

### Task B — Wire data health into the cycle
In src/engine/cycle.py, call `src.research.data_quality.verdict()` before
signal evaluation; when not healthy, skip NEW entries this cycle (existing
positions still manage exits) and record the reason. Non-fatal on error.

### Task C — Dashboard: 실험 section under the 전략 tab
API endpoint(s) in src/api/dashboard_data.py exposing:
- experiments table (status, priority, created/finished, aggregate metrics,
  positive_windows "5/6"), diff vs current champion config instead of full
  config, expandable detail with per-window results.
- totals bar: experiments run / queued / failed — never hide failures.
- event_study_results table: signal × horizon × regime grid with verdict
  badges (PASS green, fail gray, insufficient_n muted).
Render in Korean, matching the existing 전략 tab style.

### Task D — Dashboard: new final tab "히스토리"
Timeline from the promotions table: each promote/rollback event with reason,
applied config diff, and the live performance of that config between this
event and the next (from paper_trades in that window). Weekly report cards
(experiments run, best candidate, champion performance) auto-generated.
Include a manual rollback button calling promotion.rollback (POST, confirm
dialog) and a data-freshness status board from data_quality.freshness().

### Task E — Historical data loader
scripts/load_history.py: download data.binance.vision monthly 1h klines +
funding for top-20 USDT perp symbols, 2020-01 → now, into prices/funding_rates
(INSERT OR IGNORE, chunked, resumable). Respect the existing schema. Then
RESEARCH_SYMBOLS can widen and walk-forward windows can extend to 2020.

### Task F — Capital stage gates (dashboard gauge)
Config: CAPITAL_STAGE (paper|live_150|live_400|live_full), promotion criteria
per stage (>=60 trades, expectancy>0, maxDD<=12% for 2 consecutive months),
demotion (DD>15% or 30-trade rolling expectancy<0). Compute current stage
progress from paper_trades and show as a gauge on 히스토리 tab. Do NOT
implement live order routing in this task — display and criteria only.

## Constraints (unchanged, non-negotiable)
- Deterministic only; no LLM in any decision loop.
- Backtest/paper/live share one code path; any behavior knob must apply to all.
- Held-out data (RESEARCH_HOLDOUT_START, default 2026-06-01) is never used by
  research runs; the runner already enforces the boundary — keep it.
- All thresholds via env/config. Ask before adding dependencies (pyyaml is
  the only new one this drop requires).

## Mac Mini에서 켜는 법 (사람용 메모)
    bash ops/install.sh        # pmset + launchd 3종 설치
    # 이후: 매일 01:00 자동 리서치 배치, 워커 상시, 워치독 60초
    # 수동 실행: bash ops/nightly_research.sh
    # 상태 확인: python -m src.research.promotion status
    #           python -m src.research.data_quality
    #           python -m src.research.paper_analysis
