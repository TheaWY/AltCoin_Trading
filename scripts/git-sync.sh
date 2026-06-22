#!/bin/bash
# Auto-commit and push local changes to GitHub (respects .gitignore).
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"
LOG_FILE="$PROJECT_DIR/logs/git-sync.log"

mkdir -p "$(dirname "$LOG_FILE")"
exec >>"$LOG_FILE" 2>&1

echo "=== git-sync $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not a git repository — skip"
  exit 0
fi

if ! git remote get-url origin >/dev/null 2>&1; then
  echo "No origin remote — skip"
  exit 0
fi

if [[ -f .git/index.lock ]] || [[ -f .git/shallow.lock ]]; then
  echo "Git lock present — skip"
  exit 0
fi

BRANCH="$(git branch --show-current)"
if [[ -z "$BRANCH" ]]; then
  echo "Detached HEAD — skip"
  exit 0
fi

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# Stage tracked + new files; .gitignore excludes .env, logs, .venv, etc.
git add -A

if git diff --cached --quiet; then
  echo "No changes to sync"
  exit 0
fi

MSG="Auto-sync $(date -u +%Y-%m-%dT%H:%M:%SZ)"
git commit -m "$MSG"
git push origin "$BRANCH"
echo "Pushed to origin/$BRANCH"
