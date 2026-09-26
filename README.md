# XAUEX

XAUEX is a repo-packaged XAUUSD signal and cTrader demo execution runtime. It builds market context and evidence, writes a signal bundle, confirms the bundle for a scheduled window, and applies session, freshness, spread, symbol, replay, risk, and operator kill-switch gates before demo execution. It is not real-money production trading and this repository does not prove that any host is deployed or healthy.

## How it works

The normal flow is:

1. A window signal job builds XAUUSD context, prediction, evidence, and a command bundle.
2. A window confirm job checks the latest bundle against current quote, news, trend, shadow, and runtime state.
3. `xauex.service` runs the cTrader demo loop, manages positions, and enforces execution and risk gates.
4. The loopback dashboard exposes health, state, evidence, paper or demo positions, journals, and operator controls.
5. Review and monitoring jobs write decision ledger, journal, weekly review, and alert artifacts.

The signal writers preserve the top-level `kill_switch` in `/var/lib/xauex/cmd.json`. When it is active, the runtime stops new entry. The schedule source of truth is `xauex/live_windows.py`:

| Window | Signal | Confirm | Entry | Time zone |
| --- | --- | --- | --- | --- |
| London morning | 07:55 | 07:59 | 08:00 to 08:10 | Europe/London |
| London midday | 11:25 | 11:29 | 11:30 to 11:40 | Europe/London |
| US open | 08:40 | 08:44 | 08:45 to 08:55 | America/New_York |

Windows are weekday windows. The US open schedule reads the post-release tape after the 08:30 New York macro-release minute.

## Repository map

- `xauex/main.py` and `xauex/bot/` contain the demo broker loop, execution, filters, strategies, position state, and risk gates.
- `xauex/signal/` contains context building, source ingestion, prediction, confirmation, evidence, signal writing, memory, and shadow trials.
- `xauex/app/` contains the loopback Flask dashboard and runtime API.
- `xauex/analyst/` contains the decision ledger, journals, replay, policy, and weekly review.
- `xauex/shared/` contains diagnostics, safe I/O, event journaling, replay guards, and manual-command contracts.
- `xauex/backtester/` and `xauex/tick_parser/` contain historical backtesting and the Rust tick parser extension.
- `ops/` contains systemd units, wrappers, monitoring, host checks, Caddy examples, and the optional DSA service.
- `tests/` and `xauex/tests/` contain bridge, dashboard, runtime, security, signal, broker, schedule, and regression tests.

The root `config.py`, `auth.py`, and `bot/__init__.py` files are compatibility shims. New code should import from `xauex.*`.

## Setup and local verification

The checked-in fresh-host procedure is documented in [docs/REBUILD.md](docs/REBUILD.md). It requires Python 3.11 or newer, Git, curl, systemd, sudo access, and cTrader demo credentials. The procedure creates `.venv`, installs `requirements.txt`, and can install systemd units. Keep both environment files private and never commit credentials or tokens.

For a development environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-dev.txt
```

Run the full test suite from the repository root:

```bash
python3 -m pytest
```

Useful focused checks are:

```bash
python3 -m pytest xauex/tests/test_xauex_windows.py xauex/tests/test_session_manager.py xauex/tests/test_manual_trade_commands.py -q
python3 -m pytest tests/bridge/test_signal_writer.py tests/bridge/test_signal_parser_validator.py tests/dashboard/test_manual_controls.py -q
```

See [the development guide](docs/DEVELOPMENT.md) for focused test lanes, linting and the optional Rust parser build. The root pytest command covers both test trees; `make -C xauex test` covers only `xauex/tests/`.

Signal generation and confirmation are operational commands that can contact providers and change runtime artifacts. Use the [manual signal procedure](ops/RUNBOOK.md#manual-signal-and-confirm) against the intended demo environment.

## Operations

The [operations runbook](ops/RUNBOOK.md) describes service installation, status checks, kill-switch handling, manual signal and confirmation, source-truth state files, monitoring, logs, and code verification. The dashboard service listens on loopback at `127.0.0.1:8089`; the bot health endpoint listens on `127.0.0.1:8051`. The checked-in Caddy configuration is VPN-only and proxies to the loopback dashboard.

The primary runtime artifacts are under `/var/lib/xauex`, including `cmd.json`, `state.json`, `risk_state.json`, signal evidence, signal runs, shadow trials, trade journal, and weekly review files. Logs are written under `/var/log/xauex` by the deployed configuration. Inspect the target host to establish actual service state.

## Optional DSA sidecar

The [DSA sidecar guide](docs/DSA_SIDECAR.md) describes an optional localhost-only equity research integration. It is disabled by default, shadow-only, and advisory. The adapter adds output to signal evidence. Keep it out of the command and execution paths, with `XAUEX_DSA_SHADOW_ONLY=true` and loopback configuration. Do not treat it as a replacement for the XAUUSD signal path.

## Security, legal, and licensing

See [SECURITY.md](SECURITY.md) for the repository-declared vulnerability reporting contact and operational security boundaries. See [LEGAL.md](LEGAL.md) for the proprietary status, no-advice statement, and trading risk warning. See [TRADEMARKS.md](TRADEMARKS.md) for marks and attribution. The root proprietary terms are in [LICENSE](LICENSE). The [tick parser component](xauex/tick_parser/LICENSE.md) is separately licensed under `MIT OR Apache-2.0`, at the recipient's option. [license.md](license.md) records that component exception and the dependency declarations. It is not a complete third-party notice bundle, and this documentation does not withdraw any existing grant.

## Documentation and contribution guidance

[AGENTS.md](AGENTS.md) records project-specific safety boundaries, source-truth files, and verification commands for contributors and coding agents. Keep source, tests, fixtures, runtime contracts, and legal text aligned. Preserve the demo-only boundary, all confirmation and risk gates, the localhost-only DSA posture, and unrelated dirty-worktree changes. Do not commit `.env`, `xauex/.env`, credentials, tokens, logs, runtime state, caches, or virtual environments.

See [CONTRIBUTING.md](CONTRIBUTING.md) for change and review expectations and [SUPPORT.md](SUPPORT.md) for help.

The [fresh-host rebuild](docs/REBUILD.md), [operations runbook](ops/RUNBOOK.md), and [DSA sidecar guide](docs/DSA_SIDECAR.md) are the maintained procedural documents. Historical design and implementation notes under `docs/superpowers/` are not the source of current runtime behavior.
