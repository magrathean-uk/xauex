#!/usr/bin/env bash
set -euo pipefail

SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-/bin/systemctl}"
MONIT_PROBE_PYTHON_BIN="${MONIT_PROBE_PYTHON_BIN:-/usr/bin/python3}"
MONIT_PROBE_BIN="${MONIT_PROBE_BIN:-/usr/local/lib/monitoring/monit_probe.py}"
PROC_UPTIME_PATH="${PROC_UPTIME_PATH:-/proc/uptime}"
GRACE_SECONDS="${XAUEX_MONIT_TRANSITION_GRACE_SECONDS:-120}"

read_systemctl_property() {
  local property="$1"
  "$SYSTEMCTL_BIN" show xauex.service --property "$property" --value 2>/dev/null || true
}

active_state="$(read_systemctl_property ActiveState)"
sub_state="$(read_systemctl_property SubState)"

if [[ "$active_state" == "activating" || "$active_state" == "deactivating" ]]; then
  state_change_usec="$(read_systemctl_property StateChangeTimestampMonotonic)"
  if [[ "$state_change_usec" =~ ^[0-9]+$ ]] && [[ -r "$PROC_UPTIME_PATH" ]]; then
    now_usec="$(python3 - "$PROC_UPTIME_PATH" <<'PY'
from pathlib import Path
import sys

text = Path(sys.argv[1]).read_text(encoding="utf-8").strip().split()
seconds = float(text[0]) if text else 0.0
print(int(seconds * 1_000_000))
PY
)"
    age_seconds=$(( (now_usec - state_change_usec) / 1000000 ))
    if (( age_seconds >= 0 && age_seconds <= GRACE_SECONDS )); then
      echo "xauex-runtime status=ok mode=transition-grace service=${active_state}/${sub_state} state_change_age=${age_seconds}s"
      exit 0
    fi
  fi
fi

exec "$MONIT_PROBE_PYTHON_BIN" "$MONIT_PROBE_BIN" xauex-runtime
