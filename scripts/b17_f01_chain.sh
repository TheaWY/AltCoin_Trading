#!/bin/bash
# F01/F01b/F01c chain: tabular fits -> sequence fits -> trade evaluation -> F01 horizon sweep. One job at a time. Log: logs/b17_f01_chain.log
cd "$(dirname "$0")/.." || exit 1
export OMP_NUM_THREADS=6
PY=.venv/bin/python
for m in lgbm logit catboost mlp; do
  [ -f data/cache/b17/p_$m.npy ] || $PY -W ignore scripts/b17_f01b.py fit $m
done
for m in gru tcn transformer; do
  [ -f data/cache/b17/p_$m.npy ] || $PY -W ignore scripts/b17_f01_seq.py $m
done
for m in lgbm logit catboost mlp gru tcn transformer; do
  [ -f data/cache/b17/p_$m.npy ] && [ ! -f data/reports/b17/f01b_$m.json ] && $PY -W ignore scripts/b17_f01b.py trade $m
done
for m in lgbm logit; do
  [ -f data/reports/b17/f01_$m.json ] || $PY -W ignore scripts/b17_f01.py $m > logs/b17_f01_$m.log 2>&1
done
echo CHAIN_DONE
