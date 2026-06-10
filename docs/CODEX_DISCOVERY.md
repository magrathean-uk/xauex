# XAUEX Codex Discovery

This is the fastest repo map for coding agents and operators.

## Current Runtime

XAUEX is a single XAUUSD demo-trading runtime with five active surfaces:

- `xauex/signal/`: builds market context, generates signals, writes evidence, and updates `/var/lib/xauex/cmd.json`.
- `xauex/main.py` plus `xauex/bot/`: cTrader execution loop, risk gates, session management, and `/var/lib/xauex/state.json`.
- `xauex/app/`: Flask dashboard and JSON API on `127.0.0.1:8089`.
- `xauex/analyst/`: post-session trade journal and weekly review jobs.
- `ops/monitoring/`: Monit checks for morning summary and trade-open alerts.

The old split products are gone. There is no supported `backend/`, `bridge/`, `frontend/`, or `dashboard_web/` runtime in this checkout.

## Read This First

For most tasks, read files in this order:

1. `README.md`
2. `ops/RUNBOOK.md`
3. `xauex/main.py`
4. `xauex/app/app.py`
5. `xauex/signal/run.py`
6. `ops/install_systemd.sh`

That sequence gives you the architecture, host model, bot entrypoint, dashboard/API surface, signal entrypoint, and installed-service source of truth.

## Directory Map

- `xauex/main.py`
  Live orchestrator. Owns signal consumption, slot logic, broker lifecycle, session management, state transitions, and trade ownership.
- `xauex/config.py`
  Runtime config loader for bot and service wrappers.
- `xauex/live_windows.py`
  Morning, midday, and US-open signal/confirm/entry schedules.
- `xauex/app/`
  Flask dashboard/API, manual controls, health views, report/brief pages, and templates.
- `xauex/signal/`
  Signal-generation pipeline, source ingestion, parser behavior, confirmation, command writing, shadow compare/evaluate helpers.
- `xauex/bot/`
  cTrader API client, execution, risk, filters, patterns, levels, and state writing.
- `xauex/shared/`
  Diagnostics, manual-command helpers, event journal, and terminal/dashboard helpers.
- `xauex/analyst/`
  Trade journal and weekly review jobs.
- `ops/`
  Systemd units, wrappers, install script, Monit checks, Caddy snippet, log hygiene, and live runbook.
- `tests/`
  Cross-surface tests for dashboard, diagnostics, signal writing, and monitoring helpers.
- `xauex/tests/`
  Bot-centric tests and runtime logic tests.
- `docs/`
  Rebuild, discovery, export, and host documentation.

## Compatibility Shims

These files keep legacy absolute imports working:

- `config.py`
- `auth.py`
- `bot/__init__.py`

Do not build new features around those paths. Prefer `xauex.*` imports.

## Runtime State Outside Git

Most live debugging requires files outside the repo:

- `/var/lib/xauex/cmd.json`
  Latest command bundle consumed by the bot.
- `/var/lib/xauex/state.json`
  Dashboard/runtime snapshot, quote state, risk counters, slot usage, positions, closed trades.
- `/var/lib/xauex/risk_state.json`
  Risk and trade-limit persistence.
- `/var/lib/xauex/latest_signal_brief.md`
  Latest operator brief.
- `/var/lib/xauex/latest_signal_evidence.json`
  Latest signal evidence payload.
- `/var/lib/xauex/signal_runs/`
  Archived signal runs.
- `/var/lib/xauex/shadow_trials/`
  Shadow compare/evaluate artifacts.
- `/var/lib/xauex/trade_journal.json`
  Closed-trade journal.
- `/var/log/xauex/xauex.log`
  Bot runtime log.

When behavior looks wrong in code but tests pass, check these files before guessing.

## Service Model

The active installed-service model is:

- `xauex-web.service`
- `xauex.service`
- `xauex-start.timer`
- `xauex-stop.service`
- `xauex-stop.timer`
- `xauex-window-signal@morning.timer`
- `xauex-window-signal@midday.timer`
- `xauex-window-signal@us_open.timer`
- `xauex-window-confirm@morning.timer`
- `xauex-window-confirm@midday.timer`
- `xauex-window-confirm@us_open.timer`
- `xauex-shadow-compare.timer`
- `xauex-shadow-evaluate.timer`
- `xauex-trade-journal.timer`
- `xauex-weekly-review.timer`

Monit alert checks installed from this repo:

