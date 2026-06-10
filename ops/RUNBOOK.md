# XAUEX Runbook

This repository is the production runtime folder for XAUEX. Do not operate an obsolete sibling checkout.

For repo orientation before editing code, read `../docs/CODEX_DISCOVERY.md`.

## Host Model

- Repo root: `/home/bolyki/mirofish-gold-oracle` on the live host.
- Root config: `<repo-root>/.env`.
- Bot config: `<repo-root>/xauex/.env`.
- Runtime state and IPC: `/var/lib/xauex`.
- Bot log: `/var/log/xauex/xauex.log`.
- Wrapper logs: `<repo-root>/logs`.
- Dashboard app: `127.0.0.1:8089` only.
- Dashboard ingress: Caddy on VPN addresses `10.8.0.1` and `10.9.0.1`.
- VPN HTTP redirects to HTTPS.
- The public relay, if present, is separate from XAUEX.
- Pi-hole admin, if installed, must stay off the dashboard ports. Use `pihole-compose.override.example.yml` as the reference split.

## Active Services And Timers

Core runtime:

- `xauex-web.service`: Flask/waitress dashboard and API.
- `xauex.service`: cTrader bot and execution loop.
- `xauex-start.timer`: starts `xauex.service` before the weekday session.
- `xauex-stop.timer`: triggers the Friday stop/force-flat boundary.
- `xauex-stop.service`: executes the Friday stop/force-flat boundary.

Signal windows:

- `xauex-window-signal@morning.timer`: `07:55 Europe/London`.
- `xauex-window-confirm@morning.timer`: `07:59 Europe/London`.
- `xauex-window-signal@midday.timer`: `11:25 Europe/London`.
- `xauex-window-confirm@midday.timer`: `11:29 Europe/London`.
- `xauex-window-signal@us_open.timer`: `08:25 America/New_York`.
- `xauex-window-confirm@us_open.timer`: `08:29 America/New_York`.

Post-session and shadow jobs:

- `xauex-trade-journal.timer`: post-session trade journal at `15:07 Europe/London`.
- `xauex-weekly-review.timer`: Friday weekly review at `15:15 Europe/London`.
- `xauex-shadow-compare.timer`: baseline-vs-debate compare after each window.
- `xauex-shadow-evaluate.timer`: resolves shadow trials after the evaluation horizon.

Alerting:

- Monit handles alert emails.
- `xauex-morning-summary`: one morning decision email.
- `xauex-trade-alerts`: trade-open emails.
- `xauex-signal-stall`: repeated source-blocked HOLD and degraded source fallback emails.
- Service failure emails use the host `systemd-email-alert@...` wiring where units declare `OnFailure=`.

Retired:

- No `/etc/cron.d/xauex-daily-report`.
- No `xauex-shadow-report.timer`.
- No scheduled daily status report or weekly shadow report email.

## Install Or Reinstall

```bash
cd /home/bolyki/mirofish-gold-oracle
chmod +x ops/*.sh
sudo bash ops/install_systemd.sh
```

The installer:

- installs systemd units and wrapper scripts;
- installs Monit XAUEX notification checks;
- installs the VPN-only Caddy snippet if Caddy exists;
- removes retired daily-report and shadow-report units/scripts if they are still on the host;
- validates the host layout with `xauex-check-host-layout --strict`;
- restarts dashboard/timers and starts the bot only if the current London schedule says it should be running.

## Start

```bash
sudo systemctl start xauex-web.service
sudo systemctl start xauex-window-signal@morning.timer
sudo systemctl start xauex-window-signal@midday.timer
sudo systemctl start xauex-window-signal@us_open.timer
sudo systemctl start xauex-window-confirm@morning.timer
sudo systemctl start xauex-window-confirm@midday.timer
sudo systemctl start xauex-window-confirm@us_open.timer
sudo systemctl start xauex-shadow-compare.timer
sudo systemctl start xauex-shadow-evaluate.timer
sudo systemctl start xauex-start.timer
sudo systemctl start xauex-stop.timer
sudo systemctl start xauex-trade-journal.timer
sudo systemctl start xauex-weekly-review.timer
```

Start the bot directly only when you intentionally need it outside the timer schedule:

```bash
sudo systemctl start xauex.service
```

## Stop

```bash
sudo systemctl stop xauex-weekly-review.timer
sudo systemctl stop xauex-trade-journal.timer
sudo systemctl stop xauex-stop.timer
sudo systemctl stop xauex-start.timer
sudo systemctl stop xauex-shadow-evaluate.timer
sudo systemctl stop xauex-shadow-compare.timer
sudo systemctl stop xauex-window-confirm@us_open.timer
sudo systemctl stop xauex-window-confirm@midday.timer
sudo systemctl stop xauex-window-confirm@morning.timer
sudo systemctl stop xauex-window-signal@us_open.timer
sudo systemctl stop xauex-window-signal@midday.timer
sudo systemctl stop xauex-window-signal@morning.timer
sudo systemctl stop xauex.service
sudo systemctl stop xauex-web.service
```

## Restart After Repo Changes

For normal code/config changes:

