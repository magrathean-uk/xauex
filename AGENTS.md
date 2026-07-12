# XAUEX Agent Guide

Read [README.md](./README.md), [ops/RUNBOOK.md](./ops/RUNBOOK.md), [docs/REBUILD.md](./docs/REBUILD.md), and [docs/DSA_SIDECAR.md](./docs/DSA_SIDECAR.md) when the optional sidecar is relevant.

## Safety boundary

- XAUEX is the XAUUSD cTrader demo runtime. Do not describe it as real-money production trading.
- `xauex/live_windows.py` is the schedule source of truth.
- Preserve confirmation, freshness, spread, risk, session, symbol, replay, and `cmd.json` kill-switch gates.
- DSA is optional, localhost-only, disabled by default, and shadow-only. It cannot write XAUEX commands or enter the execution path.
- Never commit `.env`, `xauex/.env`, broker credentials/tokens, logs, runtime state, caches, or virtualenvs.
- Preserve unrelated dirty-worktree changes.

## Repository map

- `xauex/main.py`, `xauex/bot/`: cTrader demo execution and position management.
- `xauex/signal/`: XAUUSD context, prediction, confirmation, evidence, source quality, and DSA shadow adapter.
- `xauex/app/`: loopback dashboard and operator API.
- `xauex/analyst/`: decision ledger, journal, replay, and weekly review.
- `xauex/shared/`: diagnostics, safe I/O, event journal, and manual-command contracts.
- `ops/`: systemd units, wrappers, monitoring, Caddy, DSA service, and host checks.
- `tests/`, `xauex/tests/`: signal, dashboard, broker, schedule, and runtime coverage.

Root `config.py`, `auth.py`, and `bot/__init__.py` are compatibility shims. New imports use `xauex.*`.

## Commands

```bash
python3 -m pytest
make -C xauex test
make -C xauex build
python3 -m xauex.signal.run --asset XAUUSD --auto-context
```

Focused runtime lanes:

```bash
python3 -m pytest xauex/tests/test_xauex_windows.py xauex/tests/test_session_manager.py xauex/tests/test_manual_trade_commands.py -q
python3 -m pytest tests/bridge/test_signal_writer.py tests/bridge/test_signal_parser_validator.py tests/dashboard/test_manual_controls.py -q
```

## Done when

- Narrow tests pass before the full suite, or the exact blocker is recorded.
- Runtime, unit, wrapper, monitoring, and docs names remain aligned.
- The XAUUSD schedule and gate behavior come from current source, not historical notes.
- Deployed-host state is checked on the host and never inferred from this checkout.
