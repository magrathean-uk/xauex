<p align="center">
  <img src="https://raw.githubusercontent.com/magrathean-uk/magrathean-uk/main/assets/icons/xauex.png" width="96" height="96" alt="">
</p>

<h1 align="center">XAUEX</h1>

<p align="center">A repo-packaged XAUUSD signal and cTrader demo execution runtime.</p>

<p align="center">
  <a href="docs/index.md">Documentation</a>
</p>

## Overview

XAUEX builds market context and evidence, writes a signal bundle, confirms the bundle for a scheduled window, and applies session, freshness, spread, symbol, replay, risk, and operator kill-switch gates before demo execution. It runs as a single-operator, self-hosted service with no packaged release or public distribution. It is not real-money production trading, and this repository does not by itself prove that any host is deployed or healthy.

## Features

- Builds XAUUSD market context, prediction, evidence, and a signal bundle for scheduled trading windows.
- Confirms each bundle against current quote, news, trend, shadow, and runtime state before entry.
- Applies session, freshness, spread, symbol, replay, risk, and operator kill-switch gates before demo execution.
- Runs the cTrader demo loop, manages positions, and exposes health, state, and controls on a loopback dashboard.
- Writes decision ledger, trade journal, weekly review, and alert artifacts.
- Supports an optional, shadow-only DSA sidecar for equity research evidence.

Trading windows come from `xauex/live_windows.py`:

| Window | Signal | Confirm | Entry | Time zone |
| --- | --- | --- | --- | --- |
| London morning | 07:55 | 07:59 | 08:00 to 08:10 | Europe/London |
| London midday | 11:25 | 11:29 | 11:30 to 11:40 | Europe/London |
| US open | 08:40 | 08:44 | 08:45 to 08:55 | America/New_York |

Windows are weekday windows. The US open schedule reads the post-release tape after the 08:30 New York macro-release minute. The signal writers preserve the top-level `kill_switch` in `/var/lib/xauex/cmd.json`; while it is active, the runtime stops new entry.

## Getting started

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

See [docs/development.md](docs/development.md) for focused test lanes, linting, and the optional Rust parser build. For host installation, see [docs/rebuild.md](docs/rebuild.md); for day-to-day operation, see [the operations runbook](docs/development/runbook.md).

## Documentation

- [docs/index.md](docs/index.md): full documentation index.
- [Development](docs/development.md): local environment, tests, and lint.
- [Rebuild](docs/rebuild.md): fresh-host installation.
- [Operations runbook](docs/development/runbook.md): status, kill switch, manual signal and confirm, logs.
- [DSA sidecar](docs/dsa-sidecar.md): optional shadow-evidence integration.
- [AGENTS.md](AGENTS.md): safety boundaries and verification commands for contributors and coding agents.
- [Contributing](.github/CONTRIBUTING.md), [Support](.github/SUPPORT.md), [Security](.github/SECURITY.md).
- [Legal overview](docs/legal/overview.md), [Trademarks](docs/legal/trademarks.md), [Third-party notices](docs/legal/third-party-notices.md).

## Licence

xauex is proprietary; the `tick_parser` component is open source under MIT OR Apache-2.0. See [LICENSE](LICENSE) and the [legal overview](docs/legal/overview.md) for the risk warning.

<sub>© 2026 MAGRATHEAN UK LTD · [Legal](https://github.com/magrathean-uk/.github/blob/main/LEGAL.md)</sub>
