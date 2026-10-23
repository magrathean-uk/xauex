# XAUEX project context

XAUEX is an XAUUSD signal and cTrader demo-execution runtime.

## Source map and runtime

- Execution includes confirmation, freshness, spread, risk, session, symbol and replay checks. The operator `kill_switch` is stored in `cmd.json`.
- `xauex/live_windows.py` defines trading windows used by timers, monitoring and documentation.
- The optional DSA adapter defaults to a disabled, loopback, shadow configuration. [docs/dsa-sidecar.md](docs/dsa-sidecar.md) describes the sidecar.
- `xauex.*` is the Python package namespace. Root `auth.py`, `config.py`, `diagnostics.py`, `tui_diagnostics.py` and `bot/` are compatibility entry points.
- The signal runner can call providers and write runtime artefacts. The installer changes host configuration and can start or restart the bot. [docs/development/runbook.md](docs/development/runbook.md) describes host operations.

## Development reference

Python 3.11+ and development dependencies are described in [docs/development.md](docs/development.md). Commands from the repository root:

```bash
python3 -m pytest
make -C xauex lint
```

Signal and dashboard coverage is in `tests/bridge/`, `tests/dashboard/` and `tests/security/`; execution and risk coverage is in `xauex/tests/`. The root pytest command covers both trees. `make -C xauex test` covers `xauex/tests/`.

Additional references are [CONTRIBUTING.md](.github/CONTRIBUTING.md), [SECURITY.md](.github/SECURITY.md) and [docs/legal/overview.md](docs/legal/overview.md). Dated files under `docs/development/archive/` record earlier designs and plans.
