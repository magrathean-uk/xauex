#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run as root: sudo bash ops/install_systemd.sh" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
RUN_USER="${SUDO_USER:-${USER:-bolyki}}"

mkdir -p "$REPO_ROOT/logs" /var/lib/xauex /var/log/xauex
chown -R "$RUN_USER:$RUN_USER" "$REPO_ROOT/logs" /var/lib/xauex /var/log/xauex

install -m 755 "$REPO_ROOT/ops/run_backend.sh" /usr/local/bin/mirofish-run-backend
install -m 755 "$REPO_ROOT/ops/run_xauex.sh" /usr/local/bin/mirofish-run-xauex
install -m 755 "$REPO_ROOT/ops/run_bridge.sh" /usr/local/bin/mirofish-run-bridge
install -m 755 "$REPO_ROOT/ops/run_trade_journal.sh" /usr/local/bin/mirofish-run-trade-journal
install -m 755 "$REPO_ROOT/ops/run_weekly_review.sh" /usr/local/bin/mirofish-run-weekly-review
install -m 755 "$REPO_ROOT/ops/run_oracle_dashboard.sh" /usr/local/bin/mirofish-run-oracle-dashboard
install -m 755 "$REPO_ROOT/ops/enforce_log_budget.sh" /usr/local/bin/mirofish-enforce-log-budget

for unit in mirofish-backend.service xauex.service mirofish-bridge.service mirofish-bridge.timer xauex-start.timer xauex-stop.service xauex-stop.timer xauex-trade-journal.service xauex-trade-journal.timer xauex-weekly-review.service xauex-weekly-review.timer oracle-dashboard.service; do
  sed \
    -e "s|__REPO_ROOT__|$REPO_ROOT|g" \
    -e "s|__RUN_USER__|$RUN_USER|g" \
    "$REPO_ROOT/ops/$unit" > "/etc/systemd/system/$unit"
done

sed \
  -e "s|__REPO_ROOT__|$REPO_ROOT|g" \
  -e "s|__RUN_USER__|$RUN_USER|g" \
  "$REPO_ROOT/ops/logrotate-mirofish.conf" > /etc/logrotate.d/mirofish-gold-oracle

mkdir -p /etc/systemd/journald.conf.d
install -m 644 "$REPO_ROOT/ops/mirofish-journald.conf" /etc/systemd/journald.conf.d/mirofish-gold-oracle.conf

systemctl disable --now mirofish-run.timer >/dev/null 2>&1 || true
systemctl disable --now mirofish-run.service >/dev/null 2>&1 || true

systemctl daemon-reload
systemctl restart systemd-journald
systemctl disable xauex.service >/dev/null 2>&1 || true
systemctl enable mirofish-backend.service mirofish-bridge.timer xauex-start.timer xauex-stop.timer xauex-trade-journal.timer xauex-weekly-review.timer oracle-dashboard.service
systemctl restart mirofish-backend.service
systemctl restart oracle-dashboard.service
systemctl stop xauex.service >/dev/null 2>&1 || true
systemctl restart mirofish-bridge.timer
systemctl restart xauex-start.timer
systemctl restart xauex-stop.timer
systemctl restart xauex-trade-journal.timer
systemctl restart xauex-weekly-review.timer
logrotate -f /etc/logrotate.d/mirofish-gold-oracle >/dev/null 2>&1 || true
journalctl --vacuum-time=14d >/dev/null 2>&1 || true
journalctl --vacuum-size=256M >/dev/null 2>&1 || true
su -s /bin/bash "$RUN_USER" -c "$REPO_ROOT/ops/enforce_log_budget.sh 1887436800" >/dev/null 2>&1 || true

echo "Installed services from $REPO_ROOT"
echo "  backend: systemctl status mirofish-backend.service"
echo "  xauex:   systemctl status xauex.service"
echo "  bridge:  systemctl status mirofish-bridge.timer"
echo "  dashboard: systemctl status oracle-dashboard.service"
echo "  xauex timers: systemctl status xauex-start.timer xauex-stop.timer xauex-trade-journal.timer xauex-weekly-review.timer"
