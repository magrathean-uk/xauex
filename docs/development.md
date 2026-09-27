# Development

## Local environment

Run commands from the repository root with Python 3.11 or newer:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-dev.txt
```

`requirements-dev.txt` includes the runtime requirements and pytest, Ruff and mypy. Runtime requirements alone do not install the test tools. Keep local environment files and generated data out of Git. Use fixtures and temporary directories for tests; do not point checks at a running account's state.

Consider [Clean Development](https://github.com/magrathean-uk/clean-development) to manage development caches and supported build output.

## Validation

The root pytest configuration collects `tests/` and `xauex/tests/`:

```bash
python3 -m pytest
```

Choose a focused lane while working:

| Change | Command |
| --- | --- |
| Execution, sessions and manual trades | `python3 -m pytest xauex/tests/test_xauex_windows.py xauex/tests/test_session_manager.py xauex/tests/test_manual_trade_commands.py -q` |
| Signals, parsing and dashboard controls | `python3 -m pytest tests/bridge/test_signal_writer.py tests/bridge/test_signal_parser_validator.py tests/dashboard/test_manual_controls.py -q` |
| Risk state | `python3 -m pytest xauex/tests/test_risk_state_wiring.py tests/test_reconcile_xauex_risk_state.py -q` |
| File safety and replay | `python3 -m pytest tests/security xauex/tests/test_manual_command_replay_guard.py -q` |
| Optional sidecar | Follow [dsa-sidecar.md](dsa-sidecar.md). |

The Makefile lint target runs Ruff over `xauex` and `tests`, then mypy over its listed modules:

```bash
make -C xauex lint
```

Its default lint interpreter is the root `.venv/bin/python`. Override `LINT_PYTHON` if using another environment. `make -C xauex test` runs only `xauex/tests/`; it does not replace the root suite.

## Optional Rust parser

The Rust/PyO3 extension is in `xauex/tick_parser/`. Its build metadata requires Maturin `>=1.0,<2.0` and Python 3.11+. Rust/Cargo and Maturin are separate prerequisites, not installed by `requirements-dev.txt`.

```bash
make -C xauex build
```

This produces a wheel under `xauex/tick_parser/dist/`; `make -C xauex install` builds and installs it into the selected Python environment. Python backtesting code includes a fallback when the extension is unavailable. Building a wheel is not proof of broker or deployment behavior.

## Command boundaries

Use [rebuild.md](rebuild.md) for the supported host installation path. The Makefile's `deploy`, `setup-dirs` and `download` targets reference scripts missing from this checkout. Do not use those targets as installation or data-download instructions.

`python3 -m xauex.signal.run --asset XAUUSD --auto-context` is an operational signal run. It can fetch external data, call model providers and write command/evidence files. Its `--dry-run` skips final signal, brief and evidence writes, but does not make the pipeline offline or side-effect-free. Check configured paths and credentials before any manual run.

## Source map

- `xauex/main.py` and `xauex/bot/`: broker execution, risk and session state.
- `xauex/signal/`: context, prediction, confirmation and evidence.
- `xauex/app/`: dashboard and operator API.
- `xauex/analyst/`: journals, replay and reviews.
- `xauex/shared/`: safe file I/O, event journal, diagnostics and command contracts.
- `ops/`: wrappers, host integration and monitoring.

New code uses `xauex.*` imports. Keep wrappers and compatibility entry points aligned with the canonical package.
