# XAUEX Codex Discovery

This document is the fastest way to orient a coding agent in the current repo.

## What This Repo Is

XAUEX is a single live XAUUSD demo-trading runtime with four active surfaces:

- `xauex/signal/`: builds market context, produces the directional signal, and writes `/var/lib/xauex/cmd.json`
- `xauex/main.py` plus `xauex/bot/`: runs the cTrader bot, reads the command file, places/manages trades, and writes `/var/lib/xauex/state.json`
- `xauex/app/`: serves the operator dashboard and JSON API on port `8089`
- `xauex/analyst/`: post-session reporting jobs used by timers

The old split products are gone. There is no supported `backend/`, `bridge/`, `frontend/`, or `dashboard_web/` runtime left in this checkout.

## Read This First

For most tasks, read files in this order:

1. `README.md`
2. `ops/RUNBOOK.md`
3. `xauex/main.py`
4. `xauex/app/app.py`
5. `xauex/signal/run.py`

That sequence gets you the live architecture, host model, bot entrypoint, dashboard/API surface, and signal entrypoint.

## Directory Map

- `xauex/main.py`
  The live orchestrator. This is the highest-leverage file in the repo and the biggest one. It owns signal consumption, slot logic, broker lifecycle, session management, and most runtime state transitions.
- `xauex/config.py`
  Shared runtime config loader for the bot and service wrappers.
- `xauex/app/`
  Flask dashboard and API. Start here for UI payloads, health views, manual trade endpoints, and report pages.
- `xauex/signal/`
  Signal-generation pipeline. Start here for signal rules, source ingestion, parser behavior, and command writing.
- `xauex/bot/`
  Lower-level trading components: cTrader API client, execution, risk, filters, patterns, levels, state writing.
- `xauex/shared/`
  Diagnostics and terminal/dashboard helpers shared by operator surfaces.
- `xauex/analyst/`
  Trade journal, weekly review, and morning brief jobs.
- `ops/`
  Systemd units, wrapper scripts, install script, cron entry, and operational runbook.
  Host-ingress helpers also live here now: `check_host_layout.sh`, `Caddyfile.root.example`, and `pihole-compose.override.example.yml`.
- `tests/`
  Cross-surface tests for the dashboard, diagnostics, and signal pipeline.
- `xauex/tests/`
  Bot-centric tests and integration coverage for the XAUEX runtime.

## Compatibility Shims

These files exist only to keep legacy absolute imports working:

- `config.py`
- `auth.py`
- `bot/__init__.py`

Do not build new features around those paths. Prefer `xauex.*` imports in new code.

## Runtime State Outside Git

Most live debugging requires files outside the repo:

- `/var/lib/xauex/cmd.json`
  Latest generated signal bundle consumed by the bot.
- `/var/lib/xauex/state.json`
  Runtime snapshot, risk counters, slot usage, closed trades, quote state.
- `/var/lib/xauex/latest_signal_brief.md`
- `/var/lib/xauex/latest_signal_evidence.json`
- `/var/lib/xauex/trade_journal.json`
- `/var/log/xauex/xauex.log`

When behavior looks wrong in code but not in tests, check those files before guessing.

## Service Model

The host uses:

- `xauex-web.service`
- `xauex-signal.service`
- `xauex-signal.timer`
- `xauex.service`
- `xauex-start.timer`
- `xauex-stop.service`
- `xauex-stop.timer`
- `xauex-trade-journal.timer`
- `xauex-weekly-review.timer`
- `/etc/cron.d/xauex-daily-report`

The install path is `ops/install_systemd.sh`. That script is the source of truth for what gets installed on the host.

## Where To Edit Common Tasks

- Dashboard/API response wrong:
  Edit `xauex/app/app.py` and the templates under `xauex/app/templates/`.
- Signal content, confidence, SL/TP distances, or evidence wrong:
  Edit `xauex/signal/signal_parser.py`, `xauex/signal/direct_predictor.py`, `xauex/signal/run.py`, and `xauex/signal/assets.py`.
- Bot not placing or managing trades correctly:
  Start in `xauex/main.py`, then `xauex/bot/execution/executor.py`, `xauex/bot/api/client.py`, and `xauex/bot/risk/`.
- State file or dashboard diagnostics inconsistent:
  Check `xauex/bot/state/writer.py`, `xauex/shared/diagnostics.py`, and `status.sh`.
- Systemd or host install issue:
  Check `ops/install_systemd.sh`, `ops/check_host_layout.sh`, and the relevant unit or wrapper in `ops/`.

## Test Map

The test names are partly modernized and partly legacy.

- `tests/bridge/`
  Still named after the old package, but these tests now exercise `xauex.signal`.
- `tests/dashboard/`
  Dashboard/API behavior for the live Flask app.
- `tests/test_diagnostics.py`, `tests/test_terminal_dashboards.py`, `tests/test_tui_diagnostics.py`
  Shared diagnostics surfaces.
- `xauex/tests/test_session_manager.py`
  Session phase logic, config loading, and stop-distance helpers.
- `xauex/tests/test_manual_trade_commands.py`
  Manual command flow.
- `xauex/tests/test_xauex_signal_policy.py`
  Signal-policy behavior and execution gating.
- `xauex/tests/test_xauex_windows.py`
  Service timing and schedule expectations.
- `xauex/tests/test_trailing_integration.py`
  Stop-amend and trailing-stop integration behavior.

## Fast Commands

- Repo health snapshot:
  `bash status.sh`
- Live dashboard payload:
  `curl -fsS http://127.0.0.1:8089/api/dashboard`
- Bot health:
  `curl -fsS http://127.0.0.1:8051/health`
- Manual signal refresh:
  `./.venv/bin/python -m xauex.signal.run --asset XAUUSD --auto-context`
- Targeted dashboard tests:
  `python3 -m pytest tests/dashboard/test_manual_controls.py tests/dashboard/test_direct_report.py -q`
- Targeted bot/session tests:
  `python3 -m pytest xauex/tests/test_session_manager.py xauex/tests/test_xauex_signal_policy.py xauex/tests/test_trailing_integration.py -q`

## Known Traps

- `xauex/main.py` is large and mixes orchestration concerns. Search before editing; do not assume a single helper owns a behavior.
- `tests/bridge/` is still the signal test area even though `bridge/` is gone.
- Several old docs under `docs/superpowers/` are historical design material, not current runtime truth.
- Host behavior can differ from repo code if `/etc/systemd/system/` or `/usr/local/bin/` was not reinstalled after edits. Re-run `sudo bash ops/install_systemd.sh` when changing units or wrappers.
- Runtime truth often lives in `/var/lib/xauex/state.json`, not only in logs.

## Recommended Workflow For Codex

1. Read `README.md`, this file, and `ops/RUNBOOK.md`.
2. Identify the active surface: `app`, `signal`, `bot`, `analyst`, or `ops`.
3. Check the matching tests before editing.
4. If behavior is live/runtime-specific, inspect `/var/lib/xauex/*` and `/var/log/xauex/xauex.log`.
5. Prefer targeted pytest subsets and cheap HTTP or dry-run checks over broad runs.
