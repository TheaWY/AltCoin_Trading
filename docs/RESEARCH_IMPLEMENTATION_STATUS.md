# Paper-Only Research Strategy Implementation Status

This branch implements the research stack as paper/backtest modules only. Live trading remains disabled unless `LIVE_TRADING=true` is explicitly configured, and the recommended research preset keeps it false.

## Implemented

| Research item | Implementation |
|---|---|
| Small-account fee realism | `src/engine/small_account.py` cost/EV guard; `.env.example` documents `PAPER_STARTING_CAPITAL=730`, min notional, and expected net USD hurdle. |
| Delta-neutral funding carry | `src/strategies/funding_carry.py` from PR #6, with paper/backtest metadata `execution_mode=delta_neutral` and funding-cashflow PnL approximation. |
| Existing strategy registry cleanup | `mean_reversion` from PR #6 plus `donchian_breakout` and `tsmom_28d` registered as first-class strategies. |
| Neutral/short grid | `src/strategies/grid.py`, paper-only, range/trend/cost gated. Emits short only when price is in upper range zone. |
| Liquidation/crowding proxy | `positioning_short` from PR #6, using long/short ratio + funding + open interest. Full liquidation API is left optional because CoinGlass requires paid API access. |
| Pair trading sandbox | `src/strategies/pair_trading.py`, paper-only z-score spread logic using symbol vs BTC/ETH benchmark when available. |
| Listing pump/dump sandbox | `src/strategies/listing_reversion.py`, paper-only and event-gated. Emits NONE unless listing metadata exists. |
| Bad-fit strategies excluded | HFT, cross-exchange arb, triangular arb, market making, and kimchi-premium arb remain intentionally out of scope. |

## Strategy names now registered

- `funding_carry`
- `positioning_short`
- `grid`
- `mean_reversion`
- `donchian_breakout`
- `tsmom_28d`
- `pair_trading`
- `listing_reversion`
- plus the original `funding_rate`, `momentum`, `volume_spike`

## Recommended paper-only env

```bash
LIVE_TRADING=false
PAPER_STARTING_CAPITAL=730
ALLOW_LONG=false
ALLOW_SHORT=true
ACTIVE_STRATEGIES=funding_carry,positioning_short,grid,mean_reversion,donchian_breakout,tsmom_28d,pair_trading,listing_reversion,funding_rate,momentum,volume_spike
```

## Known limitations

1. `funding_carry` models the delta-neutral carry PnL from funding cashflows but spot/perp basis drift is still zero until basis data is stored.
2. `grid` is a paper signal proxy, not an order-book simulator with individual maker limit orders.
3. `pair_trading` is symbol-centric because the current signal engine is symbol-centric; it uses a benchmark leg when data exists.
4. `listing_reversion` requires future listing-event ingestion before it can fire actionable signals.
5. CoinGlass liquidation data is not implemented as a paid API dependency; `positioning_short` is the free Binance proxy.

## Validation target

Before this should be considered for tiny live experiments, run:

```bash
python scripts/test_new_strategies.py
python scripts/test_research_stack.py
PRIMARY_STRATEGY=funding_carry python scripts/backtest.py --symbols BTC/USDT,ETH/USDT
PRIMARY_STRATEGY=positioning_short python scripts/backtest.py --symbols BTC/USDT,ETH/USDT
PRIMARY_STRATEGY=grid python scripts/backtest.py --symbols BTC/USDT,ETH/USDT
```
