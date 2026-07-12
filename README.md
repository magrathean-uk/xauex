# XAUEX

XAUEX is a repo-packaged XAUUSD signal and cTrader demo-execution runtime. Scheduled workers build and confirm XAUUSD decisions, the bot applies session and risk gates, and a loopback dashboard exposes runtime state, evidence, paper/demo positions, journals, and operator controls.

Repository state does not prove any server is deployed or healthy. Verify the target host with [ops/RUNBOOK.md](./ops/RUNBOOK.md).

## Runtime flow

```text
window signal timer -> XAUUSD context/evidence -> /var/lib/xauex/cmd.json
window confirm timer -> confirmation fields in the command bundle
xauex.service -> cTrader demo quote/execution loop + risk/session gates
state and review artifacts -> dashboard on 127.0.0.1:8089
VPN-only Caddy -> dashboard
```

The active windows are defined in `xauex/live_windows.py`:

- London morning: signal `07:55`, confirm `07:59`, entry `08:00-08:10 Europe/London`.
- London midday: signal `11:25`, confirm `11:29`, entry `11:30-11:40 Europe/London`.
- US macro/open: signal `08:40`, confirm `08:44`, entry `08:45-08:55 America/New_York`.

`cmd.json` preserves the operator `kill_switch`; the runtime polls it and halts new entry while active. Signal writers must preserve the existing value.

## Repository map

- `xauex/main.py`, `xauex/bot/`: demo broker runtime, execution, filters, and risk.
- `xauex/signal/`: sources, prediction, confirmation, evidence, and memory.
- `xauex/app/`: dashboard and JSON API.
- `xauex/analyst/`: decision ledger, journal, replay, and weekly review.
- `ops/`: service, timer, monitoring, ingress, and installation definitions.
- `tests/`, `xauex/tests/`: regression coverage.

## Repo-defined services

- `xauex.service`: cTrader demo runtime.
- `xauex-web.service`: loopback dashboard.
- `xauex-window-signal@*.timer`, `xauex-window-confirm@*.timer`: scheduled windows.
- `xauex-start.timer`, `xauex-stop.timer`: weekday start and Friday stop boundaries.
- `xauex-shadow-compare.timer`, `xauex-shadow-evaluate.timer`: shadow trials.
- `xauex-trade-journal.timer`, `xauex-decision-ledger.timer`, `xauex-weekly-review.timer`: review artifacts.
- `dsa-sidecar.service`: optional, installed disabled.

## Runtime files

```text
/var/lib/xauex/cmd.json
/var/lib/xauex/state.json
/var/lib/xauex/risk_state.json
/var/lib/xauex/latest_signal_brief.md
/var/lib/xauex/latest_signal_evidence.json
/var/lib/xauex/signal_runs/
/var/lib/xauex/shadow_trials/
/var/lib/xauex/trade_journal.json
/var/lib/xauex/weekly_review.json
/var/lib/xauex/weekly_review.md
/var/log/xauex/xauex.log
```

## Local verification

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m pytest
```

Signal-only check:

```bash
python3 -m xauex.signal.run --asset XAUUSD --auto-context
```

## Maintained docs

- [Fresh-host rebuild](./docs/REBUILD.md)
- [Operations](./ops/RUNBOOK.md)
- [Optional DSA research sidecar](./docs/DSA_SIDECAR.md)
