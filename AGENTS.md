# XAUEX agent guidance

## Boundaries that matter

- XAUEX is an XAUUSD signal and cTrader demo-execution runtime. Do not describe this checkout as a verified deployment or as ready for real-money trading.
- Preserve confirmation, freshness, spread, risk, session, symbol and replay checks, plus the operator `kill_switch` in `cmd.json`. Signal writers must retain that switch.
- Use `xauex/live_windows.py` for trading windows. Keep generated timers, monitoring and documentation aligned when schedules change.
- Keep the optional DSA adapter disabled by default, loopback-only and shadow-only. Its evidence must not enter commands or execution.
- Use `xauex.*` imports. Root `auth.py`, `config.py`, `diagnostics.py`, `tui_diagnostics.py` and `bot/` are compatibility entry points.
- Preserve unrelated work. Do not include environment files, tokens, broker account data, logs, runtime state or generated artifacts in changes.

## Work and verification

Use Python 3.11+ and the development dependencies described in [docs/development.md](docs/development.md). From the repository root:

```bash
python3 -m pytest
make -C xauex lint
```

Start with tests for the changed behavior. Signal and dashboard changes have coverage in `tests/bridge/`, `tests/dashboard/` and `tests/security/`; execution and risk changes have coverage in `xauex/tests/`. The root pytest command covers both trees. `make -C xauex test` covers only `xauex/tests/`.

Finish with the relevant tests and the full suite for code changes. Record exact blockers and distinguish a checked command from an executed, passing check. For documentation-only changes, check paths, links, command definitions and consistency without starting services or running the signal pipeline.

The signal runner can call providers and write runtime artifacts. The installer changes host configuration and can start or restart the bot. These are operational actions, not local test commands. Use [docs/development/runbook.md](docs/development/runbook.md) for host work and [docs/dsa-sidecar.md](docs/dsa-sidecar.md) for sidecar changes.

<!-- clean-development-policy:v1 (canonical text: ~/dev/source/dev-bootstrap/snippets/clean-development-policy.md) -->
## Clean development (mandatory)

This project follows [Clean Development](https://github.com/magrathean-uk/clean-development) and the machine rule that nothing creates tool state under `~` (only the allow-listed agent homes).

- The shell environment comes from `~/.zshenv`, which loads `~/dev/env.zsh`. It routes every tool home and cache (`CARGO_HOME`, `RUSTUP_HOME`, `XDG_*`, `BUNDLE_USER_HOME`, `npm_config_cache`, `XCODE_DERIVED_DATA_PATH`, ...) and switches telemetry off. Never unset, override or bypass those variables. If a script needs a scrubbed environment, re-export them with `source ~/dev/env.zsh`.
- Run builds, tests, installs and anything else that writes caches or build output through Clean Development: `clean-development run --session session-only -- <command>`. Follow its docs and keep its receipts.
- Do not add installers or scripts that default into `~` (`~/.cargo`, `~/.rustup`, `~/.cache`, `~/.npm`, `~/.swiftpm`, `~/.gradle`, ...) and do not hardcode `$HOME` paths for caches; use the routed variables.
- Before finishing, run `dev-env-check` (must pass) and `dev-audit` (no new entries in `~`). If your work caused a violation, fix the cause in the repo and say so.

## Keep guidance current

Use [CONTRIBUTING.md](.github/CONTRIBUTING.md) for contribution checks and [SECURITY.md](.github/SECURITY.md) for reporting and trust boundaries. Preserve the legal texts and attribution listed in [docs/legal/overview.md](docs/legal/overview.md).

Dated files under `docs/development/archive/` record earlier designs and plans. They are historical context, not current deployment authority or evidence that their acceptance checks passed. Prefer current code and maintained guides when those notes differ.

## Legal files

Legal files (`LICENSE`, `NOTICE`, `docs/legal/`, contributor terms, copyright and attribution strings) are owner-controlled: change them only on the owner's explicit instruction.
