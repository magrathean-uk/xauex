# MiroFish Gold Oracle Runbook

This repository is the only runtime folder. Do not use `/home/bolyki/xauex` for live operation anymore.

## Layout

- Repo root: `/home/bolyki/mirofish-gold-oracle`
- Backend config: `/home/bolyki/mirofish-gold-oracle/.env`
- XAUEX config: `/home/bolyki/mirofish-gold-oracle/xauex/.env`
- Bridge/backend logs: `/home/bolyki/mirofish-gold-oracle/logs`
- XAUEX state and IPC: `/var/lib/xauex`
- XAUEX trade log: `/var/log/xauex/xauex.log`

## Runtime Model

- `mirofish-backend.service`: keeps the Flask backend running all the time.
- `oracle-dashboard.service`: lightweight web dashboard on port `8089` for bot state, signal, positions, trades, and account metrics.
- `xauex.service`: weekday trading process that stays up through the work week.
- `xauex-start.timer`: starts `xauex.service` at `07:25 Europe/London`, Monday to Friday.
- `xauex-stop.timer`: stops `xauex.service` at `12:05 Europe/London` on Friday so it does not run on weekends.
- `xauex-trade-journal.timer`: journals the day’s closed trades at `12:06 Europe/London`, Monday to Friday.
- `xauex-weekly-review.timer`: writes the current-week review at `12:15 Europe/London` on Friday.
- `mirofish-bridge.timer`: refreshes the signal once per weekday at `07:35 Europe/London`.
- `mirofish-bridge.service`: one-shot signal generation job triggered by the timer or manually.

XAUEX is configured to poll `/var/lib/xauex/cmd.json` and trade Oracle signals when `MIROFISH_MODE=true` in `xauex/.env`.

XAUEX also supports a separate manual command lane through:

- `/var/lib/xauex/manual_trade_cmd.json`

The dashboard writes manual commands there directly. XAUEX executes them with the same broker connection, but Oracle ignores those manual positions for its own daily limits, predictor memory, and session management.

There is no app-level dashboard password now. Access is expected to stay limited by VPN/localhost-only network rules.

The current live signal path is `direct`:

- fetch fresh curated macro / gold context
- blend that with local price structure and recent local trade memory
- generate one morning `BUY` / `SELL` / rare `HOLD`
- write:
  - `/var/lib/xauex/cmd.json`
  - `/var/lib/xauex/latest_signal_brief.md`
  - `/var/lib/xauex/latest_signal_evidence.json`

The old MiroFish graph/simulation path is retained only as an explicit fallback/research path and is no longer the intended daily live dependency.

## Oracle Session Manager

Oracle-managed trades use a staged lifecycle:

- `OBSERVE`: initial wide catastrophe stop only
- `PROTECT`: move stop to breakeven-plus cushion after the move reaches the protect threshold
- `TRAIL`: trail the stop by structure/ATR once follow-through is stronger

Cash risk remains capped. Wider stops reduce lot size; they do not increase maximum allowed cash risk.

## Install

One-time setup:

```bash
cd /home/bolyki/mirofish-gold-oracle
chmod +x ops/*.sh
sudo bash ops/install_systemd.sh
```

## Start / Stop

Start everything:

```bash
sudo systemctl start mirofish-backend.service
sudo systemctl start xauex.service
sudo systemctl start xauex-start.timer
sudo systemctl start xauex-stop.timer
sudo systemctl start xauex-trade-journal.timer
sudo systemctl start xauex-weekly-review.timer
sudo systemctl start oracle-dashboard.service
sudo systemctl start mirofish-bridge.timer
sudo systemctl start mirofish-bridge.service
```

Stop everything:

```bash
sudo systemctl stop mirofish-bridge.timer
sudo systemctl stop xauex-start.timer
sudo systemctl stop xauex-stop.timer
sudo systemctl stop xauex-trade-journal.timer
sudo systemctl stop xauex-weekly-review.timer
sudo systemctl stop oracle-dashboard.service
sudo systemctl stop xauex.service
sudo systemctl stop mirofish-backend.service
```

