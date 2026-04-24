#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$REPO_ROOT"
WINDOW_LABEL="${1:-${XAUEX_WINDOW_LABEL:-}}"
ARGS=(--asset XAUUSD --auto-context)
if [[ -n "$WINDOW_LABEL" ]]; then
  ARGS+=(--window-label "$WINDOW_LABEL")
fi
exec "$REPO_ROOT/.venv/bin/python" -m xauex.signal.run "${ARGS[@]}"
