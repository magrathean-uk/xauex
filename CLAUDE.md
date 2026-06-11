# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Quick Start

- **Python**: 3.11+ required
- **Set up**: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
- **Run tests**: `python3 -m pytest` or `make -C xauex test`
- **Run specific test**: `python3 -m pytest tests/path/to/test_file.py::test_name -v`
- **Lint**: `make -C xauex lint` (ruff + mypy)
- **Rust extension**: `make -C xauex build && make -C xauex install` (requires maturin). The extension is `xauex/tick_parser/` (PyO3), which parses Dukascopy CSV ticks into H1 OHLC bars for backtesting.
- **Other Make targets** (in `xauex/Makefile`): `test-fast` (stop on first failure), `backtest`/`download` (require `DATE_FROM`/`DATE_TO`), `auth` (cTrader OAuth2 flow), `dashboard` (TUI), `logs` (tail bot log), `clean`.

## Architecture Overview

XAUEX is a single live XAUUSD demo trading system with four active surfaces:

1. **Signal Pipeline** (`xauex/signal/`): Fetches market context, runs the direct predictor, generates trading signals, and writes command bundles to `/var/lib/xauex/cmd.json`.
   - Entry point: `xauex/signal/run.py --asset XAUUSD --auto-context`
   - Key files: `signal_parser.py`, `direct_predictor.py`, `assets.py`

2. **Bot/Execution** (`xauex/main.py` + `xauex/bot/`): Reads commands, places trades on cTrader, manages risk, and writes runtime state to `/var/lib/xauex/state.json`.
   - Entry point: `python3 -m xauex.main` (or via systemd)
   - Key files: `xauex/main.py` (orchestrator), `bot/` (cTrader API, execution, risk, patterns)

3. **Dashboard** (`xauex/app/`): Flask API and operator dashboard on port 8089.
   - Entry point: Flask app in `xauex/app/app.py`
   - Key files: `app/app.py`, `app/templates/`

4. **Analyst Jobs** (`xauex/analyst/`): Post-session trade journal, weekly review, and morning brief helpers.

5. **Monitoring** (`ops/monitoring/`): Monit checks for morning decision summaries and trade-open alerts. Scheduled daily report and weekly shadow report emails are retired.

## Key Files & Concepts

- **`xauex/main.py`**: Highest-leverage orchestrator. Owns signal consumption, slot logic, broker lifecycle, and session management.
- **`xauex/config.py`**, **`auth.py`**, **`bot/__init__.py`**: Legacy compatibility shims. Prefer `xauex.*` imports in new code.
- **`xauex/shared/`**: Diagnostics and terminal dashboard helpers.
- **Session Stages**: `OBSERVE` (initial observation), `PROTECT` (active protection), `TRAIL` (trailing stops).
- **Risk Model**: Cash risk stays capped; stops may widen when session manager deems it safe.

## Runtime State Outside Git

Live debugging uses files outside the repo:

- `/var/lib/xauex/cmd.json` — Latest signal command bundle
- `/var/lib/xauex/state.json` — Runtime snapshot, risk counters, slot usage, closed trades
- `/var/lib/xauex/latest_signal_evidence.json` — Signal evidence
- `/var/log/xauex/xauex.log` — Bot runtime log

When behavior looks wrong in code but tests pass, check these files first.

## Common Development Tasks

- **Dashboard/API response wrong**: Edit `xauex/app/app.py` and templates under `xauex/app/templates/`.
- **Signal confidence, SL/TP distances, or evidence wrong**: Edit `xauex/signal/signal_parser.py`, `xauex/signal/direct_predictor.py`, `xauex/signal/run.py`, and `xauex/signal/assets.py`.
- **Bot placement or trade management wrong**: Edit `xauex/main.py`, `xauex/bot/execution.py`, `xauex/bot/risk.py`, `xauex/bot/patterns.py`.
- **Add new feature to signal pipeline**: Start in `xauex/signal/run.py` and follow the context → prediction → command output flow.

## Testing

- **All tests**: `python3 -m pytest`
- **Test layout**: `pytest.ini` sets `testpaths = tests, xauex/tests`. Narrower subsets (before full suite):
  - Dashboard/API: `tests/dashboard/`
  - Signal pipeline: `tests/bridge/`
  - Runtime contracts: `tests/runtime/`; security: `tests/security/`
  - Bot/runtime unit tests: `xauex/tests/`
- **Run single test**: `python3 -m pytest tests/path/to/test.py::test_name -v`
- **Fast mode** (stop on first failure): `make -C xauex test-fast` or `pytest -x`
- **Async tests**: Use `@pytest.mark.asyncio`; `pytest.ini` sets `asyncio_mode = auto`
- **Fixtures**: Keep close to the behavior they support; prefer local fixtures in test files.

## Deployment & Operations

- **Systemd units**: See `ops/install_systemd.sh` (source of truth for what gets installed and what retired units are removed).
- **Key services**: `xauex-web.service`, `xauex.service`, `xauex-window-signal@*.timer`, `xauex-window-confirm@*.timer`, `xauex-shadow-compare.timer`, `xauex-shadow-evaluate.timer`, `xauex-trade-journal.timer`, `xauex-decision-ledger.timer`, `xauex-weekly-review.timer`.
- **Monitoring**: Monit owns alert emails via `ops/monitoring/45-xauex-notify.monit`.
- **Rebuild docs**: `docs/REBUILD.md` — fresh host setup.
- **Runbook**: `ops/RUNBOOK.md` — live host operations.
- **Retired reports**: No `/etc/cron.d/xauex-daily-report` and no `xauex-shadow-report.timer`.

## Code Style & Conventions

- Python: 4-space indentation, `snake_case` for functions/modules, `PascalCase` for classes.
- Keep functions focused; prefer explicit typed helpers over hidden side effects.
- Typing: `xauex/bot/` and `xauex/main.py` use mypy; check with `make -C xauex lint`.
- Comments: Only add when the WHY is non-obvious (hidden constraint, workaround, subtle invariant).

## Environment & Configuration

- `.env` and `xauex/.env`: Machine-specific (broker credentials, API keys); never commit.
- `.gitignore`: Ignores `.env`, virtualenvs, logs, caches, compiled files, and the old `NEW/` and `frontend/` directories.
- Required packages in `requirements.txt`: Flask, httpx, aiohttp, ctrader-open-api, qdrant-client (semantic memory), Textual (TUI), Rich (dashboards), openai (LLM in `direct_predictor.py`), pydantic, schedule, waitress.

## Useful References

- **Fast repo map**: `docs/CODEX_DISCOVERY.md`
- **README.md**: High-level overview of the trading system and deployment.
- **AGENTS.md**: Project structure, build commands, testing, security, and commit guidelines.
- **Makefile** (`xauex/Makefile`): All build, test, and deployment targets.

## Git Compatibility Shims

These files re-export for backward compatibility and should not be extended:

- `config.py` → `xauex/config.py`
- `auth.py` → `xauex/auth.py`
- `bot/__init__.py` (legacy)

New code should use `xauex.*` imports directly.
