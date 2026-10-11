#!/bin/zsh
# B17 remaining-families queue. Runs one family at a time; a family whose report exists is skipped, so a restart resumes.
#   launchctl submit -l com.altcoin.b17queue -- /bin/zsh ~/Projects/AltCoin_Trading/scripts/b17_queue.sh
# Queue order (see research/b17_queue.md): F14 -> F09 -> F13 -> F20 [-> F11 -> F07 -> F16 -> F19_004 once their scripts exist]
cd ~/Projects/AltCoin_Trading || exit 1
export OMP_NUM_THREADS=6
(
  echo "=== B17 queue $(date) ==="
  for fam in f14 f09 f13 f20 f11 f07 f16 f19; do
    [ -f scripts/b17_${fam}.py ] || { echo "skip $fam (script not written yet)"; continue; }
    [ -f data/reports/b17/${fam}.json ] && { echo "skip $fam (report exists)"; continue; }
    echo "--- $fam start $(date)"
    .venv/bin/python -u -W ignore scripts/b17_${fam}.py > logs/b17_${fam}.log 2>&1 && echo "--- $fam done $(date)" || echo "--- $fam FAILED $(date) (see logs/b17_${fam}.log)"
  done
  echo "B17_QUEUE_DONE $(date)"
) >> logs/b17_queue.log 2>&1
launchctl remove com.altcoin.b17queue
