#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# XAUEX Setup Script
# One-command setup for x86 VPS deployment
# ═══════════════════════════════════════════════════════════════
set -e

echo "XAUEX - Setup"
echo "================================"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Check Python ─────────────────────────────────────────────
PYTHON=""
for cmd in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        ver=$($cmd --version 2>&1 | grep -oP '\d+\.\d+')
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -gt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -ge 11 ]; }; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "Python 3.11+ required. Install with:"
    echo "  sudo apt install python3.11 python3.11-venv python3.11-dev"
    exit 1
fi
echo "✓ Found $PYTHON ($($PYTHON --version))"

# ── Create virtual environment ───────────────────────────────
echo ""
echo "Creating virtual environment..."
if [ ! -d ".venv" ]; then
    $PYTHON -m venv .venv
    echo "✓ Virtual environment created"
else
    echo "✓ Virtual environment exists"
fi

source .venv/bin/activate

# ── Install dependencies ─────────────────────────────────────
echo ""
echo "Installing Python dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q 2>&1 | tail -5
echo "✓ Dependencies installed"

# ── Create required directories ──────────────────────────────
echo ""
echo "Creating directories..."
sudo mkdir -p /var/lib/xauex
sudo chown $(whoami) /var/lib/xauex
mkdir -p logs
echo "✓ Directories ready"

# ── Setup .env file ──────────────────────────────────────────
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo ""
        echo "⚠ Created .env from template — you MUST edit it with your API keys:"
        echo "   nano .env"
    fi
else
    echo "✓ .env file exists"
fi

# ── Also create per-component .env symlinks when absent ─────
for dir in xauex; do
    if [ -d "$dir" ] && [ ! -f "$dir/.env" ]; then
        ln -sf "$SCRIPT_DIR/.env" "$dir/.env" 2>/dev/null || true
    fi
done

# ── Per-component venv symlinks (so each component finds deps) ──
for dir in xauex; do
    if [ -d "$dir" ] && [ ! -d "$dir/.venv" ]; then
        ln -sf "$SCRIPT_DIR/.venv" "$dir/.venv" 2>/dev/null || true
    fi
done

# ── Validate ─────────────────────────────────────────────────
echo ""
echo "Validating installation..."
$PYTHON -c "import rich; print('  ✓ rich')" 2>/dev/null || echo "  ✗ rich missing"
$PYTHON -c "import httpx; print('  ✓ httpx')" 2>/dev/null || echo "  ✗ httpx missing"
$PYTHON -c "import openai; print('  ✓ openai')" 2>/dev/null || echo "  ✗ openai missing"
$PYTHON -c "import schedule; print('  ✓ schedule')" 2>/dev/null || echo "  ✗ schedule missing"

echo ""
echo "═══════════════════════════════════════════════════"
echo "✅ Setup complete!"
echo ""
echo "To run:"
echo "  source .venv/bin/activate"
echo "  sudo bash ops/install_systemd.sh"
echo "  systemctl status xauex-web.service xauex-signal.timer xauex-start.timer"
echo ""
echo "Docs:"
echo "  README.md"
echo "  docs/REBUILD.md"
echo "  ops/RUNBOOK.md"
echo "═══════════════════════════════════════════════════"
