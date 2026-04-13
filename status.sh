#!/usr/bin/env bash
set -uo pipefail

echo '=== XAUEX Status ===' && date
echo
echo '=== Services ==='
systemctl --no-pager --no-legend --plain status \
  xauex-web.service \
  xauex.service \
  xauex-signal.timer \
  xauex-start.timer \
  xauex-stop.timer \
  xauex-trade-journal.timer \
  xauex-weekly-review.timer 2>/dev/null | sed -n '1,16p'
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
echo '=== Recent XAUEX Logs ==='
journalctl --no-pager -u xauex-web.service -u xauex.service -u xauex-signal.service -u xauex-trade-journal.service -u xauex-weekly-review.service -n 20 2>/dev/null || echo 'No recent journal entries.'
