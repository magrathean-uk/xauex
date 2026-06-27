#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run as root: sudo bash ops/install_systemd.sh" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
RUN_USER="${SUDO_USER:-${USER:-bolyki}}"
XAUEX_RUNTIME_CHANGED=0

install_if_changed() {
  local src="$1"
  local dst="$2"
  local mode="$3"
  local track_runtime="${4:-0}"

  if [[ ! -f "$dst" ]] || ! cmp -s "$src" "$dst"; then
    install -m "$mode" "$src" "$dst"
    if [[ "$track_runtime" -eq 1 ]]; then
      XAUEX_RUNTIME_CHANGED=1
    fi
  fi
}

render_unit_if_changed() {
  local unit="$1"
  local track_runtime="${2:-0}"
  local dst="/etc/systemd/system/$unit"
  local tmp
  tmp="$(mktemp)"
  sed \
    -e "s|__REPO_ROOT__|$REPO_ROOT|g" \
    -e "s|__RUN_USER__|$RUN_USER|g" \
    "$REPO_ROOT/ops/$unit" > "$tmp"
  if [[ ! -f "$dst" ]] || ! cmp -s "$tmp" "$dst"; then
    install -m 644 "$tmp" "$dst"
    if [[ "$track_runtime" -eq 1 ]]; then
      XAUEX_RUNTIME_CHANGED=1
    fi
  fi
  rm -f "$tmp"
}

window_timer_specs() {
  local phase="$1"
  PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 - "$phase" <<'PY'
from __future__ import annotations

import sys

from xauex.live_windows import all_live_windows


phase = sys.argv[1].strip().lower()
time_attr = {
    "signal": "signal_time_local",
    "confirm": "confirm_time_local",
}[phase]

for window in all_live_windows():
    hhmm = getattr(window, time_attr)
    print(f"{window.window_label}|Mon-Fri *-*-* {hhmm}:00 {window.timezone}")
PY
}

render_window_timer_if_changed() {
  local template_unit="$1"
  local phase="$2"
  while IFS='|' read -r window_label on_calendar; do
    [[ -n "$window_label" ]] || continue
    local dst="/etc/systemd/system/${template_unit/@./@${window_label}.}"
    local tmp
    tmp="$(mktemp)"
    sed \
      -e "s|__WINDOW_LABEL__|$window_label|g" \
      -e "s|__ON_CALENDAR__|$on_calendar|g" \
      "$REPO_ROOT/ops/$template_unit" > "$tmp"
    if [[ ! -f "$dst" ]] || ! cmp -s "$tmp" "$dst"; then
      install -m 644 "$tmp" "$dst"
    fi
    rm -f "$tmp"
  done < <(window_timer_specs "$phase")
}

mkdir -p "$REPO_ROOT/logs" /var/lib/xauex /var/lib/xauex/shadow_trials /var/lib/xauex/dsa-sidecar /var/log/xauex /etc/xauex
chown -R "$RUN_USER:$RUN_USER" "$REPO_ROOT/logs" /var/lib/xauex /var/log/xauex
mkdir -p /usr/local/lib/monitoring /etc/monit/conf-enabled

for stale_unit in oracle-dashboard.service mirofish-backend.service mirofish-bridge.service mirofish-bridge.timer; do
  rm -f "/etc/systemd/system/$stale_unit"
done
for retired_unit in xauex-shadow-report.timer xauex-shadow-report.service; do
  systemctl disable --now "$retired_unit" >/dev/null 2>&1 || true
  rm -f "/etc/systemd/system/$retired_unit"
done
rm -f /etc/systemd/system/xauex-window-signal@.timer /etc/systemd/system/xauex-window-confirm@.timer
rm -f /etc/logrotate.d/mirofish-gold-oracle
rm -f /etc/cron.d/xauex-daily-report
rm -f /usr/local/bin/xauex-run-daily-report /usr/local/bin/xauex-run-shadow-report

