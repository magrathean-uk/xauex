# Rebuild Guide

This guide is for recreating XAUEX on a fresh Linux host.

It assumes:

- Ubuntu 22.04+ or similar Debian-based system
- Python 3.11+ for the virtualenv and test runs
- passwordless `sudo` or root access
- outbound HTTPS access
- a cTrader demo account
- LLM provider credentials
- Zep Cloud credentials if you want live graph builds

## 1. System Packages

Install the base packages:

```bash
sudo apt update
sudo apt install -y \
  python3 python3-venv python3-dev python3-pip \
  build-essential curl git jq \
  nodejs npm nginx logrotate
```

If the distro Python is too old for your deployment target, install Python 3.11+ first and use that interpreter for the virtualenv.

## 2. Clone The Repo

```bash
git clone <your-github-url> xauex
cd xauex
```

## 3. Create Python Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Notes:

- root dependencies install into `.venv`
- `backend/.venv` and `xauex/.venv` are symlinked at runtime by the local setup script only when needed
- the committed repo does not include a virtualenv

## 4. Build Frontend

```bash
cd frontend
npm ci
npm run build
cd ..
```

This creates `frontend/dist`, which nginx serves in production.

## 5. Configure Environment Files

Create both config files:

```bash
cp .env.example .env
cp xauex/.env.example xauex/.env
```

### Root `.env`

This is used by:

- `backend/`
- `bridge/`
- frontend API base behavior via nginx proxying

Fill in at minimum:

- LLM provider key and base URL
- model name
- Zep API key
- bridge parser model settings if using a separate parser model

Recommended current shape:

```dotenv
LLM_API_KEY=...
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL_NAME=llama-3.1-8b-instant
BRIDGE_PARSER_LLM_API_KEY=...
BRIDGE_PARSER_LLM_BASE_URL=https://api.groq.com/openai/v1
BRIDGE_PARSER_LLM_MODEL=llama-3.3-70b-versatile
ZEP_API_KEY=...
MIROFISH_URL=http://127.0.0.1:5001
SIGNAL_OUTPUT_PATH=/var/lib/xauex/cmd.json
BRIDGE_MAX_ROUNDS=18
```

### `xauex/.env`

This is used by the live execution bot.

Fill in at minimum:

- cTrader client credentials
- account ID
- access and refresh token
- execution/risk settings

Current live-style settings should include values equivalent to:

```dotenv
CTRADER_HOST=demo-uk-eqx-01.p.c-trader.com
CTRADER_TLS_SERVER_NAME=connect.spotware.com
OBSERVE_ONLY=false
MIROFISH_MODE=true
CMD_FILE_PATH=/var/lib/xauex/cmd.json
LOG_FILE_PATH=/var/log/xauex/xauex.log
STATE_FILE_PATH=/var/lib/xauex/state.json
```

Then add your London-open timing and risk configuration as required by your deployment.

Analyst jobs such as the trade journal and weekly review now reuse the same OpenAI-compatible API settings from the repo root `.env` by default. If you want a different cheap reporting model, set `XAUEX_ANALYST_MODEL` in `xauex/.env`.

## 6. Install Services

Install the systemd units, journald cap, and log rotation:

```bash
sudo bash ops/install_systemd.sh
```

That installs:

- `mirofish-backend.service`
- `mirofish-bridge.service`
- `mirofish-bridge.timer`
- `xauex.service`
- `xauex-start.timer`
- `xauex-stop.service`
- `xauex-stop.timer`
- `xauex-trade-journal.service`
- `xauex-trade-journal.timer`
- `xauex-weekly-review.service`
- `xauex-weekly-review.timer`

It also installs:

- logrotate policy for app logs
- journald retention cap
- log-budget enforcement script

## 7. Verify Runtime

Check services:

```bash
systemctl status mirofish-backend.service --no-pager
systemctl status mirofish-bridge.timer --no-pager
systemctl status xauex-start.timer xauex-stop.timer --no-pager
systemctl status xauex.service --no-pager
```

Check the repo helper:

```bash
bash status.sh
```

Check logs:

```bash
tail -f /var/log/xauex/xauex.log
tail -f logs/backend.log
tail -f logs/bridge-run.log
tail -f logs/bridge-run-error.log
```

## 8. Frontend / Website

The expected nginx site points to:

- repo root frontend build: `frontend/dist`
- backend API proxy: `/api` -> `127.0.0.1:5001`

If you are rebuilding nginx manually, ensure the site has:

- `root /path/to/xauex/frontend/dist;`
- `try_files $uri $uri/ /index.html;`
- `/api` proxying to the backend service

## 9. What Is Not In Git

These are intentionally excluded:

- `.env`
- `xauex/.env`
- `.venv`
- `frontend/node_modules`
- `frontend/dist`
- `logs/`
- `backend/logs/`
- `backend/uploads/`
- local caches and compiled artifacts

That means a rebuild requires reinstalling dependencies and rebuilding the frontend, but not reconstructing code.

## 10. Recovery Checklist

If moving to a new machine:

1. clone repo
2. create `.env`
3. create `xauex/.env`
4. create `.venv`
5. `pip install -r requirements.txt`
6. `cd frontend && npm ci && npm run build`
7. `sudo bash ops/install_systemd.sh`
8. confirm timers
9. confirm `/api/report/signal`
10. confirm nginx site loads and history section is visible

## 11. Known External Dependencies

Two external providers can still stop live bridge freshness:

- Zep quota / episode limits during graph build
- LLM provider quota / balance

The bridge now has a history fallback for graph-build quota failures, so a Zep limit does not necessarily kill signal generation, but live freshness still depends on those upstream services being healthy.
