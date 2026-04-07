#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

export PYTHONUNBUFFERED=1
exec "$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/.venv/bin/waitress-serve" \
  --host=0.0.0.0 \
  --port=8089 \
  dashboard_web.wsgi:app

