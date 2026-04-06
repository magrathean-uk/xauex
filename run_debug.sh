set -euo pipefail
ROOT="/home/bolyki/mirofish-gold-oracle"
LOG="$ROOT/logs/run_debug_$(date +%Y%m%d_%H%M%S).log"
VENV="$ROOT/.venv/bin/python"

echo "=== MiroFish DEBUG RUN === $(date)" | tee "$LOG"
echo "Log: $LOG"
echo "Python: $($VENV --version)" | tee -a "$LOG"
echo "numpy: $($VENV -c 'import numpy; print(numpy.__version__)' 2>&1)" | tee -a "$LOG"
echo "oasis: $($VENV -c 'import oasis; print("OK")' 2>&1)" | tee -a "$LOG"
echo "" | tee -a "$LOG"

export PYTHONPATH="$ROOT:$ROOT/bridge:$ROOT/backend"
export LOG_LEVEL=DEBUG
export BRIDGE_DEBUG=1

cd "$ROOT"
exec $VENV main.py --once 2>&1 | tee -a "$LOG"
