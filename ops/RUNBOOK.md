# XAUEX Operations

This runbook describes checked-in behavior and commands for verifying a deployment. It does not claim the target host currently matches the repository.

## Service model

- `xauex.service`: cTrader demo bot, XAUUSD quote stream, command polling, risk, and health endpoint on `127.0.0.1:8051`.
- `xauex-web.service`: waitress dashboard on `127.0.0.1:8089`.
- `xauex-window-signal@*.timer`, `xauex-window-confirm@*.timer`: generated from `xauex/live_windows.py`.
- `xauex-start.timer`: weekday `07:25 Europe/London` start service.
- `xauex-stop.timer`: Friday `15:06 Europe/London` stop service.
- `xauex-shadow-compare.timer`, `xauex-shadow-evaluate.timer`: shadow trials.
- `xauex-trade-journal.timer`: `15:45`, `16:30`, and `19:00 Europe/London` retries.
- `xauex-decision-ledger.timer`: `15:10 Europe/London`.
- `xauex-weekly-review.timer`: Friday `19:15 Europe/London`.
- `dsa-sidecar.service`: optional and installed disabled.

The repo Caddy snippet redirects VPN HTTP to internal-CA HTTPS and proxies only to `127.0.0.1:8089`. It defines no public listener.

## Install or refresh

From the deployed checkout:

```bash
sudo bash ops/install_systemd.sh
```

The installer can restart services and start the bot during its configured weekday schedule. Check open positions and session state before running it on an active demo account.

## Status

```bash
systemctl --failed --no-pager
systemctl status xauex.service xauex-web.service --no-pager
systemctl status xauex-window-signal@morning.timer xauex-window-signal@midday.timer xauex-window-signal@us_open.timer --no-pager
systemctl status xauex-window-confirm@morning.timer xauex-window-confirm@midday.timer xauex-window-confirm@us_open.timer --no-pager
systemctl status xauex-start.timer xauex-stop.timer xauex-trade-journal.timer xauex-decision-ledger.timer xauex-weekly-review.timer --no-pager
systemctl status xauex-shadow-compare.timer xauex-shadow-evaluate.timer --no-pager
systemctl is-enabled dsa-sidecar.service
curl -fsS http://127.0.0.1:8051/health
curl -fsS http://127.0.0.1:8089/api/dashboard
monit summary
```

## Kill switch and stop

The operator kill switch is the top-level `kill_switch` value in `/var/lib/xauex/cmd.json`. Signal writers preserve it. Confirm runtime state after changing it:

```bash
jq '{kill_switch, xauex_signal}' /var/lib/xauex/cmd.json
jq '.runtime.kill_switch_active // .kill_switch_active' /var/lib/xauex/state.json
```

For a hard operational stop:

```bash
sudo systemctl stop xauex.service
```

Stopping the service can interrupt active demo-position management. Inspect the broker and state first when possible.

## Manual signal and confirm

```bash
./.venv/bin/python -m xauex.signal.run --asset XAUUSD --auto-context
./.venv/bin/python -m xauex.signal.confirm --window-label morning
```

Use `midday` or `us_open` for the other current windows.

## Source-truth files

```bash
jq . /var/lib/xauex/cmd.json
jq . /var/lib/xauex/state.json
jq . /var/lib/xauex/risk_state.json
jq . /var/lib/xauex/latest_signal_evidence.json
jq . /var/lib/xauex/decision_ledger.json
```

## Decision invariants

- Slow-publishing USD-index and WTI reference rows do not make an otherwise current daily snapshot stale.
- Daily-publishing macro rows older than two business days remain a hard parser block.
- Every terminal `HARD_BLOCKER` event carries `block_factors` and a `primary_block_factor`.
- `PATTERN_DIRECTION_MISMATCH` must name a real opposing pattern; `pattern=NONE` is an invariant fault.
- Monit emails immediately on unexplained or impossible hard-block evidence, and when pattern evidence suppresses at least four of six windows across two consecutive London trading days.

Inspect the latest terminal decisions:

```bash
tail -n 200 /var/lib/xauex/events.jsonl \
  | jq -c 'select(.event_type == "risk_result" and .payload.terminal == true)
    | {timestamp_utc, slot: .payload.slot, reason: .payload.reason,
       block_factors: .payload.block_factors, pattern: .payload.pattern_evidence}'
sudo monit status xauex-signal-stall
```

## Logs

```bash
journalctl -u xauex.service -f
journalctl -u xauex-web.service -f
journalctl -u xauex-window-signal@morning.service -u xauex-window-signal@midday.service -u xauex-window-signal@us_open.service -f
journalctl -u xauex-window-confirm@morning.service -u xauex-window-confirm@midday.service -u xauex-window-confirm@us_open.service -f
tail -f /var/log/xauex/xauex.log
tail -f logs/xauex-web.log
```

## Optional DSA sidecar

Keep it disabled unless intentionally configured:

```bash
systemctl is-enabled dsa-sidecar.service
systemctl is-active dsa-sidecar.service
```

See `../docs/DSA_SIDECAR.md`. Its output is advisory shadow evidence only and must not enter the XAUUSD command/execution path.

## Code verification

```bash
python3 -m pytest xauex/tests/test_xauex_windows.py xauex/tests/test_session_manager.py xauex/tests/test_manual_trade_commands.py -q
python3 -m pytest tests/bridge/test_signal_writer.py tests/bridge/test_signal_parser_validator.py tests/dashboard/test_manual_controls.py -q
python3 -m pytest
```