install_if_changed "$REPO_ROOT/ops/run_xauex.sh" /usr/local/bin/xauex-run-bot 755 1
install_if_changed "$REPO_ROOT/ops/run_xauex_signal.sh" /usr/local/bin/xauex-run-signal 755
install_if_changed "$REPO_ROOT/ops/run_xauex_confirm.sh" /usr/local/bin/xauex-run-confirm 755
install_if_changed "$REPO_ROOT/ops/run_xauex_shadow_compare.sh" /usr/local/bin/xauex-run-shadow-compare 755
install_if_changed "$REPO_ROOT/ops/run_xauex_shadow_evaluate.sh" /usr/local/bin/xauex-run-shadow-evaluate 755
install_if_changed "$REPO_ROOT/ops/run_trade_journal.sh" /usr/local/bin/xauex-run-trade-journal 755
install_if_changed "$REPO_ROOT/ops/run_weekly_review.sh" /usr/local/bin/xauex-run-weekly-review 755
install_if_changed "$REPO_ROOT/ops/run_decision_ledger.sh" /usr/local/bin/xauex-run-decision-ledger 755
install_if_changed "$REPO_ROOT/ops/run_xauex_web.sh" /usr/local/bin/xauex-run-web 755
install_if_changed "$REPO_ROOT/ops/run_dsa_sidecar.sh" /usr/local/bin/xauex-run-dsa-sidecar 755
install_if_changed "$REPO_ROOT/ops/enforce_log_budget.sh" /usr/local/bin/xauex-enforce-log-budget 755
install_if_changed "$REPO_ROOT/ops/check_host_layout.sh" /usr/local/bin/xauex-check-host-layout 755
install_if_changed "$REPO_ROOT/ops/monitoring/check_xauex_runtime.sh" /usr/local/lib/monitoring/check_xauex_runtime.sh 755
install_if_changed "$REPO_ROOT/ops/monitoring/check_xauex_morning_summary.py" /usr/local/lib/monitoring/check_xauex_morning_summary.py 755
install_if_changed "$REPO_ROOT/ops/monitoring/check_xauex_trade_alerts.py" /usr/local/lib/monitoring/check_xauex_trade_alerts.py 755
install_if_changed "$REPO_ROOT/ops/monitoring/check_xauex_signal_stall.py" /usr/local/lib/monitoring/check_xauex_signal_stall.py 755
install_if_changed "$REPO_ROOT/ops/monitoring/45-xauex-notify.monit" /etc/monit/conf-enabled/45-xauex-notify.monit 644

for unit in xauex.service xauex-signal.service xauex-signal.timer xauex-window-signal@.service xauex-window-confirm@.service xauex-shadow-compare.service xauex-shadow-compare.timer xauex-shadow-evaluate.service xauex-shadow-evaluate.timer xauex-start.timer xauex-stop.service xauex-stop.timer xauex-trade-journal.service xauex-trade-journal.timer xauex-decision-ledger.service xauex-decision-ledger.timer xauex-weekly-review.service xauex-weekly-review.timer xauex-web.service dsa-sidecar.service; do
  if [[ "$unit" == "xauex.service" ]]; then
    render_unit_if_changed "$unit" 1
  else
    render_unit_if_changed "$unit"
  fi
done
render_window_timer_if_changed "xauex-window-signal@.timer" "signal"
render_window_timer_if_changed "xauex-window-confirm@.timer" "confirm"

sed \
  -e "s|__REPO_ROOT__|$REPO_ROOT|g" \
  -e "s|__RUN_USER__|$RUN_USER|g" \
  "$REPO_ROOT/ops/logrotate-xauex.conf" > /etc/logrotate.d/xauex-gold-oracle

mkdir -p /etc/systemd/journald.conf.d
install -m 644 "$REPO_ROOT/ops/xauex-journald.conf" /etc/systemd/journald.conf.d/xauex-gold-oracle.conf

if command -v caddy >/dev/null 2>&1; then
  # Install the repo-owned VPN-only dashboard snippet into the local Caddy config.
  # The public relay is managed separately and is not configured by this script.
  mkdir -p /etc/caddy/Caddyfile.d
  install -m 644 "$REPO_ROOT/ops/xauex-dashboard.caddy" /etc/caddy/Caddyfile.d/xauex-dashboard.caddy
  if [[ -f /etc/caddy/Caddyfile ]]; then
    tmp="$(mktemp)"
    awk '
      $0 == "import /etc/caddy/Caddyfile.d/*.caddy" {
        if (seen++) {
          next
        }
      }
      { print }
    ' /etc/caddy/Caddyfile > "$tmp"
    install -m 644 "$tmp" /etc/caddy/Caddyfile
    rm -f "$tmp"
  fi
  if ! grep -Fxq 'import /etc/caddy/Caddyfile.d/*.caddy' /etc/caddy/Caddyfile 2>/dev/null; then
    printf '\nimport /etc/caddy/Caddyfile.d/*.caddy\n' >> /etc/caddy/Caddyfile
  fi
fi