- `xauex-morning-summary`
- `xauex-trade-alerts`
- `xauex-signal-stall`

Retired and intentionally not installed:

- `/etc/cron.d/xauex-daily-report`
- `xauex-shadow-report.service`
- `xauex-shadow-report.timer`

The install path is `ops/install_systemd.sh`. That script is the source of truth for host installation and retired-unit cleanup.

## Where To Edit Common Tasks

- Dashboard/API response wrong:
  Edit `xauex/app/app.py` and templates under `xauex/app/templates/`.
- Signal content, confidence, SL/TP distances, or evidence wrong:
  Edit `xauex/signal/signal_parser.py`, `xauex/signal/direct_predictor.py`, `xauex/signal/run.py`, and `xauex/signal/assets.py`.
- Confirm-pass behavior wrong:
  Edit `xauex/signal/confirm.py` and the signal writer/parser tests.
- Bot not placing or managing trades correctly:
  Start in `xauex/main.py`, then `xauex/bot/execution/executor.py`, `xauex/bot/api/client.py`, and `xauex/bot/risk/`.
- State file or dashboard diagnostics inconsistent:
  Check `xauex/bot/state/writer.py`, `xauex/shared/diagnostics.py`, and `status.sh`.
- Service install or host ingress issue:
  Check `ops/install_systemd.sh`, `ops/check_host_layout.sh`, `ops/xauex-dashboard.caddy`, and the matching unit/wrapper.
- Alert email behavior wrong:
  Check `ops/monitoring/45-xauex-notify.monit`, `ops/monitoring/check_xauex_morning_summary.py`, `ops/monitoring/check_xauex_trade_alerts.py`, and `ops/monitoring/check_xauex_signal_stall.py`.

## Test Map

- `tests/bridge/`
  Still named after the old package, but these tests exercise `xauex.signal`.
- `tests/dashboard/`
  Dashboard/API behavior for the live Flask app.
- `tests/test_xauex_trade_alerts.py`
  Monit trade-open alert behavior.
- `tests/test_xauex_signal_stall_alerts.py`
  Monit repeated source-blocked HOLD and degraded source fallback alert behavior.
- `tests/test_xauex_runtime_monitor.py`
  Runtime-monitor check behavior.
- `tests/test_terminal_dashboards.py`
  Shared terminal/dashboard surfaces.
- `xauex/tests/test_session_manager.py`
  Session phase logic, config loading, and stop-distance helpers.
- `xauex/tests/test_manual_trade_commands.py`
  Manual command flow.
- `xauex/tests/test_xauex_signal_policy.py`
  Signal-policy behavior and execution gating.
- `xauex/tests/test_xauex_windows.py`
  Live-window schedule expectations.
- `xauex/tests/test_trailing_integration.py`
  Stop-amend and trailing-stop integration behavior.

## Fast Commands

```bash
bash status.sh
curl -fsS http://127.0.0.1:8089/api/dashboard
curl -fsS http://127.0.0.1:8051/health
monit summary
systemctl --failed --no-pager
./.venv/bin/python -m xauex.signal.run --asset XAUUSD --auto-context
python3 -m pytest tests/dashboard/test_manual_controls.py tests/bridge/test_signal_writer.py -q
python3 -m pytest xauex/tests/test_session_manager.py xauex/tests/test_xauex_signal_policy.py xauex/tests/test_trailing_integration.py -q
```

## Known Traps

- `xauex/main.py` is large and mixes orchestration concerns. Search before editing.
- `tests/bridge/` is still the signal test area even though `bridge/` is gone.
- `docs/superpowers/` contains historical design material, not current runtime truth.
- Host behavior can differ from repo code if `/etc/systemd/system/` or `/usr/local/bin/` was not reinstalled after edits.
- Runtime truth often lives in `/var/lib/xauex/state.json`, not only in logs.
- The dashboard API can report `SIGNAL_STALE` between windows even when the app and bot are healthy.
- Retired report files should not reappear. If they do, check `ops/install_systemd.sh` and the host `/etc/systemd/system/` state.

## Recommended Workflow

1. Read `README.md`, this file, and `ops/RUNBOOK.md`.
2. Identify the active surface: `app`, `signal`, `bot`, `analyst`, `monitoring`, or `ops`.
3. Check matching tests before editing.
4. If behavior is runtime-specific, inspect `/var/lib/xauex/*` and `/var/log/xauex/xauex.log`.
5. Prefer targeted pytest subsets and cheap HTTP/systemd checks before broad runs.
