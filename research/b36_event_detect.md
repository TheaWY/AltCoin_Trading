# B36 - early detection of +15%-in-4h continuations after a volume burst (hourly, 2024-05..2026-09)

Onset = hourly close >= +5% with volume >= 3x prior-24h mean, one per coin per 4h, in-universe. Label = a further >= +15% (high) within 4h. Train < 2025-07-01: 5366 onsets, 397 positives (7.4%). HOLDOUT >= 2025-07-01: 11020 onsets, 1625 positives (14.7%). Economics = long at the onset close, -3% stop on hourly lows, 4h exit, 15 bp cost.

## Economics (holdout)

| subset | n | P(+15%) | mean 4h net | 95% CI | hit |
|---|---|---|---|---|---|
| all onsets | 11020 | 14.7% | -0.31% | [-0.45, -0.18] | 0.24 |

## Single-feature screens (rank corr. with the label on TRAIN; quintile cut-offs from train, outcomes on HOLDOUT)

| feature | rho (train) | p | n train | P(+15%) top quintile | bottom quintile | net top | net bottom | n top / bottom |
|---|---|---|---|---|---|---|---|---|
| r1 | +0.148 | 0.000 | 5366 | 25.0% | 7.0% | -0.33% | -0.46% | 3454 / 1762 |
| r3 | +0.133 | 0.000 | 5366 | 25.5% | 8.5% | -0.20% | -0.21% | 3514 / 2035 |
| rv7d | +0.132 | 0.000 | 5366 | 22.7% | 6.7% | -0.04% | -0.42% | 4141 / 2076 |
| r24 | +0.109 | 0.000 | 5366 | 24.7% | 8.6% | -0.23% | +0.14% | 3787 / 2082 |
| corr_btc | -0.096 | 0.000 | 5366 | 2.4% | 17.8% | +1.78% | -0.40% | 656 / 4370 |
| r7d | +0.078 | 0.000 | 5366 | 24.0% | 13.1% | +0.02% | -0.21% | 2758 / 1966 |
| breadth | -0.075 | 0.000 | 5366 | 3.6% | 16.5% | +3.75% | -0.46% | 279 / 3653 |
| volx_prev | +0.074 | 0.000 | 5366 | 17.1% | 11.9% | -0.36% | -0.44% | 3230 / 1952 |
| vol_build | +0.071 | 0.000 | 5366 | 18.2% | 12.7% | -0.25% | -0.38% | 3421 / 2036 |
| volx | +0.067 | 0.000 | 5366 | 15.6% | 14.3% | -0.52% | -0.22% | 2823 / 2046 |
| mkt_r1 | -0.063 | 0.000 | 5366 | 3.8% | 15.9% | +1.94% | -0.41% | 585 / 3593 |
| mkt_r24 | -0.047 | 0.001 | 5366 | 12.7% | 15.2% | -0.10% | -0.39% | 495 / 3056 |
| kimchi | +0.045 | 0.143 | 1060 | 11.4% | 8.8% | -0.75% | -0.47% | 1059 / 353 |
| upbit_share_24h | -0.044 | 0.001 | 5366 | 14.7% | 16.4% | -0.31% | -0.24% | 11020 / 8620 |
| upbit_share_1h | -0.043 | 0.001 | 5366 | 14.7% | 16.4% | -0.31% | -0.24% | 11020 / 8621 |
| kimchi_chg | +0.041 | 0.180 | 1050 | 11.6% | 8.1% | -0.56% | -0.28% | 975 / 493 |
| taker_24h | +0.041 | 0.003 | 5366 | 16.1% | 12.9% | -0.39% | -0.19% | 2355 / 2934 |
| n_bursting | -0.040 | 0.003 | 5366 | 6.0% | 15.1% | +1.59% | -0.51% | 787 / 1032 |
| bithumb_share_1h | -0.039 | 0.005 | 5366 | 13.0% | 17.5% | -0.65% | -0.19% | 3157 / 6456 |
| dv24 | +0.024 | 0.079 | 5366 | 23.6% | 11.3% | -0.27% | -0.46% | 1066 / 4432 |
| notice | +0.023 | 0.099 | 5366 | 14.7% | 14.7% | -0.31% | -0.32% | 11020 / 10962 |
| beta | -0.019 | 0.173 | 5366 | 11.5% | 16.8% | +0.45% | -0.34% | 1691 / 5806 |
| trade_size | -0.016 | 0.245 | 5366 | 11.9% | 17.1% | +0.32% | -0.49% | 1620 / 3867 |
| f8 | -0.015 | 0.288 | 5366 | 15.0% | 15.7% | -0.20% | -0.64% | 6854 / 2966 |
| hour_kst | -0.014 | 0.313 | 5366 | 14.8% | 12.6% | -0.48% | +0.21% | 2072 / 2378 |
| dow | +0.013 | 0.340 | 5366 | 15.6% | 13.9% | -0.46% | -0.61% | 3088 / 3106 |
| r30d | -0.011 | 0.419 | 5365 | 21.0% | 14.2% | -0.14% | -0.10% | 2910 / 1826 |
| upbit_share_chg | -0.011 | 0.433 | 5366 | 15.2% | 15.8% | -0.28% | -0.27% | 10305 / 9335 |
| age_d | -0.010 | 0.484 | 5366 | 13.5% | 16.8% | -0.30% | -0.37% | 5184 / 1429 |
| dd30 | -0.004 | 0.782 | 5366 | 20.1% | 16.1% | -0.39% | -0.21% | 2931 / 2715 |
| taker_1h | -0.003 | 0.844 | 5366 | 10.5% | 14.7% | -0.23% | -0.36% | 1968 / 2993 |

