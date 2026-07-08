# Research Implementation Roadmap

This roadmap converts the bitcoin/crypto retail strategy research into concrete implementation work for this repository.

## 1. Current repository state

The current system is a Binance USDT perpetual altcoin scanner / paper trader. It already has:

- Short-first direction policy: `ALLOW_LONG=false`, `ALLOW_SHORT=true` by default.
- Rule-based evaluation over the Binance USDT perpetual universe.
- Existing signal families:
  - `funding_rate`: positive funding -> crowded-long reversal short.
  - `volume_spike`: abnormal volume + candle direction.
  - `mean_reversion`: RSI + Bollinger Band counter-trend scalp.
  - `momentum`: 7d trend continuation with SMA/MACD confirmation.
  - `breakout`: 20d Donchian / Turtle-style breakout.
  - `tsmom_28d`: 28d time-series momentum.
- Confluence scoring across aligned/conflicting setups.
- Paper trading with ATR stops, take-profit, trailing stop, time stop, cost model, and volatility-scaled sizing.

Important gap: the funding strategy currently implemented is **directional funding reversal**, not **delta-neutral funding carry**.

## 2. Research-aligned target stack

| Priority | Strategy / module | Research verdict | Repo status | Required implementation |
|---:|---|---|---|---|
| P0 | Capital + fee realism | Critical for 1,000,000 KRW / ~$730 | Partial | Set production defaults around small account size, min order, maker/taker fee, slippage, and minimum expected holding period. |
| P1 | Delta-neutral funding carry | Best risk-adjusted realistic strategy | Missing | Spot long + perp short ledger, projected net funding, fee breakeven, rebalance, liquidation guard. |
| P1 | Unified strategy registry | Needed for backtests | Partial | Promote dashboard-only setups into first-class strategy modules. |
| P2 | Neutral / short grid bot | Realistic but regime-sensitive | Missing | Range detector, ATR grid spacing, maker-only order simulation, trend invalidation. |
| P2 | Liquidation + long/short crowding | Retail-accessible edge | Partial | Long/short ratio exists as confluence only; add OI/funding/liquidation cascade signal. |
| P3 | Pair trading / stat arb | Possible but capital-tight | Missing | Rolling cointegration/z-score engine, pair position sizing, spread stop. |
| P3 | Listing pump/dump mean reversion | Experimental/high-risk | Missing | Listing event feed, first-day volatility filters, tight stop/TP rules. |
| P4 | Cross-exchange arb / HFT / market making | Not viable for ~$730 | Excluded | Keep explicitly out of scope. |

## 3. Implementation principles

1. **Backtest = paper = live should share the same signal path.** No dashboard-only strategies that cannot be backtested.
2. **No strategy ships without costs.** Every strategy must model fees, slippage, minimum notional, and expected holding period.
3. **No live trading until it survives paper.** Default mode stays paper/testnet.
4. **Small account constraints are first-class.** The system should know that a $20 theoretical edge is not worth complex execution risk.
5. **Reject strategies, do not merely add them.** Cross-exchange arb, HFT, and market making should remain excluded unless capital/infrastructure changes.

## 4. Step-by-step action plan

### Phase 0 — Align config with research account size

- Set `PAPER_STARTING_CAPITAL=730` or KRW-equivalent reporting.
- Add explicit exchange constraints:
  - spot fee per side
  - futures fee per side
  - slippage per side
  - minimum notional per order
  - max capital deployed per strategy
- Add a `research_mode` or `small_account_mode` config preset.
- Update dashboard to show net expected return in dollars/KRW, not only percentage.

### Phase 1 — Promote all existing setups into first-class strategies

Current registry only supports `funding_rate`, `momentum`, and `volume_spike`. The evaluation engine also contains `mean_reversion`, `breakout`, and `tsmom_28d`.

Tasks:

- Create `src/strategies/mean_reversion.py`.
- Create `src/strategies/donchian_breakout.py`.
- Create `src/strategies/tsmom_28d.py`.
- Register them in `src/strategies/registry.py`.
- Make `scripts/backtest.py` able to test each strategy independently.
- Add smoke tests for long-disabled policy and short-only behavior.

### Phase 2 — Implement delta-neutral funding carry

This is the biggest research gap.

New files / modules:

- `src/strategies/cash_carry.py`
- `src/engine/hedged_position.py`
- `src/engine/funding_ev.py`
- storage migration for hedged positions and funding cashflows

Core rules:

```text
Enter only if:
  funding_rate > minimum threshold
  projected funding over min_hold_days > round_trip_cost + safety_margin
  spot/perp basis acceptable
  liquidation buffer acceptable
  capital allocation <= cap

Position:
  buy spot BTC/ETH
  short equal notional perp
  track net delta, basis, funding received, fees, and margin buffer

Exit if:
  funding turns neutral/negative
  expected carry no longer clears cost
  liquidation buffer falls below threshold
  max hold reached
```

Backtest requirements:

- Use historical funding data.
- Track 8h funding cashflows.
- Include spot and futures fees separately.
- Calculate breakeven days.
- Report absolute return for $730 capital.

### Phase 3 — Add grid bot as paper-only

Grid should not go live before extended paper testing.

Tasks:

- `src/strategies/grid.py`
- ATR-based range detector.
- Grid spacing must be wider than round-trip costs.
- Max grid count must respect 5 USDT minimum notional.
- Add trend invalidation: stop grid when price exits range or ADX/momentum says trend.
- Dashboard should show grid range, number of live grid levels, expected fee drag.

### Phase 4 — Add liquidation / crowding reversal strategy

Current system has long/short ratio as a confluence bonus only. It should become a standalone strategy.

Tasks:

- Expand market data collection:
  - Binance long/short ratio
  - open interest history
  - taker buy/sell ratio
  - optional CoinGlass liquidation data if API key exists
- Create `src/strategies/crowding_reversal.py`.
- Signal examples:
  - long/short ratio extreme + funding positive + OI rising -> short setup
  - long liquidation cascade + OI flush -> avoid late short / possible cover
- Score should require multiple conditions, not ratio alone.

### Phase 5 — Pair trading / stat arb experiment

This is not a P1 strategy for $730, but it is useful research.

Tasks:

- Create pair universe: BTC-ETH, ETH-SOL, BTC-SOL.
- Rolling correlation + cointegration tests.
- Spread z-score entry/exit.
- Capital model must reserve both legs.
- Disable if spread half-life is too long or cointegration breaks.

### Phase 6 — Event/listing strategy as sandbox only

Tasks:

- Build listing event ingestion manually first.
- Detect post-listing dump/rebound patterns.
- Restrict to paper only.
- Tight max loss and max hold.

### Phase 7 — Live-readiness checklist

Before any real-money execution:

- All strategies pass walk-forward tests.
- Paper results include fees/slippage and show positive expectancy.
- Live order sizing respects exchange min notional.
- Kill switch exists.
- Max daily loss exists.
- Telegram/Slack/mobile alerting exists.
- Railway deployment has separate web and worker services.
- Database migrations are confirmed on production.

## 5. Recommended build order

```text
1. Make current strategies backtestable
2. Add small-account cost model
3. Add cash-carry funding module
4. Add strategy roadmap UI
5. Paper-test cash-carry + current short setups
6. Add grid bot
7. Add liquidation/crowding reversal
8. Only then consider pair/event experiments
```

## 6. What not to build now

Do not spend time on:

- cross-exchange arbitrage
- triangular arbitrage
- HFT
- market making rebate capture
- Kimchi-premium arbitrage

They are explicitly poor fits for the stated capital size and infrastructure.
