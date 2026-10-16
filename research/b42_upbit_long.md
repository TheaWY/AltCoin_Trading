# B42 Upbit-native long-only pump strategy (F2U) (2026-10-04)

2066 Upbit KRW +10%/1h events with 1m paths (2024-01-04..2026-10-03); events per era: test 950, train 692, val 424.
Long only, Upbit fees 2x0.05% + slippage. Protocol pre-registered in the script docstring.

## No model: buy every pump (mean net by entry delay x exit)

**train** (n events 692)

|   delay | hold1h   | hold4h   | hold8h   | trail   | stop8_8h   |
|--------:|:---------|:---------|:---------|:--------|:-----------|
|       1 | -0.78%   | -1.17%   | -0.49%   | -0.40%  | -0.84%     |
|      15 | -0.34%   | -1.06%   | -0.10%   | -0.23%  | -0.37%     |
|      30 | -0.06%   | -0.58%   | +0.29%   | +0.41%  | -0.10%     |
|      60 | -0.52%   | -0.55%   | +0.22%   | +0.25%  | -0.08%     |
|     120 | -0.27%   | -0.04%   | +0.33%   | +0.34%  | +0.02%     |

**val** (n events 424)

|   delay | hold1h   | hold4h   | hold8h   | trail   | stop8_8h   |
|--------:|:---------|:---------|:---------|:--------|:-----------|
|       1 | -1.76%   | -3.53%   | -4.31%   | -4.19%  | -3.79%     |
|      15 | -1.39%   | -2.87%   | -3.35%   | -3.30%  | -3.06%     |
|      30 | -1.47%   | -2.75%   | -3.06%   | -3.49%  | -3.29%     |
|      60 | -1.25%   | -2.20%   | -2.58%   | -2.57%  | -2.99%     |
|     120 | -0.60%   | -1.16%   | -1.66%   | -1.58%  | -2.09%     |

**test** (n events 950)

|   delay | hold1h   | hold4h   | hold8h   | trail   | stop8_8h   |
|--------:|:---------|:---------|:---------|:--------|:-----------|
|       1 | -1.60%   | -3.85%   | -5.51%   | -4.44%  | -4.20%     |
|      15 | -1.34%   | -3.63%   | -5.13%   | -3.96%  | -4.25%     |
|      30 | -1.12%   | -3.33%   | -4.70%   | -3.73%  | -4.19%     |
|      60 | -0.93%   | -3.01%   | -4.40%   | -3.51%  | -3.73%     |
|     120 | -1.13%   | -2.77%   | -4.07%   | -3.28%  | -3.39%     |

## Validation grid (model filter), top 10

|   delay | exit     |   train_quantile |     thr |   val_n |   val_mean |
|--------:|:---------|-----------------:|--------:|--------:|-----------:|
|      60 | hold8h   |          +0.8000 | +0.0428 |      37 |    +0.0830 |
|     120 | hold8h   |          +0.7000 | +0.0218 |     103 |    +0.0261 |
|      30 | hold8h   |          +0.7000 | +0.0161 |      74 |    +0.0239 |
|      60 | trail    |          +0.8000 | +0.0420 |      45 |    +0.0152 |
|      60 | hold8h   |          +0.7000 | +0.0175 |     114 |    +0.0096 |
|      60 | stop8_8h |          +0.8000 | +0.0401 |      37 |    +0.0089 |
|      15 | hold8h   |          +0.7000 | +0.0142 |      81 |    +0.0086 |
|      15 | stop8_8h |          +0.7000 | +0.0064 |      84 |    +0.0081 |
|       1 | stop8_8h |          +0.7000 | -0.0007 |      50 |    +0.0070 |
|      15 | trail    |          +0.7000 | +0.0140 |      70 |    +0.0023 |

## TEST (2026, touched once) for the validation-chosen configuration

Chosen on validation: entry T+60m, exit hold8h, threshold = train-prediction quantile 0.8 (val n=37, val mean +8.30%).

| set | n | mean net | median | win | p10 | worst | 95% CI (day-clustered) |
|---|---|---|---|---|---|---|---|
| model picks | 81 | -2.62% | -4.18% | 32% | -13.1% | -29.7% | [-5.05%, +0.07%] |
| all pumps, same entry/exit | 1174 | -4.40% | -5.04% | 22% | -14.4% | -81.7% | [-5.44%, -3.43%] |

Verdict under the pre-registered rule: **FAIL**.
Runtime 17s.

## Reading (2026-10-04)

- Run 1 (22:28) showed +19%/trade and PASS. It was INVALID: feature pre_ret_1h read c[iT-121] = c[-1], which wraps to the
  last minute of the window (T+12h), i.e. the future price. Fixed (c[0] = T-2h); run 1 kept in b42_upbit_long_INVALID_run1.md.
- Corrected run: FAIL. The LightGBM filter cuts the loss of buying Upbit pumps (picks -2.6% vs all -4.4% at T+60m/8h) but
  stays negative (CI [-5.05%, +0.07%]). No long-only Upbit pump entry (delay 1-120 min, 5 exits) makes money in 2026.
- Mirror (per 유리's rule): buying every Upbit pump loses -3..-5.5% at 4-8h in val and test -> the opposite side would gain
  that, but it is a short (not possible on Upbit). Use as AVOID rule, consistent with B41/B43.
