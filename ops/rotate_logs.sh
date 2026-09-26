#!/bin/bash
# Copy-truncate rotation for data/logs, no sudo needed (launchd opens logs with
# O_APPEND, so truncating in place is safe for running services).
# Rotates any *.log over MAX_MB, keeps the newest KEEP archives per log.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOGS="$REPO/data/logs"; ARCH="$LOGS/archive"
MAX_MB="${LOG_ROTATE_MAX_MB:-200}"; KEEP="${LOG_ROTATE_KEEP:-5}"
mkdir -p "$ARCH"
stamp="$(date +%Y%m%d-%H%M%S)"
for f in "$LOGS"/*.log; do
  [ -f "$f" ] || continue
  size_mb=$(( $(stat -f%z "$f") / 1048576 ))
  [ "$size_mb" -lt "$MAX_MB" ] && continue
  base="$(basename "$f" .log)"
  if gzip -c "$f" > "$ARCH/$base.$stamp.log.gz"; then
    : > "$f"
    echo "rotated $base (${size_mb}MB)"
  fi
  ls -t "$ARCH/$base".*.log.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs rm -f
done
