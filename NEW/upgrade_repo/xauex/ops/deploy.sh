#!/usr/bin/env bash
# ops/deploy.sh — install/update XAUEX on this machine
#
# What it does:
#   1. Copies source to /opt/xauex
#   2. Creates a Python venv and installs all deps
#   3. Builds the Rust tick_parser extension
#   4. Installs the systemd service and reloads it
#   5. Does NOT start the bot — use: sudo systemctl start xauex
#
# Prerequisites:
#   - Run ops/setup_dirs.sh as root first
#   - .env file present and configured (run python auth.py first)
#   - Rust toolchain installed (rustup / apt)
#   - Python 3.11+ installed
#
# Idempotent: safe to run again to update an existing install.

set -euo pipefail

INSTALL_DIR="/opt/xauex"
VENV_DIR="$INSTALL_DIR/.venv"
SERVICE_NAME="xauex"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(dirname "$SCRIPT_DIR")"

# ─── Require root ────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
  echo "ERROR: deploy.sh must be run as root." >&2
  exit 1
fi

echo "=== XAUEX deploy.sh ==="
echo "Source      : $SRC_DIR"
echo "Install dir : $INSTALL_DIR"
echo ""

# ─── 1. Copy source ──────────────────────────────────────────────────────────
echo "[1/6] Syncing source files…"
rsync -a --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.venv' --exclude='tick_parser/target' --exclude='tick_parser/dist' \
  --exclude='data/dukascopy/*.csv' \
  "$SRC_DIR/" "$INSTALL_DIR/"

# ─── 2. Create venv ──────────────────────────────────────────────────────────
echo "[2/6] Setting up Python venv…"
if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt" --quiet
echo "       Python deps installed."

# ─── 3. Build Rust extension ─────────────────────────────────────────────────
echo "[3/6] Building Rust tick_parser…"
if command -v cargo &>/dev/null && command -v maturin &>/dev/null; then
  cd "$INSTALL_DIR/tick_parser"
  maturin build --release --out "$INSTALL_DIR/tick_parser/dist" \
    --interpreter "$VENV_DIR/bin/python3" 2>&1 | tail -3
  "$VENV_DIR/bin/pip" install --force-reinstall "$INSTALL_DIR/tick_parser/dist/"*.whl --quiet
  echo "       Rust tick_parser installed into venv."
elif command -v cargo &>/dev/null; then
  echo "       maturin not found — installing…"
  "$VENV_DIR/bin/pip" install maturin --quiet
  cd "$INSTALL_DIR/tick_parser"
  maturin build --release --out "$INSTALL_DIR/tick_parser/dist" \
    --interpreter "$VENV_DIR/bin/python3" 2>&1 | tail -3
  "$VENV_DIR/bin/pip" install --force-reinstall "$INSTALL_DIR/tick_parser/dist/"*.whl --quiet
  echo "       Rust tick_parser installed into venv."
else
  echo "       WARNING: Rust not found. tick_parser will use pure-Python fallback (slower)."
fi
cd "$SRC_DIR"

# ─── 4. Permissions ──────────────────────────────────────────────────────────
echo "[4/6] Setting permissions…"
chown -R xauex:xauex "$INSTALL_DIR"
chmod 600 "$INSTALL_DIR/.env" 2>/dev/null || true

# ─── 5. Install systemd service ──────────────────────────────────────────────
echo "[5/6] Installing systemd service…"
# Patch ExecStart in service file to use venv python
sed "s|ExecStart=.*|ExecStart=$VENV_DIR/bin/python main.py|" \
  "$INSTALL_DIR/ops/xauex.service" > /etc/systemd/system/xauex.service
systemctl daemon-reload
systemctl enable xauex.service
echo "       Service installed and enabled."

# ─── 6. Done ─────────────────────────────────────────────────────────────────
echo ""
echo "=== Deploy complete ==="
echo ""
echo "  Start the bot   : sudo systemctl start xauex"
echo "  View logs       : sudo journalctl -u xauex -f"
echo "  Bot log file    : sudo tail -f /var/log/xauex/xauex.log"
echo "  Dashboard       : python $INSTALL_DIR/dashboard.py"
echo ""
echo "  OBSERVE_ONLY=true by default — the bot will NOT place real orders."
echo "  Edit $INSTALL_DIR/.env and set OBSERVE_ONLY=false to go live."