```bash
sudo systemctl restart xauex-web.service
sudo systemctl restart xauex-window-signal@morning.timer
sudo systemctl restart xauex-window-signal@midday.timer
sudo systemctl restart xauex-window-signal@us_open.timer
sudo systemctl restart xauex-window-confirm@morning.timer
sudo systemctl restart xauex-window-confirm@midday.timer
sudo systemctl restart xauex-window-confirm@us_open.timer
sudo systemctl restart xauex-shadow-compare.timer
sudo systemctl restart xauex-shadow-evaluate.timer
sudo systemctl restart xauex-start.timer
sudo systemctl restart xauex-stop.timer
sudo systemctl restart xauex-trade-journal.timer
sudo systemctl restart xauex-weekly-review.timer
```

Restart `xauex.service` only if you changed bot runtime code or config and are prepared to interrupt the active session:

```bash
sudo systemctl restart xauex.service
```

## Status

```bash
bash /home/bolyki/mirofish-gold-oracle/status.sh
systemctl --failed --no-pager
systemctl status xauex-web.service --no-pager
systemctl status xauex.service --no-pager
systemctl status xauex-window-signal@morning.timer xauex-window-signal@midday.timer xauex-window-signal@us_open.timer --no-pager
systemctl status xauex-window-confirm@morning.timer xauex-window-confirm@midday.timer xauex-window-confirm@us_open.timer --no-pager
systemctl status xauex-shadow-compare.timer xauex-shadow-evaluate.timer --no-pager
systemctl status xauex-start.timer xauex-stop.timer xauex-trade-journal.timer xauex-weekly-review.timer --no-pager
monit summary
```

## Dashboard Checks

Local checks:

```bash
curl -fsS http://127.0.0.1:8051/health
curl -fsS http://127.0.0.1:8089/api/dashboard
```

VPN ingress checks from the host:

```bash
curl -I http://10.8.0.1/
curl -I http://10.8.0.1:8089/
curl -Ik https://10.8.0.1/api/dashboard
curl -Ik https://10.9.0.1/api/dashboard
```

Expected model:

- `http://10.x.0.1/` redirects to HTTPS.
- `http://10.x.0.1:8089/` is compatibility redirect traffic.
- `https://10.x.0.1/api/dashboard` returns JSON.
- Caddy uses an internal CA; VPN clients need that CA installed to avoid browser warnings.

## Logs

```bash
journalctl -u xauex-web.service -f
journalctl -u xauex.service -f
journalctl -u xauex-window-signal@morning.service -u xauex-window-signal@midday.service -u xauex-window-signal@us_open.service -f
journalctl -u xauex-window-confirm@morning.service -u xauex-window-confirm@midday.service -u xauex-window-confirm@us_open.service -f
journalctl -u xauex-trade-journal.service -f
journalctl -u xauex-weekly-review.service -f
journalctl -u caddy -f
tail -f /var/log/xauex/xauex.log
tail -f logs/xauex-web.log
tail -f logs/xauex-signal-morning.log
tail -f logs/xauex-confirm-morning.log
tail -f logs/xauex-shadow-compare.log
tail -f logs/xauex-shadow-evaluate.log
```

## Runtime State Files

- `/var/lib/xauex/cmd.json`: latest command bundle consumed by the bot.
- `/var/lib/xauex/state.json`: dashboard/runtime snapshot.
- `/var/lib/xauex/risk_state.json`: daily/weekly risk counters and slot usage.
- `/var/lib/xauex/latest_signal_brief.md`: latest operator brief.
- `/var/lib/xauex/latest_signal_evidence.json`: latest evidence payload.
- `/var/lib/xauex/signal_runs/`: archived signal runs.
- `/var/lib/xauex/shadow_trials/`: shadow compare/evaluate artifacts.
- `/var/lib/xauex/trade_journal.json`: closed-trade journal.
- `/var/lib/xauex/weekly_review.json`: weekly review data.
- `/var/lib/xauex/weekly_review.md`: weekly review markdown.

## Trading Mode

Confirm these values in `xauex/.env` before trusting live demo execution:

```dotenv
CTRADER_HOST=demo-uk-eqx-01.p.c-trader.com
CTRADER_TLS_SERVER_NAME=connect.spotware.com
XAUEX_MODE=true
CMD_FILE_PATH=/var/lib/xauex/cmd.json
LOG_FILE_PATH=/var/log/xauex/xauex.log
STATE_FILE_PATH=/var/lib/xauex/state.json
XAUEX_MAX_TRADES_PER_DAY=3
XAUEX_CASH_TAKE_PROFIT_GBP=50
XAUEX_CASH_STOP_LOSS_GBP=50
```

## Manual Signal Refresh

```bash
cd /home/bolyki/mirofish-gold-oracle
./.venv/bin/python -m xauex.signal.run --asset XAUUSD --auto-context
```

## Manual Confirm Refresh

```bash
cd /home/bolyki/mirofish-gold-oracle
./.venv/bin/python -m xauex.signal.confirm --window-label morning
```

Use `midday` or `us_open` for other windows.

## Validation

Targeted checks:

```bash
python3 -m pytest tests/test_xauex_trade_alerts.py tests/test_xauex_signal_stall_alerts.py tests/test_xauex_runtime_monitor.py tests/dashboard/test_manual_controls.py tests/bridge/test_signal_writer.py -q
python3 -m pytest xauex/tests/test_session_manager.py xauex/tests/test_xauex_signal_policy.py xauex/tests/test_trailing_integration.py -q
```

Full suite:

```bash
python3 -m pytest
```
