#!/usr/bin/env bash
# ops/setup_dirs.sh — create runtime directories and system user for XAUEX
# Run as root before deploy.sh
# Idempotent: safe to run multiple times.

set -euo pipefail

RUNTIME_USER="xauex"
LIB_DIR="/var/lib/xauex"
LOG_DIR="/var/log/xauex"

# ─── Require root ────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
  echo "ERROR: This script must be run as root." >&2
  exit 1
fi

echo "=== XAUEX setup_dirs.sh ==="

# ─── Create system user (no login shell, no home) ────────────────────────────
if id "$RUNTIME_USER" &>/dev/null; then
  echo "[OK] User '$RUNTIME_USER' already exists"
else
  useradd --system --no-create-home --shell /sbin/nologin "$RUNTIME_USER"
  echo "[+]  Created system user '$RUNTIME_USER'"
fi

# ─── Create directories ──────────────────────────────────────────────────────
for dir in "$LIB_DIR" "$LOG_DIR"; do
  if [[ -d "$dir" ]]; then
    echo "[OK] $dir already exists"
  else
    mkdir -p "$dir"
    echo "[+]  Created $dir"
  fi
  chown "$RUNTIME_USER:$RUNTIME_USER" "$dir"
  chmod 750 "$dir"
done

# ─── Create install directory ─────────────────────────────────────────────────
INSTALL_DIR="/opt/xauex"
if [[ ! -d "$INSTALL_DIR" ]]; then
  mkdir -p "$INSTALL_DIR"
  echo "[+]  Created $INSTALL_DIR"
fi

echo ""
echo "=== Done. Run ops/deploy.sh to install the bot. ==="
