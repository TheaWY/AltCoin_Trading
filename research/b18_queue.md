# B18 queue (2026-09-30)

Everything still open from B17 and the discovery engine, plus the main-book loss follow-ups. One heavy job at a time.

| id | hypothesis | status | trigger / blocker |
|---|---|---|---|
| B18_F2FIX | pre-registered filters for the pump CNN (tight p, short-only, breadth, BTC, 24h cooldown, vol sizing) | done 2026-09-30: 0/6 pass | - |
| B18_DISC2 | slow (72h / 168h) hold-band L/S on the residual Korea volume share and OI/volume, plus a combo | done 2026-10-01: 0/18 pass (Upbit share 168h +0.46%/+0.41% per week, CI incl. 0) | - |
| B19 | 100 ML hypotheses (targets x feature sets x models, walk-forward) | done 2026-10-01: 0/100 pass; IC +0.10..0.15, AUC 0.76-0.79, but quintile L/S loses to costs | - |
| B20_COSTAWARE | cost-aware books on B19 + vshare + OI/vol | done: 0/4; VSH K4 positive both periods (+12 bp/day), CI spans 0 |
| B18_F07B | venue lead-lag at 1m/5m/15m for Binance spot and Bybit (the B17 F07 cells that had no data) | queued | needs a 1m spot/Bybit backfill (public archives) |
| B18_F16_010 | Korean-listed coins with a thin Upbit book before pumps | waiting for data | Korea tick recorder since 2026-09-27; runnable ~2026-10-25 |
| B18_F17 | order-book microstructure (10 statements) | waiting for data | recorded book depth; runnable ~2026-11-10 |
| B18_F19 | news / social (11 statements) | blocked | CryptoPanic / Santiment / Telegram keys from 유리 |
| B18_F09 | coordination 002/003 | blocked | Telegram pump-call data |
| B18_F20 | on-chain 003 stablecoins / 004 holders / 007 bridges | blocked | no data source yet |
| B18_F19_004 | Binance delisting SHORT +1h (same sign both periods, n=40/68) | live logging via F1 | review at >= 30 new events |
| F2 forward | registered decision: keep only if mean net > 0 with day-clustered CI above 0 after >= 300 paper trades | 16 trades so far | ~6-8 weeks |

Rules: pre-register before running, commit scripts first, discovery 2024-04..2025-12 / validation 2026, day- or period-clustered CIs, costs included, log every result to research/trial_ledger.csv.
| B21_EDGE | 15 edge-case tests on survivors | done: predictive 4/4 survive, books 0/3 + F2 not confirmed; vshare_up -> F7 |
| F1_FIX | stale reposts filtered, NaN -> NULL | done |
| F7 forward | weekly vshare_up L/S paper, Thursdays | live from 2026-10-01 |
