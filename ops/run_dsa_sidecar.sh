#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${DSA_SIDECAR_REPO_URL:-https://github.com/ZhuLinsen/daily_stock_analysis.git}"
DSA_COMMIT="${DSA_SIDECAR_COMMIT:-7ff3297050cfebd6f741649d799cb50cad857451}"
BASE_DIR="${DSA_SIDECAR_BASE_DIR:-/var/lib/xauex/dsa-sidecar}"
REPO_DIR="${DSA_SIDECAR_REPO_DIR:-$BASE_DIR/daily_stock_analysis}"
VENV_DIR="${DSA_SIDECAR_VENV_DIR:-$BASE_DIR/.venv}"
ENV_FILE="${DSA_SIDECAR_ENV_FILE:-/etc/xauex/dsa-sidecar.env}"
HOST="${DSA_SIDECAR_HOST:-127.0.0.1}"
PORT="${DSA_SIDECAR_PORT:-8090}"
INSTALL_DEPS="${DSA_SIDECAR_INSTALL_DEPS:-auto}"

mkdir -p "$BASE_DIR"

if [[ ! -d "$REPO_DIR/.git" ]]; then
  rm -rf "$REPO_DIR"
  git clone "$REPO_URL" "$REPO_DIR"
fi

git -C "$REPO_DIR" fetch --depth=1 origin "$DSA_COMMIT"
git -C "$REPO_DIR" checkout --detach "$DSA_COMMIT"

if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
  INSTALL_DEPS=1
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip
if [[ "$INSTALL_DEPS" == "1" || "$INSTALL_DEPS" == "true" || "$INSTALL_DEPS" == "yes" || "$INSTALL_DEPS" == "auto" ]]; then
  python -m pip install -r "$REPO_DIR/requirements.txt"
fi

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

export WEBUI_HOST="$HOST"
export API_PORT="$PORT"
cd "$REPO_DIR"
exec python main.py --serve-only --host "$HOST" --port "$PORT"
