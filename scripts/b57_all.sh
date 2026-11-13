#!/bin/zsh
# B57 orchestration (prereg v8): every model x horizon x target in its own process (LightGBM/PyTorch OpenMP clash).
# Skips jobs whose dev prediction file already exists. Log: logs/b57_all.out
cd /Users/pc/Projects/AltCoin_Trading
TAB=(ridge enet lgbm xgb cat mlp)
SEQ=(lstm gru transformer)
jobs=()
for tgt in tim sel; do
  for h in 28 7; do
    for m in $TAB $SEQ; do jobs+=("$m $h $tgt"); done
  done
  for m in $TAB; do jobs+=("$m 1 $tgt"); done
done
for j in $jobs; do
  set -- ${=j}
  f=data/upbit_db/b57/preds/${1}_h${2}_${3}.parquet
  if [[ -f $f ]]; then echo "skip $j"; continue; fi
  df=$(df -m / | tail -1 | awk '{print $4}')
  if (( df < 4000 )); then echo "STOP: disk ${df} MB"; exit 1; fi
  echo "$(date +%T) start $j"
  .venv/bin/python scripts/b57_run.py $1 $2 $3 || echo "FAILED $j"
done
echo "$(date +%T) ALL DONE"
