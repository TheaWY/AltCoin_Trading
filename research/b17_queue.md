# B17 queue: remaining families (queued 2026-09-29)

| Family | Hypotheses | Status | Script |
|---|---|---|---|
| F14 learned exit (CQL / IQL / CVaR / joint entry) | 4 | queued, running first | scripts/b17_f14.py |
| F09 coordination | 4 of 6 | queued (002/003 need Telegram pump-call data) | scripts/b17_f09.py |
| F13 changepoint / shape triggers at 1s | 9 | queued (clean random null) | scripts/b17_f13.py |
| F20 on-chain | 5 of 8 | queued (003 stablecoins, 004 holders, 007 bridges: no data) | scripts/b17_f20.py |
| F11 execution | 5 of 6 | script next (001 maker fills approximated from trades; no bookTicker history) | scripts/b17_f11.py |
| F07 venue lead-lag | spot/Bybit/Hyperliquid cells + coinalyze 1h cells | needs Bybit + spot 1s backfill for the 10,200 random windows first | scripts/b17_f07.py |
| F16 Korea | 10 of 12 | needs a targeted Upbit/Bithumb minute backfill around pumps and random windows | scripts/b17_f16.py |
| F19 news/social | 004 (Binance announcements) now; rest blocked | CryptoPanic / Santiment / Telegram keys from 유리 | scripts/b17_f19.py |
| F17 microstructure | 10 | waits for 4-6 weeks of recorded order-book data (from about 2026-11-10) | - |

Runner: scripts/b17_queue.sh (launchd com.altcoin.b17queue, one family at a time, resumes by skipping finished reports).
