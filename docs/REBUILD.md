# Rebuild Guide

This guide rebuilds XAUEX v1.0 on a fresh Debian/Ubuntu-style host.

Read `CODEX_DISCOVERY.md` after the rebuild if you need a codebase map.

## 1. Prerequisites

- Debian/Ubuntu-style Linux host.
- Python 3.11+.
- `git`, `curl`, `systemd`, and root/sudo access.
- Outbound HTTPS access.
- cTrader demo credentials.
- Optional but expected on the live host: Caddy, Monit, and sendmail-compatible local mail delivery.
- If Pi-hole runs on this host, keep its admin UI off the dashboard ports. The reference mapping is `../ops/pihole-compose.override.example.yml`.
- If a public relay runs on this host, keep it separate from XAUEX. XAUEX dashboard ingress is VPN-only.

## 2. Clone The Repo

```bash
git clone https://github.com/magrathean-uk/xauex.git
cd xauex
```

## 3. Create The Python Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 4. Configure Environment Files

Create both config files:

```bash
cp .env.example .env
cp xauex/.env.example xauex/.env
```

Root `.env` is used by the signal/dashboard support code. Fill in the LLM/API settings required by the deployment.

`xauex/.env` is used by the cTrader bot and service wrappers. Minimum live-demo shape:

```dotenv
CTRADER_HOST=demo-uk-eqx-01.p.c-trader.com
CTRADER_TLS_SERVER_NAME=connect.spotware.com
XAUEX_MODE=true
CMD_FILE_PATH=/var/lib/xauex/cmd.json
LOG_FILE_PATH=/var/log/xauex/xauex.log
STATE_FILE_PATH=/var/lib/xauex/state.json
```

## 5. Install Services

```bash
sudo bash ops/install_systemd.sh
```

The installer creates runtime directories, installs wrappers, installs units, reloads systemd, installs Monit XAUEX checks, applies Caddy dashboard ingress if Caddy exists, and validates the host layout.

Installed systemd units:

- `xauex-web.service`
- `xauex.service`
- `xauex-signal.service`
- `xauex-signal.timer` for manual/legacy one-shot support, disabled by default
- `xauex-window-signal@morning.timer`
- `xauex-window-signal@midday.timer`
- `xauex-window-signal@us_open.timer`
- `xauex-window-confirm@morning.timer`
- `xauex-window-confirm@midday.timer`
- `xauex-window-confirm@us_open.timer`
- `xauex-shadow-compare.service`
- `xauex-shadow-compare.timer`
- `xauex-shadow-evaluate.service`
- `xauex-shadow-evaluate.timer`
- `xauex-start.timer`
- `xauex-stop.service`
- `xauex-stop.timer`
- `xauex-trade-journal.service`
- `xauex-trade-journal.timer`
- `xauex-weekly-review.service`
- `xauex-weekly-review.timer`

Installed monitoring files:

- `/etc/monit/conf-enabled/45-xauex-notify.monit`
- `/usr/local/lib/monitoring/check_xauex_runtime.sh`
- `/usr/local/lib/monitoring/check_xauex_morning_summary.py`
- `/usr/local/lib/monitoring/check_xauex_trade_alerts.py`

Retired files removed by the installer if present:

- `/etc/cron.d/xauex-daily-report`
- `/etc/systemd/system/xauex-shadow-report.service`
- `/etc/systemd/system/xauex-shadow-report.timer`
- `/usr/local/bin/xauex-run-daily-report`
- `/usr/local/bin/xauex-run-shadow-report`

There is no scheduled daily status email and no scheduled weekly shadow report email in v1.0.

## 6. Verify Runtime

Check service state:

```bash
systemctl --failed --no-pager
systemctl status xauex-web.service --no-pager
systemctl status xauex.service --no-pager
systemctl status xauex-window-signal@morning.timer xauex-window-signal@midday.timer xauex-window-signal@us_open.timer --no-pager
systemctl status xauex-window-confirm@morning.timer xauex-window-confirm@midday.timer xauex-window-confirm@us_open.timer --no-pager
systemctl status xauex-shadow-compare.timer xauex-shadow-evaluate.timer --no-pager
systemctl status xauex-start.timer xauex-stop.timer xauex-trade-journal.timer xauex-weekly-review.timer --no-pager
```

Check dashboard and bot APIs:

```bash
curl -fsS http://127.0.0.1:8051/health
curl -fsS http://127.0.0.1:8089/api/dashboard
```

Check VPN ingress:

```bash
curl -I http://10.8.0.1/
curl -I http://10.8.0.1:8089/
curl -Ik https://10.8.0.1/api/dashboard
curl -Ik https://10.9.0.1/api/dashboard
```

Check monitoring:

```bash
monit summary
```

Run the repo helper:

```bash
bash status.sh
```

## 7. Validate Tests

Targeted post-rebuild checks:

```bash
python3 -m pytest tests/test_xauex_trade_alerts.py tests/test_xauex_runtime_monitor.py tests/dashboard/test_manual_controls.py tests/bridge/test_signal_writer.py -q
python3 -m pytest xauex/tests/test_session_manager.py xauex/tests/test_xauex_signal_policy.py xauex/tests/test_trailing_integration.py -q
```

Full suite:

```bash
python3 -m pytest
```

## 8. Runtime Files Not In Git

These must be recreated or generated on the host:

- `.env`
- `xauex/.env`
- `.venv`
- `logs/`
- `/var/lib/xauex/*`
- `/var/log/xauex/*`

These are intentionally excluded from git.

## 9. Recovery Checklist

1. Clone `https://github.com/magrathean-uk/xauex.git`.
2. Recreate `.env`.
3. Recreate `xauex/.env`.
4. Build `.venv`.
5. Install dependencies.
6. Run `sudo bash ops/install_systemd.sh`.
7. Verify dashboard, bot health, timers, Monit, and logs.
