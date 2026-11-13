# B17 queue: remaining families (queued 2026-09-29)

| Family | Hypotheses | Status | Script |
|---|---|---|---|
| F14 learned exit (CQL / IQL / CVaR / joint entry) | 4 | queued, running first | scripts/b17_f14.py |
| F09 coordination | 4 of 6 | queued (002/003 need Telegram pump-call data) | scripts/b17_f09.py |
| F13 changepoint / shape triggers at 1s | 9 | queued (clean random null) | scripts/b17_f13.py |
| F20 on-chain | 5 of 8 | queued (003 stablecoins, 004 holders, 007 bridges: no data) | scripts/b17_f20.py |
| F11 execution | 4 of 6 | done: 0 pass (applied to the oi_drop3 short, itself voided) | scripts/b17_f11.py |
| F07 venue lead-lag | 18 cells now (all 1h cells + Upbit/Bithumb 1m/5m/15m); spot/Bybit sub-hour cells need a 1m backfill | queued | scripts/b17_f07.py |
| F16 Korea | 11 of 12 (hourly Korean data; 012 on kr1m_hist) | queued | scripts/b17_f16.py |
| F19 news/social | 004 queued; 11 blocked | CryptoPanic / Santiment / Telegram keys from 유리 | scripts/b17_f19.py |
| F17 microstructure | 10 | waits for 4-6 weeks of recorded order-book data (from about 2026-11-10) | - |

Runner: scripts/b17_queue.sh (launchd com.altcoin.b17queue, one family at a time, resumes by skipping finished reports).

Audit 2026-09-29: Vision metrics create_time T = OI measured ~T+5 min (voided the oi_drop3 short). metrics1h / alpha_lab / deep_search are aligned; futures_metrics (load_metrics.py) stamps the hour start with the last sample (1 h early) but nothing reads it. coinalyze_1h is stamped at bar OPEN (+3600 applied in F07).
