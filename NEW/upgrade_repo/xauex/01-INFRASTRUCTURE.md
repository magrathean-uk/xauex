# 01 — XAUEX: Infrastructure

## VPS Environment

- **OS:** Linux with MATE desktop
- **Spec:** 2 dedicated vCPU, 6GB total RAM (~3GB available)
- **Location:** Hungary
- **Bot RAM budget:** ≤256MB steady-state
- **Python required:** 3.11+

---

## Python Environment Setup

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3-pip git

mkdir -p /opt/xauex
cd /opt/xauex

python3.11 -m venv .venv
source .venv/bin/activate

pip install \
  ctrader-open-api \
  protobuf \
  python-dotenv \
  aiohttp \
  textual \
  pytest \
  pytest-asyncio

pip freeze > requirements.txt
```

---

## Credentials and Configuration

File: `/opt/xauex/.env` — never committed to version control (add to `.gitignore`).

```env
# cTrader Open API application credentials
CTRADER_CLIENT_ID=22757_bfByGhHUuLAp8I5xS1aFwEy2hNOiwQsvybbt5pIw1ot1Y16FpJ
CTRADER_CLIENT_SECRET=lvUqL9i9CG8gClQEqbuTwXAScfRzb3KrsdX7gU3C9UIHkAC062

# cTrader connection
CTRADER_HOST=demo-uk-eqx-01.p.c-trader.com
CTRADER_PORT=5035

# Account
CTRADER_ACCOUNT_ID=9911635

# OAuth tokens — populated by auth.py on first run, refreshed automatically
CTRADER_ACCESS_TOKEN=
CTRADER_REFRESH_TOKEN=
CTRADER_TOKEN_EXPIRY=

# Bot parameters
RISK_PERCENT=1.0
MAX_OPEN_TRADES=2
SL_MIN_DOLLARS=10.0
SL_MAX_DOLLARS=15.0
LEVEL_PROXIMITY_DOLLARS=3.0
NEWS_BLOCK_MINUTES=30
WEEKLY_STOP_PCT=5.0
MAX_CONSECUTIVE_LOSSES=3

# Set true until manual demo trading proves edge
OBSERVE_ONLY=true

# Paths
STATE_FILE_PATH=/var/lib/xauex/state.json
CMD_FILE_PATH=/var/lib/xauex/cmd.json
LOG_FILE_PATH=/var/log/xauex/xauex.log
```

Commit only `.env.example` with all keys present but values empty.

---

## OAuth2 First-Run Authentication (`auth.py`)

cTrader Open API uses OAuth2. Tokens must be obtained once via browser and then refreshed automatically by the bot.

`auth.py` handles the one-time flow:

1. Opens the cTrader authorisation URL in the default browser
2. Starts a temporary HTTP server on `http://localhost:8050`
3. Catches the redirect to `http://localhost:8050/callback?code=...`
4. Exchanges the code for access + refresh tokens
5. Writes tokens and expiry timestamp to `.env`
6. Exits

Run once before starting the bot for the first time:

```bash
cd /opt/xauex
source .venv/bin/activate
python auth.py
```

The browser will open automatically. Log in with IC Markets cTrader credentials for account 9911635. After authorisation, the terminal prints confirmation and tokens are saved to `.env`.

**OAuth endpoints for IC Markets demo:**
- Authorisation URL: `https://connect.spotware.com/apps/auth`
- Token URL: `https://connect.spotware.com/apps/token`
- Redirect URI: `http://localhost:8050/callback`
- Scope: `trading`

`auth.py` must pass `client_id`, `client_secret`, and `redirect_uri` exactly as registered on https://openapi.ctrader.com.

---

## Token Refresh (Handled by Bot)

The access token expires (typically 1 hour). The bot (`bot/api/client.py`) handles refresh automatically:

- On startup: check `CTRADER_TOKEN_EXPIRY` — if within 5 minutes of expiry, refresh immediately
- During operation: before every order placement, check expiry
- On refresh: update `CTRADER_ACCESS_TOKEN`, `CTRADER_REFRESH_TOKEN`, `CTRADER_TOKEN_EXPIRY` in `.env`
- If refresh fails: set `bot_status = HALTED_AUTH_FAILURE`, write state file, log CRITICAL

---

## Directory Layout

```
/opt/xauex/             # application code
/opt/xauex/.venv/       # Python virtual environment
/opt/xauex/.env         # credentials (chmod 600)
/var/lib/xauex/         # runtime state
/var/lib/xauex/state.json
/var/lib/xauex/cmd.json
/var/log/xauex/         # logs
/var/log/xauex/xauex.log
```

Create runtime directories:
```bash
sudo mkdir -p /var/lib/xauex /var/log/xauex
sudo chown $USER:$USER /var/lib/xauex /var/log/xauex
chmod 600 /opt/xauex/.env
```

---

## systemd Service Unit

File: `/opt/xauex/ops/xauex.service`

```ini
[Unit]
Description=XAUEX XAUUSD Trading Daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=xauex
WorkingDirectory=/opt/xauex
EnvironmentFile=/opt/xauex/.env
ExecStart=/opt/xauex/.venv/bin/python main.py
Restart=on-failure
RestartSec=30
StandardOutput=append:/var/log/xauex/xauex.log
StandardError=append:/var/log/xauex/xauex.log

[Install]
WantedBy=multi-user.target
```

Install and enable:
```bash
sudo cp ops/xauex.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable xauex
sudo systemctl start xauex
sudo systemctl status xauex
```

`RestartSec=30` — do not restart instantly on crash. A rapid restart loop during network outage risks order state corruption.

---

## Logging

- Level: `INFO` in production, `DEBUG` during development
- Every trade entry, exit, skip, and gate trigger logged with UTC timestamp
- Rotation: Python `RotatingFileHandler`, max 10MB per file, keep 5 files
- Never log tokens, passwords, or secrets

---

## cTrader Demo Connection Details

| Parameter | Value |
|---|---|
| Host | `demo-uk-eqx-01.p.c-trader.com` |
| Port | `5035` (cTrader Open API, SSL) |
| Account ID | `9911635` |
| Currency | GBP |
| Leverage | 1:100 |
| Mode | Hedging |
| Balance | £3,000 |

Verify port is reachable before first run:
```bash
nc -zv demo-uk-eqx-01.p.c-trader.com 5035
```

---

## Network

Hungary VPS to London Equinix latency ~20–40ms. Irrelevant for an H1 strategy. No VPS migration required.

Outbound TCP port 5035 must be open. Outbound TCP port 443 required for OAuth token exchange and ForexFactory calendar feed.
