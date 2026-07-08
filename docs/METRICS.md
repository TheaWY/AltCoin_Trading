# Evaluation Metrics — what the dashboard shows and why

The home tab classifies every tracked coin as **단타** (1–3 day scalp),
**스윙** (1–2 week swing), or **대기** (no entry, with reasons). The verdict is
computed in `src/engine/evaluation.py` from the quant metrics in
`src/engine/indicators.py`. Every metric below is a data point stored/derived
from our own OHLCV + funding history, so verdicts are fully explainable.

## Metrics and their research basis

| Metric | What it measures | Why it's included |
| --- | --- | --- |
| 24h / 7d / 30d return | Time-series momentum | The strongest documented crypto return predictor. Liu & Tsyvinski, *Risks and Returns of Cryptocurrency* (Review of Financial Studies, 2021) find significant time-series momentum at daily and weekly horizons; Moskowitz, Ooi & Pedersen, *Time Series Momentum* (JFE, 2012) establish it across asset classes. Drives the 스윙 setup. |
| BTC correlation & beta (30d, hourly) | Co-movement with the crypto market factor | Liu, Tsyvinski & Wu, *Common Risk Factors in Cryptocurrency* (Journal of Finance, 2022) show a coin-market factor prices the cross-section. An alt with correlation ≳ 0.9 has no independent trade — you're just trading BTC with extra fees. Used as a why-not reason. |
| Realized volatility (7d/30d, annualized) | Risk level | Moreira & Muir, *Volatility-Managed Portfolios* (Journal of Finance, 2017): scaling exposure by volatility improves risk-adjusted returns. Context for position sizing. |
| ATR% (14, 1h) | Typical hourly range | Practical scalping gate: if ATR < ~0.25%, the expected move can't cover fees + slippage, so 단타 is disqualified. |
| Sharpe ratio (7d/30d) | Risk-adjusted trend quality | Distinguishes a clean trend from a choppy one; a 7d trend with high Sharpe is a better swing candidate. |
| Max drawdown (30d) | Worst recent peak-to-trough | Risk context for how badly a swing entry could be underwater. |
| Funding rate | Perp-market crowding | Positive extremes = over-leveraged longs (mean-reversion short); negative = shorts paying longs (long). Funding-rate carry/reversal is a standard crypto stat-arb input. Drives the 단타 funding setup. |
| Dollar volume (24h) & Amihud illiquidity | Liquidity / price impact | Amihud, *Illiquidity and Stock Returns* (J. Financial Markets, 2002); volume-based liquidity screens are also significant in Liu-Tsyvinski-Wu's cross-section. Illiquid pairs are disqualified (slippage risk). |
| Volume ratio (vs 24h avg) | Abnormal participation | Volume spikes with a directional move signal real flow; used for the 단타 volume setup and as confirmation elsewhere. |
| Distance from 30d high/low | Anchoring | George & Hwang, *The 52-Week High and Momentum Investing* (Journal of Finance, 2004): proximity to highs predicts continuation. Context metric. |
| RSI(14) | Overbought/oversold state | Standard feature in crypto ML forecasting; confirms funding-reversal setups (e.g. hot funding + RSI > 65) and warns against chasing (RSI > 75). |
| MACD (12/26/9) | Trend direction/inflection | Confirms that a 7d momentum reading still has an active trend behind it. |
| Bollinger %B / bandwidth (20, 2σ) | Range position / squeeze | Squeeze (narrow bandwidth) often precedes expansion; %B shows where price sits in its recent envelope. |
| Price vs SMA20/SMA50 | Trend structure | 정배열 (price > SMA20 > SMA50) required for swing longs — filters counter-trend entries. |

## How the verdict works

1. **Hard gates** (disqualify regardless of setups):
   - fewer than 48 hourly candles → metrics unreliable ("데이터 수집 중"),
   - 24h dollar volume below $5M → illiquid,
   - ATR% below 0.25% → not enough movement to trade.
2. **단타 setups** (1–3 days):
   - *funding_rate*: funding beyond ±threshold → fade the crowd (RSI agreement adds score),
   - *volume_spike*: volume ≥ 2× average with a ≥1% 24h move → follow the flow.
3. **스윙 setup** (1–2 weeks):
   - *momentum*: |7d| ≥ 5% **and** trend structure (price/SMA20/SMA50 alignment) **and** MACD agreement; 7d Sharpe adds score, extreme RSI subtracts.
4. Best-scoring setup wins and is displayed with its strategy and a short
   Korean reason. Long-term buy-and-hold is intentionally excluded — this
   page only answers "단타 or 스윙, now or not."
5. If nothing passes, the card shows the specific reasons (neutral funding,
   no volume signal, no 7d trend, too-high BTC correlation, neutral RSI...).

All thresholds live in `src/config.py` (`EVAL_*`, `FUNDING_RATE_*`,
`MOMENTUM_7D_STRONG_PCT`, `VOLUME_SPIKE_RATIO`) and can be tuned via env vars.

## Data requirements

Metrics use up to 720 hourly candles (30 days). `OHLCV_LIMIT` now defaults to
720 so a fresh deployment backfills 30 days on its first collection cycle;
after that, collection stays incremental. Run `python scripts/backfill.py`
to extend history further (up to 1000 × 1h).

## Not yet included (future data sources)

- **Investor attention** (Google Trends / social volume) — the second strong
  predictor in Liu-Tsyvinski; needs an external API.
- **Open interest** — complements funding for crowding; available from the
  Binance futures API.
- **On-chain network activity** (active addresses, transfers) — the network
  factor of Liu-Tsyvinski (RFS 2021); needs a data vendor.
