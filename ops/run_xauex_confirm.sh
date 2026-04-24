#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$REPO_ROOT"
WINDOW_LABEL="${1:-${XAUEX_WINDOW_LABEL:-}}"
if [[ -z "$WINDOW_LABEL" ]]; then
  echo "XAUEX window label is required" >&2
  exit 1
fi
exec "$REPO_ROOT/.venv/bin/python" -m xauex.signal.confirm --window-label "$WINDOW_LABEL"
