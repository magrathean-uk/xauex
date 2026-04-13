# Rebuild Guide

This guide recreates XAUEX on a fresh Linux host.

Read [CODEX_DISCOVERY.md](CODEX_DISCOVERY.md) after rebuild if you need a current codebase map.

## 1. Prerequisites

- Ubuntu 22.04+ or similar Debian-based system
- Python 3.11+
- passwordless `sudo` or root access
- outbound HTTPS access
- a cTrader demo account
- if this host also runs Pi-hole, move the Pi-hole admin UI to `:8081` before you install XAUEX so the standard web ports remain free for the host's own reverse-proxy setup
- if this host exposes a public relay, keep that relay separate from the XAUEX dashboard; XAUEX expects VPN-only HTTPS on the 10.8.0.1 and 10.9.0.1 interfaces
- use [../ops/Caddyfile.root.example](../ops/Caddyfile.root.example) and [../ops/pihole-compose.override.example.yml](../ops/pihole-compose.override.example.yml) as the repo-owned starting point for that host layout

## 2. Clone The Repo

```bash
git clone <your-github-url> xauex
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

### Root `.env`

This file is used by the XAUEX runtime and dashboard support code.

Fill in the API key, base URL, model name, and any optional memory settings required by your deployment.

### `xauex/.env`

This file is used by the XAUEX execution bot and service wrappers.

At minimum, provide the cTrader credentials and execution settings. Keep the live demo endpoint unless your broker instructs otherwise:

```dotenv
CTRADER_HOST=demo-uk-eqx-01.p.c-trader.com
CTRADER_TLS_SERVER_NAME=connect.spotware.com
OBSERVE_ONLY=false
XAUEX_MODE=true
CMD_FILE_PATH=/var/lib/xauex/cmd.json
LOG_FILE_PATH=/var/log/xauex/xauex.log
STATE_FILE_PATH=/var/lib/xauex/state.json
```

## 5. Install Services

Install the systemd units, journald cap, and log rotation:

```bash
sudo bash ops/install_systemd.sh
```

This installs:

- `xauex-web.service`
- `xauex-signal.service`
- `xauex-signal.timer`
- `xauex.service`
- `xauex-start.timer`
- `xauex-stop.service`
- `xauex-stop.timer`
- `xauex-trade-journal.service`
- `xauex-trade-journal.timer`
- `xauex-weekly-review.service`
- `xauex-weekly-review.timer`
- `/etc/cron.d/xauex-daily-report`
- the VPN-only Caddy snippet that redirects dashboard HTTP to HTTPS on the VPN interfaces and reverse-proxies to `127.0.0.1:8089`
- `/usr/local/bin/xauex-check-host-layout`, which validates the Caddy/Pi-hole host layout and causes the install to fail if the host still conflicts with XAUEX's VPN HTTPS model

It also installs:

- logrotate policy for app logs
- journald retention cap
- log-budget enforcement script

## 6. Verify Runtime

Check services:

```bash
systemctl status xauex-web.service --no-pager
systemctl status xauex-signal.timer --no-pager
systemctl status xauex-start.timer xauex-stop.timer --no-pager
systemctl status xauex.service --no-pager
```

Confirm the dashboard ingress model:

```bash
curl -I http://10.8.0.1/
curl -Ik https://10.8.0.1/
```

Check the repo helper:

```bash
bash status.sh
```

Check logs:

```bash
tail -f /var/log/xauex/xauex.log
tail -f logs/xauex-signal.log
tail -f logs/xauex-signal-error.log
```

## 7. What Is Not In Git

These are intentionally excluded:

- `.env`
- `xauex/.env`
- `.venv`
- `logs/`
- local caches and compiled artifacts

## 8. Recovery Checklist

If moving to a new machine:

1. clone the repo
2. create `.env`
3. create `xauex/.env`
4. create `.venv`
5. `pip install -r requirements.txt`
6. `sudo bash ops/install_systemd.sh`
