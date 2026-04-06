# Repository Guidelines

## Project Structure & Module Organization
`main.py` is the async bot entry point. Core trading logic lives in `bot/` with domain packages for `api/`, `execution/`, `filters/`, `levels/`, `patterns/`, `risk/`, and `state/`. Historical simulation code lives in `backtester/`. Tick ingestion and parsing are split between `data/` and the Rust-backed `tick_parser/` extension. Tests live under `tests/`, with broader scenarios in `tests/integration/` and `tests/smoke/`. Operational scripts are in `ops/`, and the numbered `00-*.md` to `11-*.md` files document architecture and strategy decisions.

## Build, Test, and Development Commands
Use the `Makefile` as the primary interface:

- `make test` runs the full `pytest` suite.
- `make test-fast` stops on first failure with shorter output.
- `make build` compiles the Rust `tick_parser` wheel with `maturin`.
- `make install` rebuilds and installs the wheel into the active Python environment.
- `make backtest DATE_FROM=2022-01-01 DATE_TO=2026-02-28` runs the backtester.
- `make download DATE_FROM=2022 DATE_TO=2026` downloads Dukascopy tick data into `data/dukascopy/`.
- `make dashboard` launches the Textual dashboard; `make auth` runs the cTrader OAuth flow.
- `make lint` runs `ruff` and `mypy` checks when installed.

## Coding Style & Naming Conventions
Target Python 3.11+ and keep Python code PEP 8-aligned: 4-space indentation, `snake_case` for functions/modules, `PascalCase` for classes, and explicit type hints where practical. Follow the existing style of small focused modules, descriptive logger messages, and docstrings on non-trivial classes or flows. In Rust (`tick_parser/src/lib.rs`), follow standard `rustfmt` formatting and keep the Python-facing API minimal.

## Testing Guidelines
Tests use `pytest` with `pytest-asyncio` (`asyncio_mode = auto`). Name files `test_*.py` and keep fixtures near the tests that use them in `tests/conftest.py` or the local module. Add unit coverage for new trading rules, plus integration or smoke coverage for execution, persistence, or parser changes. Run `make test` before opening a PR.

## Commit & Pull Request Guidelines
Git history is not included in this workspace snapshot, so no repository-specific commit convention can be derived. Use short imperative commit subjects such as `Add trailing stop persistence guard`. Keep PRs focused, describe behavioral impact, list verification steps, and attach screenshots when changing `dashboard.py` or other operator-facing output.

## Security & Configuration Tips
Start from `.env.example`; never commit live cTrader credentials or tokens. Treat paths under `/var/lib/xauex` and `/var/log/xauex` as deployment-specific, and use `OBSERVE_ONLY=true` for new strategies until demo validation is complete.