## LightGBM (shallow, regularised) trained on TRAIN, scored on HOLDOUT

holdout AUC 0.677 (train 0.988), average precision 0.251 vs base rate 0.147

| subset by predicted P | n | P(+15%) | mean 4h net | 95% CI | hit |
|---|---|---|---|---|---|
| p >= train q50 (0.036) | 8936 | 17.1% | -0.37% | [-0.52, -0.18] | 0.21 |
| p >= train q80 (0.097) | 5287 | 21.1% | -0.32% | [-0.54, -0.08] | 0.19 |
| p >= train q90 (0.170) | 2739 | 25.7% | -0.30% | [-0.64, +0.07] | 0.17 |
| p >= train q95 (0.321) | 691 | 31.5% | -0.44% | [-1.25, +0.46] | 0.13 |
| p < train median | 2084 | 4.6% | -0.06% | [-0.30, +0.21] | 0.35 |

top features by gain: r1 9%, r3 9%, rv7d 8%, mkt_r24 7%, r30d 7%, vol_build 7%, taker_1h 6%, volx_prev 6%, age_d 6%, corr_btc 6%, dv24 6%, trade_size 6%, volx 6%, r7d 6%, breadth 6%

top-decile (train q90) rule by holdout month: n / mean net:
2025-07 83/-0.5%, 2025-08 69/-0.8%, 2025-09 92/-0.5%, 2025-10 229/-0.1%, 2025-11 252/-0.3%, 2025-12 192/-0.2%, 2026-01 113/-0.3%, 2026-02 148/-0.3%, 2026-03 191/+0.0%, 2026-04 346/-0.1%, 2026-05 166/-0.1%, 2026-06 221/-0.4%, 2026-07 171/-0.8%, 2026-08 286/-0.5%, 2026-09 180/-0.4%

## Depth-2 tree (train), leaves scored on holdout

```
|--- r1 <= 0.080
|   |--- breadth <= 0.973
|   |   |--- class: 0
|   |--- breadth >  0.973
|   |   |--- class: 0
|--- r1 >  0.080
|   |--- volx_prev <= 0.886
|   |   |--- class: 0
|   |--- volx_prev >  0.886
|   |   |--- class: 1

```

| leaf | n train | P train | n holdout | P holdout | net holdout |
|---|---|---|---|---|---|
| 2 | 2695 | 5.9% | 5804 | 9.1% | -0.48% |
| 3 | 882 | 1.1% | 228 | 3.1% | +3.87% |
| 5 | 530 | 7.0% | 1242 | 19.1% | -0.23% |
| 6 | 1259 | 15.3% | 3746 | 22.7% | -0.33% |

(65s)
## Exit sensitivity (holdout, same model, 4h hold from the onset close)

| subset | n | stop 3% | stop 6% | stop 10% | no stop |
|---|---|---|---|---|---|
| all onsets | 11020 | -0.31 [-0.4,-0.2] | -0.29 | -0.27 | -0.28 [-0.5,-0.1] |
| model p >= q80 | 5287 | -0.32 | -0.30 | -0.21 | -0.20 [-0.6,+0.2] |
| model p >= q90 | 2739 | -0.30 | -0.18 | +0.06 | +0.07 [-0.5,+0.7] |
| model p >= q95 | 691 | -0.44 | -0.63 | -0.05 | -0.19 [-1.9,+1.7] |
| short all onsets, no stop | 11020 | | | | -0.02 [-0.2,+0.2] |

Top decile of the model: P(further +15%) 26% (base 15%), mean 4h high +13.8%, mean 4h low -9.9%, 82% dip >= 3%, 37% dip >= 10%,
median 4h close -1.8%. The model finds the coins that will MOVE; it does not find which way they close. Long and short are both ~0.

## Conclusion
At hourly resolution (entry after the first +5% hour), big continuations are common (15%, raisable to ~30% by a model with
holdout AUC 0.68), but they come with equally large adverse excursions, so no exit rule turns the detection into money:
every subset is within noise of zero after 15 bp. Consistent with the whole programme: magnitude is predictable, direction
after the fact is not. Money can only be made by entering EARLIER (inside the first minutes, on the per-second tape) or by
an exogenous reason the tape cannot see (the exchange notice). That is what F11 (notice + confirmation), F12 (fresh burst
at the 5-minute scale) and the burst tape recorder (1-second tape of every +3%/5-min break, from 2026-10-03) are for.
