# XAUEX Runbook

This repository is the live runtime folder. Do not use an obsolete sibling checkout for production operation.

For repo orientation and edit paths, read [../docs/CODEX_DISCOVERY.md](../docs/CODEX_DISCOVERY.md).

## Layout

- Repo root: `<repo-root>`
- Root config: `<repo-root>/.env`
- XAUEX config: `<repo-root>/xauex/.env`
- Runtime state and IPC: `/var/lib/xauex`
- XAUEX trade log: `/var/log/xauex/xauex.log`

## Host Model

- XAUEX dashboard HTTP on the VPN addresses redirects to HTTPS.
- XAUEX dashboard HTTPS is only exposed on `10.8.0.1` and `10.9.0.1`.
- The dashboard app itself listens on loopback only at `127.0.0.1:8089`.
- The public relay, if present, remains public and is not managed by the XAUEX Caddy snippet.
- If Pi-hole is installed on the host, its admin UI must be moved to `:8081` before XAUEX install so the host reverse-proxy layout matches the documented model.
- Use [Caddyfile.root.example](Caddyfile.root.example) and [pihole-compose.override.example.yml](pihole-compose.override.example.yml) as the repo-owned reference for that split.

## Services

- `xauex-web.service`: operator dashboard on port `8089`
- HTTPS dashboard over VPN: `https://10.8.0.1/` and `https://10.9.0.1/`
- `xauex-signal.timer`: triggers signal generation before the London windows
- `xauex-signal.service`: one-shot signal generation job
- `xauex.service`: weekday execution bot
- `xauex-start.timer`: starts `xauex.service` before the morning session
- `xauex-stop.service`: stops `xauex.service` at the Friday force-flat boundary
- `xauex-stop.timer`: schedules the stop boundary
- `xauex-trade-journal.timer`: writes the post-session trade journal
- `xauex-weekly-review.timer`: writes the weekly review
- `xauex-daily-report` cron: emails a daily GMT status summary at 20:00

The signal generator writes the latest command bundle to `/var/lib/xauex/cmd.json`. XAUEX polls that file and executes only the retained XAUEX live path.

Manual dashboard trades, when used, remain separate from XAUEX-owned positions and do not count toward the XAUEX trade/session limits.

The dashboard app itself listens on loopback only. VPN HTTPS is terminated by Caddy on the VPN interfaces and proxied to `127.0.0.1:8089`.
HTTP requests to the VPN dashboard hosts are redirected to HTTPS with a 308 response.

## Important Files

- `/var/lib/xauex/cmd.json`
- `/var/lib/xauex/latest_signal_brief.md`
- `/var/lib/xauex/latest_signal_evidence.json`
- `/var/lib/xauex/trade_journal.json`
- `/var/lib/xauex/weekly_review.json`
- `/var/lib/xauex/weekly_review.md`

## Install

```bash
cd <repo-root>
chmod +x ops/*.sh
sudo bash ops/install_systemd.sh
```

Before running the install script, confirm any host-owned services that use web ports have already been moved out of the way, especially Pi-hole admin on `:8081`.

The installer also runs:

```bash
xauex-check-host-layout --strict
```

If that fails, fix the host web-port layout before trusting the dashboard ingress again.

## Start

```bash
sudo systemctl start xauex-web.service
sudo systemctl start xauex-signal.timer
sudo systemctl start xauex-start.timer
sudo systemctl start xauex-stop.timer
sudo systemctl start xauex-trade-journal.timer
sudo systemctl start xauex-weekly-review.timer
```

## Stop

```bash
sudo systemctl stop xauex-weekly-review.timer
sudo systemctl stop xauex-trade-journal.timer
sudo systemctl stop xauex-stop.timer
sudo systemctl stop xauex-start.timer
sudo systemctl stop xauex-signal.timer
sudo systemctl stop xauex.service
sudo systemctl stop xauex-web.service
```

## Restart After Changes

```bash
sudo systemctl restart xauex-web.service
sudo systemctl restart xauex-signal.timer
sudo systemctl restart xauex-start.timer
sudo systemctl restart xauex-stop.timer
sudo systemctl restart xauex-trade-journal.timer
sudo systemctl restart xauex-weekly-review.timer
```

## Status

```bash
systemctl status xauex-web.service --no-pager
systemctl status xauex.service --no-pager
systemctl status xauex-signal.timer --no-pager
systemctl status xauex-start.timer xauex-stop.timer xauex-trade-journal.timer xauex-weekly-review.timer --no-pager
bash <repo-root>/status.sh
```

## Logs

```bash
journalctl -u xauex-web.service -f
journalctl -u caddy -f
journalctl -u xauex.service -f
journalctl -u xauex-signal.service -f
journalctl -u xauex-trade-journal.service -f
journalctl -u xauex-weekly-review.service -f
tail -f /var/log/xauex/xauex.log
tail -f <repo-root>/logs/xauex-signal.log
tail -f <repo-root>/logs/xauex-web.log
tail -f /var/log/xauex/xauex-daily-report.log
```

## HTTPS Notes

- Caddy terminates HTTPS only on the VPN addresses and proxies to `127.0.0.1:8089`.
- HTTP on the VPN addresses is redirected to HTTPS.
- The certificate is issued by Caddy's internal CA, not a public CA.
- Browsers on VPN clients will trust it only after the Caddy local root CA is installed on the client device.
- The public relay, if used, is separate and remains public-facing; this repo does not place the dashboard behind it.

## Trading Mode

This machine is intended to keep trading on demo. Confirm these values in `xauex/.env`:

```dotenv
CTRADER_HOST=demo-uk-eqx-01.p.c-trader.com
CTRADER_TLS_SERVER_NAME=connect.spotware.com
OBSERVE_ONLY=false
XAUEX_MODE=true
RISK_PERCENT=1.5
XAUEX_ENTRY_START_LONDON=08:00
XAUEX_ENTRY_END_LONDON=08:05
XAUEX_ENTRY_SECOND_START_LONDON=11:30
XAUEX_ENTRY_SECOND_END_LONDON=11:35
XAUEX_FORCE_FLAT_LONDON=15:00
XAUEX_MAX_TRADES_PER_DAY=2
XAUEX_CASH_TAKE_PROFIT_GBP=50
XAUEX_CASH_STOP_LOSS_GBP=50
CMD_FILE_PATH=/var/lib/xauex/cmd.json
LOG_FILE_PATH=/var/log/xauex/xauex.log
```

## Manual Signal Refresh

```bash
cd <repo-root>
./.venv/bin/python -m xauex.signal.run --asset XAUUSD --auto-context
```

## Notes

- The supported interactive run paths are the XAUEX modules and scripts in `ops/`.
- The signal service runs non-interactively as a module because that import path is stable.
