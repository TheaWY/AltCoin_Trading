#!/bin/zsh
# B17 F12c chain (clean random null for the F12b 60m/y1 lead). Launch detached:
#   launchctl submit -l com.altcoin.f12c -- /bin/zsh ~/Projects/AltCoin_Trading/scripts/b17_f12c_chain.sh
# Each step is skipped if its output exists, so a restart resumes. Removes its own launchd job at the end.
cd ~/Projects/AltCoin_Trading || exit 1
LOG=logs/b17_f12c.log
export F12C=1 F12_ONLY=60m:y1 OMP_NUM_THREADS=6
(
  echo "=== F12c chain $(date) ==="
  [ -f data/cache/b17/sample_rand.parquet ] || .venv/bin/python -W ignore scripts/b17_f12c.py sample || exit 1
  .venv/bin/python -W ignore scripts/backfill_b17.py sec1_rand || exit 1
  [ -f data/cache/b17/f12c_meta.parquet ] || .venv/bin/python -W ignore scripts/b17_f12.py features || exit 1
  for m in lgbm hawkes; do [ -f data/cache/b17/f12c_p_${m}_60m_y1.npy ] || .venv/bin/python -W ignore scripts/b17_f12.py tab $m || exit 1; done
  for m in tcn gru transformer; do [ -f data/cache/b17/f12c_p_${m}_60m_y1.npy ] || .venv/bin/python -W ignore scripts/b17_f12.py seq $m || exit 1; done
  .venv/bin/python -W ignore scripts/b17_f12.py report && echo F12C_DONE
) >> $LOG 2>&1   # subshell: a failed step exits only the subshell, so the job still removes itself (submit jobs restart otherwise)
launchctl remove com.altcoin.f12c
