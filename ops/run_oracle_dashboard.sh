#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

ORACLE_DASHBOARD_HOST="${ORACLE_DASHBOARD_HOST:-0.0.0.0}"
ORACLE_DASHBOARD_PORT="${ORACLE_DASHBOARD_PORT:-8089}"

export PYTHONUNBUFFERED=1
exec "$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/.venv/bin/waitress-serve" \
  --host="$ORACLE_DASHBOARD_HOST" \
  --port="$ORACLE_DASHBOARD_PORT" \
  dashboard_web.wsgi:app
