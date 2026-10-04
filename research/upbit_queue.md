# Upbit test queue (started 2026-10-04 at 유리's request)

Scope: re-run everything tested so far on UPBIT data, plus new Upbit-specific tests. LONG ONLY (Korea: no shorting).
Protocol for every test: pre-registered eras train 2024-01..2025-06 | val 2025-07..12 | TEST 2026 (touched once);
Upbit costs (2 x 0.05% fee + slippage by 24h value); variant chosen on validation; PASS = TEST day-clustered 95% CI > 0.
유리's rule: a very negative result means the opposite side is very positive. Every test reports the MIRROR (what the
opposite side earns after costs). Shorts cannot be placed on Upbit, so a strong mirror becomes (a) an AVOID rule for
long books and (b) a prompt for a long-only form of the same effect (e.g. pump fades -> buy after the fade).

| id | test | data | status |
|---|---|---|---|
| U0 | Upbit DB: 1h since 2024, pump events, 1s (last 3 months) + 1m windows, Binance spot 1h, Binance 1s proxy | Upbit/Binance public APIs | running |
| U1 | B43 hourly suite: crash rebound, -25%/24h dump rebound, pump (mirror), buy-after-fade, daily cross-section (reversal, momentum 1/7/28d, volume surge, low vol, lottery MAX), kimchi premium, hour/weekday timing, Binance-BTC lead, pump breadth | h1, bn_h1 | queued (after U0 h1 + bn_h1) |
| U2 | B42 F2U: pump long with LightGBM filter, entry delay 1-120 min, 5 exits | m1 windows | queued (after U0 m1) |
| U3 | B44 Upbit notices (re-run of F1/F11/F13 on Upbit): new KRW listings, warning designation and release, delisting notices; long-only entries at open / after fade | exchange_notices + m1 | queued (after U2) |
| U4 | B45 crash rebound on 1m (re-run of F3 on Upbit) with entry timing | crash events + m1 | if U1 F_crash promising |
| U5 | B46 first-minutes entry timing for pumps and fresh bursts (re-run of F12 on Upbit) | s1 + bn1s | after U2 |
| U6 | B47 Upbit share / Korea-led flows long-only (re-run of F7, F14 idea in long form) | h1 + bn_h1 | after U1 |
| U7 | B48 pairs, long-only laggard leg (re-run of F10 on Upbit) | h1 | after U1 |

Progress reports go to 유리 as each test finishes; results append to research/trial_ledger.csv.
