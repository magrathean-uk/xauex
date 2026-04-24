#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${XAUEX_REPO_ROOT:-$(dirname "$SCRIPT_DIR")}"

cd "$REPO_ROOT"
exec "$REPO_ROOT/.venv/bin/python" -m xauex.signal.shadow_trial report
