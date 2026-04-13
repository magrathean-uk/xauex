# Repository Guidelines

## Project Structure & Module Organization
- `xauex/main.py` and `xauex/bot/` are the live cTrader execution runtime.
- `xauex/app/` contains the Flask dashboard and operator API.
- `xauex/signal/` contains context building, signal generation, evidence writing, and command-file output.
- `xauex/analyst/` contains the journal, weekly review, and morning brief jobs.
- `xauex/shared/` contains shared diagnostics helpers.
- `tests/` and `xauex/tests/` hold pytest suites. `tests/bridge/` is still the signal test area even though the old `bridge/` package is gone.
- `ops/` contains systemd units, cron wiring, deployment scripts, and the live runbook.
- `docs/` stores rebuild, discovery, and operational documentation.
- `config.py`, `auth.py`, and `bot/__init__.py` at repo root are compatibility shims for legacy absolute imports. Prefer `xauex.*` imports in new code.

## Build, Test, and Development Commands
- `python3 -m pytest` from the repo root runs the main suite.
- `make -C xauex test` runs the XAUEX-specific pytest subset.
- `make -C xauex build` compiles the Rust `tick_parser` extension; `make -C xauex install` installs the wheel into the active virtualenv.
- `python3 -m xauex.signal.run --asset XAUUSD --auto-context` runs the signal pipeline manually.
- `python3 -m xauex.main` starts the bot directly for local debugging, but the supported host path is via systemd in `ops/`.
- `bash status.sh` prints the live host snapshot if the local services are running.

## Coding Style & Naming Conventions
- Python code uses 4-space indentation, `snake_case` for functions and modules, and `PascalCase` for classes.
- Keep functions focused and prefer explicit, typed helpers over hidden side effects.
- There is no repo-wide formatter config; use the existing XAUEX checks in `xauex/Makefile` (`ruff check`, `mypy`) when editing that area.

## Testing Guidelines
- Pytest is the standard runner; async tests use `@pytest.mark.asyncio`.
- Name tests `test_*.py` and keep fixtures close to the behavior they support.
- Add targeted tests for new behavior first, then run the narrowest relevant subset before a full suite pass.
- Prefer the live-surface subsets first:
  - `tests/dashboard/` for dashboard/API
  - `tests/bridge/` for `xauex.signal`
  - `xauex/tests/` for bot/runtime logic

## Commit & Pull Request Guidelines
- Recent commits are short, imperative, and prefix-free, for example `Add diagnostics panels to terminal dashboards`.
- PRs should explain the change, list the verification commands you ran, and link any related issue or design note.
- Call out any config, state-file, or ops impact explicitly.

## Security & Configuration Tips
- Do not commit `.env`, `xauex/.env`, virtualenvs, logs, or generated frontend builds.
- Keep runtime state under the paths documented in `README.md` and `docs/REBUILD.md`.
- Treat broker credentials, API keys, and local state files as machine-specific.
- For live-behavior debugging, check `/var/lib/xauex/state.json`, `/var/lib/xauex/cmd.json`, and `/var/log/xauex/xauex.log` before assuming the code path is wrong.
