# 4-week forward review, 2026-11-03 (books registered 2026-10-05/06, forward days from 2026-10-06)

Window: 10/06..11/02 for F15/F17 (28 trade days, f15_trend_paper), 10/06..11/01 for F18/F19/F19x/EW30 (27 days,
data/forward/f18_breakout.parquet, recorded one day late by design). Paper only. No rule changed.
Caveat that overrides everything below: 28 days is 1/13 of the shortest decision horizon (365 days for F15/F17, 180 for
F18/F19, 90 for F19x). Nothing here passes or fails; the table says whether each book is behaving the way its
registration said it would in a down month. Market context: BTC -10.6% (116.0M -> 103.7M), 50/50 BTC/ETH -13.9%,
EW top-30 alts -26.4%. Data note: Upbit daily candles were refilled for the 10/19-10/30 outage, so every book is
recomputed statelessly over the full window; F19x had no X8 snapshots on 10/19-10/30 (treated as F19 on those days).

| book | total | maxDD | Sharpe (ann.) | worst day | worst week | backtest expectation (from forward.yaml) |
|---|---|---|---|---|---|---|
| F15 trend BTC/ETH | -9.4% | -9.3% | -7.5 | -3.1% | -4.2% | Sharpe 1.55, maxDD -41% over 2018-26; pass = DD < 50/50 B&H and Sharpe within 0.3 of B&H |
| F17 = F15 + overlay | -9.1% | -9.0% | -7.5 | -3.0% | -4.2% | Sharpe 1.72, worst day -9.6%, worst 30d -21.4%; pass = DD <= F15 and Sharpe within 0.2 of F15 |
| F18a breakout trail | -14.1% | -13.0% | -6.1 | -5.6% | -9.4% | holdout +0.4%/yr absolute, Sharpe diff vs EW30 +0.94 |
| F18b breakout own-trend | -6.2% | -4.5% | -5.6 | -2.7% | -4.2% | holdout +6.9%/yr absolute, Sharpe diff vs EW30 +1.10 |
| F19 (chosen) 80% F17 + 20% sat | -7.6% | -7.4% | -7.6 | -2.4% | -3.4% | 2024-26 CAGR +31%, maxDD -21%, Sharpe 1.42; pass = maxDD <= F17 |
| F19x = F19 + X8 avoid | -7.6% | -7.4% | -7.6 | -2.4% | -3.4% | promote only if >= F19 on return and maxDD and removed alts underperform kept |
| 50/50 BTC/ETH hold | -13.9% | -13.8% | -7.1 | -3.5% | -4.3% | benchmark for F15/F17 |
| EW30 alt basket | -26.4% | -27.4% | -6.7 | -8.6% | -13.0% | registered benchmark for F18 |
| BTC hold | -10.6% | -10.6% | -6.1 | -3.3% | -3.0% | 유리's benchmark request (10/31) |

## Book by book

F15 / F17. Behaving as registered: the point of both is drawdown control, and in a month where 50/50 buy-and-hold lost
13.9% they lost 9.4% / 9.1% with a 9.3% / 9.0% drawdown. The overlay (F17) added only +0.26% over F15: the crash brake
fired on 2 days and the vol scaling cut exposure from 50% to 48.5% on average. The F17 Sharpe is 0.35 below B&H, which
would miss the "within 0.3" clause if the window were 365 days; with 28 days the Sharpe numbers are noise (all books
sit between -5.6 and -7.6 because every book lost on most days). KEEP, no decision needed.

F18a / F18b. Both beat the registered benchmark (EW30 -26.4%) by 12 and 20 points. F18a's 20% trailing exit let the
early-October breakouts give back -14%; F18b's own-trend filter held it to -6.2% with the smallest drawdown of any book
(-4.5%) and it has been fully in cash since 10/22. F18b also beat F17 this month (the backtest said it would not, over
years). KEEP both; F18a is the weaker one and the data say so already.

F19. Did what 유리 chose it for: maxDD -7.4% vs F17 -9.0%, return -7.6% vs -9.0%. The satellite was on for only the
first week, then the alt-index crash brake and SMA10 exits put it in cash; the core carried the rest. The 10/31
hindsight study (research/hindsight_20261101.md) showed the entire gap to cash was exposure in a down market and
that no ex-ante signal timed it. KEEP.

F19x. Identical to F19 on every day (max abs daily difference 0.0). The avoid filter never removed a held alt:
the satellite held alts only during 10/06-10/13, the X8 bottom-quintile set did not overlap them, and there were no
snapshots during the outage. 680 X8 snapshots (17 days x 40 coins) are banked. Nothing to evaluate yet; the 90-day
check stays at 2027-01-04. KEEP running (it is free).

## Versus BTC hold

유리 asked on 10/31 whether the books were worse than just holding BTC. Over the full 28 days they were not: BTC
hold -10.6% vs F19 -7.6%, F18b -6.2%, F17 -9.1%. The ranking flipped after 10/29 when BTC dropped another 8%. This
is one month; the books' long-run case is drawdown, not beating BTC in rallies (the backtests say they lag in bulls).

## Decisions

- No rule changes. Next scheduled reviews: F19x 90-day 2027-01-04; F15/F17 90-day 2027-01-04; F18/F19 180-day 2027-04-04.
- Suggested, needs 유리: add "BTC hold" as a permanent row in the forward tables and alerts (reporting only).
- Suggested, needs 유리: F18a has no role (dominated by F18b on every measure, in backtest and forward). Keep for the
  180-day record or stop logging it; either is fine, stopping saves nothing material.
- Reminder: the B59/B60 candidates from the hindsight study and the F2 short-only candidate from the retrospective are
  still waiting for her go/no-go. None of them touch these books.
