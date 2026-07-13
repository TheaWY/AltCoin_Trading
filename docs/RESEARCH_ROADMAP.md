# Research Roadmap

Dated notes for hypotheses that are registered but not yet testable — usually
because the data doesn't exist yet and has to accumulate first. Append,
don't rewrite; strike through when a note graduates to an actual event-study
run or gets resolved another way.

---

## 2026-07-13 — Orderbook signals (collection just started)

`src/data/collectors/orderbook.py` started writing derived order-book
features (`orderbook_snapshots` table) to the active∪open symbol scope
(~21 symbols) every trading cycle, starting today. Binance does not serve
historical order-book snapshots — this data cannot be backfilled, so
"starting today" means the earliest possible history is today's date, full
stop.

Three signal hypotheses pre-registered in `research_decisions`
(`action='hypothesis_registered'`) at collection start, before any data
existed to look at:

- `ob_imbalance_extreme` — `imbalance_ratio` above its rolling 95th percentile
- `ob_spread_blowout` — `spread_bps` above its rolling 95th percentile
- `ob_depth_collapse` — total depth below its rolling 5th percentile

**Not testable until ~90 days of history accumulate** (2026-10-11 or later —
percentile-based signals need a real distribution to rank against, and the
event-study MIN_EVENTS/CI machinery needs enough independent observations
to say anything). Do not event-study these early just because the table has
some rows; check back after the date above.

Collector scope is deliberately narrow for now (~21 symbols, matching
`src.symbols.cycle_symbols()`) — `fetch_order_book` is a heavier-weight
ccxt/Binance call than OHLCV, and widening to the full discovered universe
(~500 symbols) needs its own rate-limit measurement before it happens, not
an assumption that it's fine. Measured cost at 21 symbols: ~6s wall-clock
per cycle (see research_decisions for the exact run).
