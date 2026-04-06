#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is not installed."
  exit 1
fi

exec python3 "$SCRIPT_DIR/remote_monitor.py" \
  --health-url "http://10.8.0.1:8051/health" \
  --watch \
  --notify-macos \
  "$@"