systemctl daemon-reload
systemctl reset-failed oracle-dashboard.service mirofish-backend.service mirofish-bridge.service mirofish-bridge.timer >/dev/null 2>&1 || true
systemctl restart systemd-journald
systemctl disable xauex.service >/dev/null 2>&1 || true
systemctl disable xauex-signal.timer >/dev/null 2>&1 || true
systemctl disable oracle-dashboard.service mirofish-backend.service mirofish-bridge.service mirofish-bridge.timer >/dev/null 2>&1 || true
systemctl enable xauex-window-signal@morning.timer xauex-window-signal@midday.timer xauex-window-signal@us_open.timer xauex-window-confirm@morning.timer xauex-window-confirm@midday.timer xauex-window-confirm@us_open.timer xauex-shadow-compare.timer xauex-shadow-evaluate.timer xauex-start.timer xauex-stop.timer xauex-trade-journal.timer xauex-decision-ledger.timer xauex-weekly-review.timer xauex-web.service
systemctl restart xauex-web.service
for _ in {1..30}; do
  if ss -ltn | grep -q '127.0.0.1:8089'; then
    break
  fi
  sleep 1
done
systemctl restart xauex-window-signal@morning.timer
systemctl restart xauex-window-signal@midday.timer
systemctl restart xauex-window-signal@us_open.timer
systemctl restart xauex-window-confirm@morning.timer
systemctl restart xauex-window-confirm@midday.timer
systemctl restart xauex-window-confirm@us_open.timer
systemctl restart xauex-shadow-compare.timer
systemctl restart xauex-shadow-evaluate.timer
systemctl restart xauex-start.timer
systemctl restart xauex-stop.timer
systemctl restart xauex-trade-journal.timer
systemctl restart xauex-decision-ledger.timer
systemctl restart xauex-weekly-review.timer
if command -v caddy >/dev/null 2>&1; then
  systemctl reload caddy >/dev/null 2>&1 || systemctl restart caddy >/dev/null 2>&1 || true
fi
if command -v monit >/dev/null 2>&1; then
  monit reload >/dev/null 2>&1 || systemctl reload monit >/dev/null 2>&1 || systemctl restart monit >/dev/null 2>&1 || true
fi
/usr/local/bin/xauex-check-host-layout --strict

LONDON_DOW="$(TZ=Europe/London date +%u)"
LONDON_HHMM="$(TZ=Europe/London date +%H:%M)"
SHOULD_RUN_XAUEX=0
if [[ "$LONDON_DOW" -ge 1 && "$LONDON_DOW" -le 4 && "$LONDON_HHMM" > "07:24" ]]; then
  SHOULD_RUN_XAUEX=1
elif [[ "$LONDON_DOW" -eq 5 && "$LONDON_HHMM" > "07:24" && "$LONDON_HHMM" < "15:06" ]]; then
  SHOULD_RUN_XAUEX=1
fi

if systemctl is-active --quiet xauex.service; then
  if [[ "$XAUEX_RUNTIME_CHANGED" -eq 1 ]]; then
    systemctl restart xauex.service
  fi
elif [[ "$SHOULD_RUN_XAUEX" -eq 1 ]]; then
  systemctl start xauex.service
fi

logrotate -f /etc/logrotate.d/xauex-gold-oracle >/dev/null 2>&1 || true
journalctl --vacuum-time=14d >/dev/null 2>&1 || true
journalctl --vacuum-size=256M >/dev/null 2>&1 || true
su -s /bin/bash "$RUN_USER" -c "$REPO_ROOT/ops/enforce_log_budget.sh 1887436800" >/dev/null 2>&1 || true

echo "Installed services from $REPO_ROOT"
echo "  xauex:   systemctl status xauex.service"
echo "  signal:  systemctl status xauex-window-signal@morning.timer xauex-window-signal@midday.timer xauex-window-signal@us_open.timer"
echo "  confirm: systemctl status xauex-window-confirm@morning.timer xauex-window-confirm@midday.timer xauex-window-confirm@us_open.timer"
echo "  shadow:  systemctl status xauex-shadow-compare.timer xauex-shadow-evaluate.timer"
echo "  web:     systemctl status xauex-web.service"
echo "  xauex timers: systemctl status xauex-start.timer xauex-stop.timer xauex-trade-journal.timer xauex-weekly-review.timer"
echo "  dsa:     optional sidecar installed disabled; start with systemctl start dsa-sidecar.service after configuring /etc/xauex/dsa-sidecar.env"
