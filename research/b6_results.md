# B6 loss-prediction ML: results (2026-09-27)

Design: research/ml_design.md (registered before running). Code: scripts/lossml_*.py.
Out-of-sample 2024-12..2026-09, 8 quarterly walk-forward folds, 1.39M (coin, time, side) rows.

## B6_1 discrimination: FAIL
| model | OOS AUC |
|---|---|
| LightGBM | 0.529 |
| CatBoost | 0.534 |
| MLP | 0.526 |
| GRU (48h sequence) | 0.537 |
| blend | 0.539, day-bootstrap CI [0.525, 0.550] |

Per-fold blend AUC ranges 0.513–0.554, so the result is stable, just small. Comparing only coins at the same timestamp gives 0.529, so a slice of the signal is market timing. On the triple-barrier label the AUC is 0.471.

## B6_2 calibration
The calibration holds: predicted 0.45 → realised loss 0.47, and predicted 0.60 → realised 0.59. The problem is that every decile has negative mean net (best decile −0.14% per 24h, CI [−0.47%, +0.18%]). The model separates bad trades from slightly less bad ones. It does not find a positive-EV set.

## B6_3 meta-label filter: FAIL (1 of 4, needed 3)
| primary | n | AUC | all | kept (P ≤ median) | improvement, CI |
|---|---|---|---|---|---|
| 30d breakout long | 10,725 | 0.50 | −0.34% | −0.50% | −0.16pp [−0.65, +0.29] |
| pump fade short | 5,770 | 0.52 | −0.34% | −0.70% | −0.35pp [−1.07, +0.40] |
| pump follow long | 5,770 | 0.51 | −0.11% | +0.21% | +0.31pp [−0.44, +1.08] |
| crash rebound long | 3,007 | 0.64 | +3.57% | +6.71% | +3.15pp [+0.76, +5.31] PASS |

For crash rebound the pass holds up. Without the 5 biggest crash days the kept trades still make +1.56% against +0.09% for the skipped ones. It holds in each year: in 2026 kept is +3.5% (n=260) and skipped is −0.7% (n=907). What the model keeps are crashes where BTC fell too and the coin sits at its 30-day low, i.e. market-wide liquidation. What it skips are coin-specific dumps with BTC flat, which keep falling. That is a post-hoc reading, so the next step is to register it as a simple rule and forward-test it. Do not trade it yet.

## B6_4 the real paper trades (indicative)
- 123 trades closed with a real exit; 111 were scorable (12 coins missing from the panel).
- AUC against realised loss:
  - all: 0.51 [0.40, 0.62]
  - directional only: 0.55 [0.42, 0.69]
  - signal_xs: 0.56 [0.37, 0.73]
- Skipping P(loss) > 0.6 would have removed only 4 trades, and those 4 netted +26, so skipping them hurts.
- Losses by strategy:
  - pairs_statarb: 74% losers, −65
  - momentum: 63%, −19
  - core_btc: −26
  - signal_xs: 38% losers, +11 (the only positive one)

The losses are explained by which strategy took the trade, not by anything the model can see at entry.

## SHAP (LightGBM, fold 6 OOS)
Top drivers of P(loss):
1. distance from the 30d low (in the trade's direction)
2. BTC 24h return in the trade's direction (with-trend trades lose more: BTC mean reversion)
3. side (longs worse)
4. coin age
5. weekday

Coin-specific microstructure features (taker flow, wicks, volume surge) rank low.
