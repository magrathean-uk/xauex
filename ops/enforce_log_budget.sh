#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

BUDGET_BYTES="${1:-1887436800}" # 1.75 GiB for app logs; journald is capped separately.
TODAY_UTC="$(date -u +%F)"

declare -a LOG_DIRS=(
  "/var/log/xauex"
  "$REPO_ROOT/logs"
)

declare -A PROTECTED=(
  ["/var/log/xauex/xauex.log"]=1
  ["/var/log/xauex/analyst.log"]=1
  ["$REPO_ROOT/logs/xauex-signal.log"]=1
  ["$REPO_ROOT/logs/xauex-signal-error.log"]=1
)

for dir in "${LOG_DIRS[@]}"; do
  [[ -d "$dir" ]] || continue
  while IFS= read -r -d '' file; do
    [[ -n "${PROTECTED[$file]:-}" ]] && continue
    rm -f -- "$file"
  done < <(find "$dir" -maxdepth 1 -type f -mtime +14 -print0)
done

total_size() {
  local total=0
  local size
  while IFS= read -r -d '' file; do
    size=$(stat -c %s "$file" 2>/dev/null || echo 0)
    total=$((total + size))
  done < <(find "${LOG_DIRS[@]}" -maxdepth 1 -type f -print0 2>/dev/null)
  echo "$total"
}

current_total="$(total_size)"
if (( current_total <= BUDGET_BYTES )); then
  exit 0
fi

while IFS=$'\t' read -r mtime file; do
  [[ -z "$file" ]] && continue
  [[ -n "${PROTECTED[$file]:-}" ]] && continue
  rm -f -- "$file"
  current_total="$(total_size)"
  if (( current_total <= BUDGET_BYTES )); then
    exit 0
  fi
done < <(
  find "${LOG_DIRS[@]}" -maxdepth 1 -type f ! -mtime +14 -printf '%T@\t%p\n' 2>/dev/null | sort -n
)
