#!/usr/bin/env bash
set -uo pipefail

echo '=== XAUEX Status ===' && date
echo
echo '=== Services ==='
systemctl --no-pager --no-legend --plain status \
  xauex-web.service \
  xauex.service \
  xauex-window-signal@morning.timer \
  xauex-window-signal@midday.timer \
  xauex-window-signal@us_open.timer \
  xauex-window-confirm@morning.timer \
  xauex-window-confirm@midday.timer \
  xauex-window-confirm@us_open.timer \
  xauex-shadow-compare.timer \
  xauex-shadow-evaluate.timer \
  xauex-start.timer \
  xauex-stop.timer \
  xauex-trade-journal.timer \
  xauex-weekly-review.timer 2>/dev/null | sed -n '1,28p'
echo
echo '=== XAUEX Snapshot ==='
if DASHBOARD_JSON=$(curl -fsS http://127.0.0.1:8089/api/dashboard 2>/dev/null); then
  DASHBOARD_JSON="$DASHBOARD_JSON" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["DASHBOARD_JSON"]).get("data", {})
signal = payload.get("signal", {}) or {}
diagnostics = payload.get("diagnostics", {}) or {}
account = payload.get("account", {}) or {}
components = diagnostics.get("components", {}) or {}
bot = components.get("bot", {}) or {}
quote = components.get("quote", {}) or {}

print("Signal:", signal.get("action", "UNKNOWN"), "|", signal.get("generated_at_utc") or "-")
print("Signal reasoning:", signal.get("reasoning") or "No signal rationale available.")
print("XAUEX health:", diagnostics.get("overall_status", "unknown"), "|", diagnostics.get("summary") or "-")
print("Bot state:", bot.get("state") or "-")
print("Quote state:", quote.get("state") or "-")
print("Signal runs:", f"{account.get('signal_runs_taken_today', 0)}/{account.get('signal_runs_cap', 2)}")
print("Trades used:", f"{account.get('trades_taken_today', 0)}/{account.get('trade_cap', 2)}")
PY
else
  echo 'Dashboard API unavailable.'
fi
echo
echo '=== XAUEX Monit Checks ==='
if command -v monit >/dev/null 2>&1; then
  monit summary 2>/dev/null | grep -Ei 'xauex|trade-alert|morning-summary|dashboard|runtime' || echo 'No XAUEX Monit checks found.'
else
  echo 'Monit not installed.'
fi
echo
echo '=== Recent XAUEX Logs ==='
journalctl --no-pager -u xauex-web.service -u xauex.service -u xauex-window-signal@morning.service -u xauex-window-signal@midday.service -u xauex-window-signal@us_open.service -u xauex-window-confirm@morning.service -u xauex-window-confirm@midday.service -u xauex-window-confirm@us_open.service -u xauex-trade-journal.service -u xauex-weekly-review.service -n 20 2>/dev/null || echo 'No recent journal entries.'