Restart after code or config changes:

```bash
sudo systemctl restart mirofish-backend.service
sudo systemctl restart xauex.service
sudo systemctl restart xauex-start.timer
sudo systemctl restart xauex-stop.timer
sudo systemctl restart xauex-trade-journal.timer
sudo systemctl restart xauex-weekly-review.timer
sudo systemctl restart oracle-dashboard.service
sudo systemctl start mirofish-bridge.service
```

## Status

```bash
systemctl status mirofish-backend.service --no-pager
systemctl status xauex.service --no-pager
systemctl status oracle-dashboard.service --no-pager
systemctl status xauex-start.timer xauex-stop.timer xauex-trade-journal.timer xauex-weekly-review.timer --no-pager
systemctl status mirofish-bridge.timer mirofish-bridge.service --no-pager
bash /home/bolyki/mirofish-gold-oracle/status.sh
```

## Logs

```bash
journalctl -u mirofish-backend.service -f
journalctl -u oracle-dashboard.service -f
journalctl -u xauex.service -f
journalctl -u mirofish-bridge.service -f
journalctl -u xauex-trade-journal.service -f
journalctl -u xauex-weekly-review.service -f
tail -f /var/log/xauex/xauex.log
tail -f /home/bolyki/mirofish-gold-oracle/logs/bridge-run.log
tail -f /home/bolyki/mirofish-gold-oracle/logs/oracle-dashboard.log
```

Report outputs:

- `/var/lib/xauex/trade_journal.json`
- `/var/lib/xauex/weekly_review.json`
- `/var/lib/xauex/weekly_review.md`
- `/var/lib/xauex/latest_signal_brief.md`
- `/var/lib/xauex/latest_signal_evidence.json`

Dashboard:

- local URL: `http://127.0.0.1:8089`
- LAN URL: `http://10.8.0.1:8089`

Retention policy:

- app logs are rotated daily and kept for 14 days
- system journal is capped at 256 MB with 14-day retention
- app log files are budget-limited to about 1.75 GiB, keeping total logging under about 2 GiB combined

## Trading Mode

This machine is intended to keep trading on demo. Confirm these values in `xauex/.env`:

```dotenv
OBSERVE_ONLY=false
MIROFISH_MODE=true
RISK_PERCENT=1.5
MIROFISH_ENTRY_START_LONDON=08:00
MIROFISH_ENTRY_END_LONDON=08:05
MIROFISH_FORCE_FLAT_LONDON=11:30
MIROFISH_MAX_TRADES_PER_DAY=1
MIROFISH_CASH_TAKE_PROFIT_GBP=50
MIROFISH_CASH_STOP_LOSS_GBP=50
CMD_FILE_PATH=/var/lib/xauex/cmd.json
LOG_FILE_PATH=/var/log/xauex/xauex.log
```

## Manual Signal Refresh

```bash
cd /home/bolyki/mirofish-gold-oracle
./.venv/bin/python -m bridge.run --asset XAUUSD --auto-context
```

Expected live behavior:

- phase 1: one trade max per London weekday
- `HOLD` is allowed, but should be rare and reserved for hard blockers or strong conflict
- optional local Qdrant memory can be enabled with `QDRANT_ENABLED=1` and `QDRANT_PATH=/var/lib/xauex/qdrant_local`
- the current local Qdrant implementation is embedded via `qdrant-client`; there is no separate daemon or port to manage
- dashboard manual trades require absolute-price SL/TP and are intentionally independent from Oracle automation

## Notes

- The top-level `main.py` is still useful for interactive/manual runs, but systemd should use the scripts in `ops/`.
- The bridge runs non-interactively as a module because that import path is reliable; direct `python bridge/run.py` is not.
