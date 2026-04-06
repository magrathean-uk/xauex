#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$REPO_ROOT/backend"
exec "$REPO_ROOT/.venv/bin/waitress-serve" \
  --host="${FLASK_HOST:-127.0.0.1}" \
  --port="${FLASK_PORT:-5001}" \
  wsgi:app
