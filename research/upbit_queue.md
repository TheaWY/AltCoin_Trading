# Upbit test queue (started 2026-10-04 at 유리's request)

Scope: re-run everything tested so far on UPBIT data, plus new Upbit-specific tests. LONG ONLY (Korea: no shorting).
Protocol for every test: pre-registered eras train 2024-01..2025-06 | val 2025-07..12 | TEST 2026 (touched once);
Upbit costs (2 x 0.05% fee + slippage by 24h value); variant chosen on validation; PASS = TEST day-clustered 95% CI > 0.
유리's rule: a very negative result means the opposite side is very positive. Every test reports the MIRROR (what the
opposite side earns after costs). Shorts cannot be placed on Upbit, so a strong mirror becomes (a) an AVOID rule for
long books and (b) a prompt for a long-only form of the same effect (e.g. pump fades -> buy after the fade).

| id | test | data | status |
|---|---|---|---|
| U0 | Upbit DB: 1h since 2024, pump events, 1s (last 3 months) + 1m windows, Binance spot 1h, Binance 1s proxy | Upbit/Binance public APIs | done (2,649 pump events, 417 1s windows, 1,423 Binance 1s proxies, 205 Binance 1h) |
| U1 | B43 hourly suite: crash rebound, -25%/24h dump rebound, pump (mirror), buy-after-fade, daily cross-section (reversal, momentum 1/7/28d, volume surge, low vol, lottery MAX), kimchi premium, hour/weekday timing, Binance-BTC lead, pump breadth | h1, bn_h1 | DONE: 9/9 FAIL; avoid rules (no buys 2-24h after a +10% pump; none at premium >= +10%) |
| U2 | B42 F2U: pump long with LightGBM filter, entry delay 1-120 min, 5 exits | m1 windows | DONE: FAIL (after fixing a look-ahead bug in run 1) |
| U3 | B44 Upbit notices (re-run of F1/F11/F13 on Upbit): new KRW listings, warning designation and release, delisting notices; long-only entries at open / after fade | exchange_notices + m1 | done: FAIL (avoid new listings 72h) |
| U4 | B45 crash rebound on 1m (re-run of F3 on Upbit) with entry timing | crash events + m1 | SKIPPED: B43 crash rebound fails outside the 2024-12-03 martial-law night |
| U5 | B46 first-minutes entry timing for pumps and fresh bursts (re-run of F12 on Upbit) | s1 + bn1s | done: FAIL (all cells negative) |
| U6 | B47 Upbit share / Korea-led flows long-only (re-run of F7, F14 idea in long form) | h1 + bn_h1 | done: 4/4 FAIL (avoid Upbit laggard after Binance pump) |
| U7 | B48 pairs, long-only laggard leg (re-run of F10 on Upbit) | h1 | after U1 |

Progress reports go to 유리 as each test finishes; results append to research/trial_ledger.csv.
| U8 | B45 BTC regime filter on long books | h1 + B39 | done: trend-up useless; BTC<MA20 -> F2W forward |
