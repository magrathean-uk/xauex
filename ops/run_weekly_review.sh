#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

export XAUEX_WEEKLY_REVIEW_MODE=current_week

cd "$REPO_ROOT"
exec "$REPO_ROOT/.venv/bin/python" -m xauex.analyst.weekly_review
