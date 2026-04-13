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

for stale_unit in oracle-dashboard.service mirofish-backend.service mirofish-bridge.service mirofish-bridge.timer; do
  rm -f "/etc/systemd/system/$stale_unit"
done
rm -f /etc/logrotate.d/mirofish-gold-oracle

install -m 755 "$REPO_ROOT/ops/run_xauex.sh" /usr/local/bin/xauex-run-bot
install -m 755 "$REPO_ROOT/ops/run_xauex_signal.sh" /usr/local/bin/xauex-run-signal
install -m 755 "$REPO_ROOT/ops/run_trade_journal.sh" /usr/local/bin/xauex-run-trade-journal
install -m 755 "$REPO_ROOT/ops/run_weekly_review.sh" /usr/local/bin/xauex-run-weekly-review
install -m 755 "$REPO_ROOT/ops/run_xauex_web.sh" /usr/local/bin/xauex-run-web
install -m 755 "$REPO_ROOT/ops/run_xauex_daily_report.py" /usr/local/bin/xauex-run-daily-report
install -m 755 "$REPO_ROOT/ops/enforce_log_budget.sh" /usr/local/bin/xauex-enforce-log-budget
install -m 755 "$REPO_ROOT/ops/check_host_layout.sh" /usr/local/bin/xauex-check-host-layout

for unit in xauex.service xauex-signal.service xauex-signal.timer xauex-start.timer xauex-stop.service xauex-stop.timer xauex-trade-journal.service xauex-trade-journal.timer xauex-weekly-review.service xauex-weekly-review.timer xauex-web.service; do
  sed \
    -e "s|__REPO_ROOT__|$REPO_ROOT|g" \
    -e "s|__RUN_USER__|$RUN_USER|g" \
    "$REPO_ROOT/ops/$unit" > "/etc/systemd/system/$unit"
done

sed \
  -e "s|__REPO_ROOT__|$REPO_ROOT|g" \
  -e "s|__RUN_USER__|$RUN_USER|g" \
  "$REPO_ROOT/ops/logrotate-xauex.conf" > /etc/logrotate.d/xauex-gold-oracle

sed \
  -e "s|__REPO_ROOT__|$REPO_ROOT|g" \
  -e "s|__RUN_USER__|$RUN_USER|g" \
  "$REPO_ROOT/ops/xauex-daily-report.cron" > /etc/cron.d/xauex-daily-report
chmod 644 /etc/cron.d/xauex-daily-report

mkdir -p /etc/systemd/journald.conf.d
install -m 644 "$REPO_ROOT/ops/xauex-journald.conf" /etc/systemd/journald.conf.d/xauex-gold-oracle.conf

if command -v caddy >/dev/null 2>&1; then
  # Install the repo-owned VPN-only dashboard snippet into the local Caddy config.
  # The public relay is managed separately and is not configured by this script.
  mkdir -p /etc/caddy/Caddyfile.d
  install -m 644 "$REPO_ROOT/ops/xauex-dashboard.caddy" /etc/caddy/Caddyfile.d/xauex-dashboard.caddy
  if [[ -f /etc/caddy/Caddyfile ]]; then
    awk '
      $0 == "import /etc/caddy/Caddyfile.d/*.caddy" {
        if (seen++) {
          next
        }
      }
      { print }
    ' /etc/caddy/Caddyfile > /etc/caddy/Caddyfile.tmp
    mv /etc/caddy/Caddyfile.tmp /etc/caddy/Caddyfile
  fi
  if ! grep -Fxq 'import /etc/caddy/Caddyfile.d/*.caddy' /etc/caddy/Caddyfile 2>/dev/null; then
    printf '\nimport /etc/caddy/Caddyfile.d/*.caddy\n' >> /etc/caddy/Caddyfile
  fi
fi

systemctl daemon-reload
systemctl reset-failed oracle-dashboard.service mirofish-backend.service mirofish-bridge.service mirofish-bridge.timer >/dev/null 2>&1 || true
systemctl restart systemd-journald
systemctl disable xauex.service >/dev/null 2>&1 || true
systemctl disable oracle-dashboard.service mirofish-backend.service mirofish-bridge.service mirofish-bridge.timer >/dev/null 2>&1 || true
systemctl enable xauex-signal.timer xauex-start.timer xauex-stop.timer xauex-trade-journal.timer xauex-weekly-review.timer xauex-web.service
systemctl restart xauex-web.service
systemctl restart xauex-signal.timer
systemctl restart xauex-start.timer
systemctl restart xauex-stop.timer
systemctl restart xauex-trade-journal.timer
systemctl restart xauex-weekly-review.timer
if command -v caddy >/dev/null 2>&1; then
  systemctl reload caddy >/dev/null 2>&1 || systemctl restart caddy >/dev/null 2>&1 || true
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
  systemctl restart xauex.service
elif [[ "$SHOULD_RUN_XAUEX" -eq 1 ]]; then
  systemctl start xauex.service
fi

logrotate -f /etc/logrotate.d/xauex-gold-oracle >/dev/null 2>&1 || true
journalctl --vacuum-time=14d >/dev/null 2>&1 || true
journalctl --vacuum-size=256M >/dev/null 2>&1 || true
su -s /bin/bash "$RUN_USER" -c "$REPO_ROOT/ops/enforce_log_budget.sh 1887436800" >/dev/null 2>&1 || true

echo "Installed services from $REPO_ROOT"
echo "  xauex:   systemctl status xauex.service"
echo "  signal:  systemctl status xauex-signal.timer"
echo "  web:     systemctl status xauex-web.service"
echo "  xauex timers: systemctl status xauex-start.timer xauex-stop.timer xauex-trade-journal.timer xauex-weekly-review.timer"
