# XAUEX Rebuild

This guide describes the fresh-host path encoded by the current repository. It does not assert current host state.

## Prerequisites

- Debian/Ubuntu-style host with Python 3.11+, `git`, `curl`, `systemd`, and sudo access.
- Outbound HTTPS.
- cTrader demo application/account credentials.
- Optional Caddy, Monit, and sendmail-compatible delivery for the checked-in ingress/alert integrations.
- Optional Docker or a supported Python runtime only when using the disabled-by-default DSA sidecar.

## Clone and configure

```bash
git clone https://github.com/magrathean-uk/xauex.git
cd xauex
cp .env.example .env
cp xauex/.env.example xauex/.env
```

The root environment configures signal/dashboard sources. `xauex/.env` configures the cTrader demo runtime. At minimum, set real demo credentials and keep these paths aligned:

```dotenv
CTRADER_HOST=demo-uk-eqx-01.p.c-trader.com
CTRADER_TLS_SERVER_NAME=connect.spotware.com
XAUEX_MODE=true
CMD_FILE_PATH=/var/lib/xauex/cmd.json
STATE_FILE_PATH=/var/lib/xauex/state.json
LOG_FILE_PATH=/var/log/xauex/xauex.log
```

Do not use documentation examples as secret values and do not commit either environment file.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
sudo bash ops/install_systemd.sh
```

The installer renders repo-path/user placeholders, installs the bot/dashboard/window/review units, installs monitoring and VPN ingress when available, enables the maintained units, installs `dsa-sidecar.service` disabled, and runs `xauex-check-host-layout --strict`.

Window timers are rendered from `xauex/live_windows.py`; do not hand-copy old clock values into systemd.

## Verify on the target host

```bash
systemctl --failed --no-pager
systemctl status xauex.service xauex-web.service --no-pager
systemctl status xauex-window-signal@morning.timer xauex-window-signal@midday.timer xauex-window-signal@us_open.timer --no-pager
systemctl status xauex-window-confirm@morning.timer xauex-window-confirm@midday.timer xauex-window-confirm@us_open.timer --no-pager
systemctl status xauex-start.timer xauex-stop.timer xauex-trade-journal.timer xauex-decision-ledger.timer xauex-weekly-review.timer --no-pager
systemctl is-enabled dsa-sidecar.service
curl -fsS http://127.0.0.1:8051/health
curl -fsS http://127.0.0.1:8089/api/dashboard
sudo /usr/local/bin/xauex-check-host-layout --strict
monit summary
```

Expected DSA posture is disabled unless it was intentionally configured. Follow [DSA_SIDECAR.md](./DSA_SIDECAR.md) before starting it.

## Validate code

```bash
source .venv/bin/activate
python3 -m pytest
python3 -m pytest xauex/tests/test_xauex_windows.py xauex/tests/test_session_manager.py xauex/tests/test_manual_trade_commands.py -q
```

Runtime state under `/var/lib/xauex`, logs, environment files, and `.venv` are recreated locally and remain outside git.
