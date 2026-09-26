# XAUEX agent guidance

## Boundaries that matter

- XAUEX is an XAUUSD signal and cTrader demo-execution runtime. Do not describe this checkout as a verified deployment or as ready for real-money trading.
- Preserve confirmation, freshness, spread, risk, session, symbol and replay checks, plus the operator `kill_switch` in `cmd.json`. Signal writers must retain that switch.
- Use `xauex/live_windows.py` for trading windows. Keep generated timers, monitoring and documentation aligned when schedules change.
- Keep the optional DSA adapter disabled by default, loopback-only and shadow-only. Its evidence must not enter commands or execution.
- Use `xauex.*` imports. Root `auth.py`, `config.py`, `diagnostics.py`, `tui_diagnostics.py` and `bot/` are compatibility entry points.
- Preserve unrelated work. Do not include environment files, tokens, broker account data, logs, runtime state or generated artifacts in changes.

## Work and verification

Use Python 3.11+ and the development dependencies described in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md). From the repository root:

```bash
python3 -m pytest
make -C xauex lint
```

Start with tests for the changed behavior. Signal and dashboard changes have coverage in `tests/bridge/`, `tests/dashboard/` and `tests/security/`; execution and risk changes have coverage in `xauex/tests/`. The root pytest command covers both trees. `make -C xauex test` covers only `xauex/tests/`.

Finish with the relevant tests and the full suite for code changes. Record exact blockers and distinguish a checked command from an executed, passing check. For documentation-only changes, check paths, links, command definitions and consistency without starting services or running the signal pipeline.

The signal runner can call providers and write runtime artifacts. The installer changes host configuration and can start or restart the bot. These are operational actions, not local test commands. Use [ops/RUNBOOK.md](ops/RUNBOOK.md) for host work and [docs/DSA_SIDECAR.md](docs/DSA_SIDECAR.md) for sidecar changes.

## Keep guidance current

Use [CONTRIBUTING.md](CONTRIBUTING.md) for contribution checks and [SECURITY.md](SECURITY.md) for reporting and trust boundaries. Preserve the legal texts and attribution listed in [LEGAL.md](LEGAL.md).

Dated files under `docs/superpowers/` record earlier designs and plans. They are historical context, not current deployment authority or evidence that their acceptance checks passed. Prefer current code and maintained guides when those notes differ.
