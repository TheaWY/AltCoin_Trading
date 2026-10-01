# Hypothesis compendium and data-split audit (2026-10-01)

## 1. What has been run

| Batch | Hypotheses / cells | Design | Split | Result |
|---|---|---|---|---|
| B1–B16 | 352 | event studies, factor sorts, AUC precursors, GRU/TCN pump models | discovery 2024-04..2025-08, holdout 2025-09..2026-09 | detection signals pass; 1 tradable pass (B6_3 crash rebound → F3) |
| B17 | 905 cells (45 ledger rows) | pre-registered grid, BH | discovery 2024-04..2025-12, validation 2026 | F04 oi_drop3 passed, then killed by OI 5-min timestamp fix |
| DISC1 | 56 variables | IC scan on residual returns, BH, weekly-block t | same as B17 | 27 BH, 14 confirmed predictive, 0 tradable |
| DISC2 | 18 | slow hold-band L/S (72h/168h) | same | 0/18 (best vshare_up 168h +0.4%/period, CI spans 0) |
| B18_F2FIX | 6 | filters on frozen pump CNN | disc = 2024 holdout, val = 2026 test | 0/6 |
| B19 | 100 | ML grid: 5 targets × 4 feature sets × ridge/LGBM/CatBoost/MLP/GRU | walk-forward quarterly refits from 2024-10, purged | 0/100 net (4 BH on discovery, none validated) |
| **Total** | **~1,437** | | | **0 tradable confirmed** |

Ledger: research/trial_ledger.csv (401 rows; grid batches logged as one row each).

## 2. Was the data sectioned correctly?

Within each test, yes:
- Discovery/validation split fixed before running; registry committed before results (B17 onward).
- Sign and thresholds chosen on discovery only; validation read once.
- B19: training rows purged so ts + horizon ≤ test start; no future rows in features; cross-sectional ranks computed per timestamp only.
- Features use data known at bar close (OI timestamp, Coinalyze bar-open, 1h label shift all fixed after audits).
- Survivorship: universe has 813 codes, 136 of which stopped trading >30d before panel end, so delisted coins are in the sample.

Weaknesses (honest):
1. **2026 validation slice is no longer clean.** B17, DISC1, DISC2, F2FIX and B19 all read it. Any further pass on 2026 is weak evidence. Only forward paper data is clean now.
2. **No latency in L/S backtests.** B19/DISC enter at the signal bar close. Optimistic by one bar.
3. **Delisting returns partly dropped.** Rows where the 24h forward return is NaN (coin delisted inside the window) are excluded: 9,204 of 5.4M rows (0.17%). Small, but it removes the worst outcomes for longs.
4. **DISC1 L/S traded raw variables while the IC was measured on residuals.** DISC2 fixed this by testing both.
5. **No global multiple-testing correction across batches.** BH is per batch. With ~1,437 tests, ~70 false passes at 5% are expected by chance.
6. Fixed defects (kept for record): OI 5-min timestamp, Coinalyze bar-open, log-return Jensen artefact, label 1h shift, outcome-selected negatives, entry look-ahead, close-only stops.

## 3. What survived

**Tradable after costs, confirmed by both backtest and forward: none.**

Forward paper (clean, out-of-sample by construction):

| ID | Strategy | Closed | Mean net | Status |
|---|---|---|---|---|
| F1 | Upbit notice | 6 | n/a (net60 NaN, needs fix) | collecting |
| F2 | pump CNN | 25 | +0.75% (sd 9.5%) | needs 300 for admission |
| F3 | crash rebound long | 2 | +18% | too few |
| F4 | spot-led | 14 | −5.2% (sd 24%) | failing |
| F5 | unlock short | 0 | – | collecting |
| F6 | OI drop short | – | – | invalidated |

Predictive only (real but not profitable after costs):
- Pump/dump precursors: depth-to-volume AUC 0.75/0.77, range 0.74, Upbit surge 0.66, B19 pump AUC 0.76–0.77, dump 0.78–0.79.
- Korea volume share (vshare_up) IC −0.038 discovery / −0.037 validation.
- oi_to_volume, B19 cross-sectional IC +0.10 to +0.15 (mostly low-vol effect).
- F19_004 delisting short: consistent sign, small n.

## 4. New edge-case tests (queued as B21_EDGE)

Applied to every survivor above (F2 backtest, B19 top 5, vshare_up, DISC2 best, F3 rule):

| ID | Test | Kill rule |
|---|---|---|
| E01 | 1-bar execution delay | net mean ≤ 0 |
| E02 | 2× costs (fee + slippage) | net mean ≤ 0 |
| E03 | drop top-5 PnL days | CI lower ≤ 0 and mean halves |
| E04 | weekend vs weekday | sign flips |
| E05 | funding-settlement hours (00/08/16 UTC ±1h) excluded | edge vanishes |
| E06 | coins <30 days since listing excluded | edge vanishes |
| E07 | include delisting rows (NaN fwd → last price or −100%) | sign flips |
| E08 | exchange outage / data-gap windows excluded | edge vanishes |
| E09 | regimes: BTC up/down 30d, high/low market vol | one regime carries >80% of PnL |
| E10 | shuffled-label placebo (200 perms) | real result not above 95th pct |
| E11 | capacity: size at 1% / 5% of dv24 with sqrt impact | net ≤ 0 at $10k per trade |
| E12 | fresh forward-only holdout from 2026-10-01 | the only test that can admit to main book |
| E13 | time-of-day split (Asia / EU / US session) | one session only |
| E14 | top-decile vs mid-cap only universe | edge only in illiquid tail |
| E15 | lookback sensitivity (±50% window) | sign flips |
