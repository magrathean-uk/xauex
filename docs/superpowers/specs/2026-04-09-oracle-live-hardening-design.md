# Oracle Live Hardening Design

**Date:** 2026-04-09

**Goal**

Harden the live GoldOracle runtime so weekday trading, dashboard visibility, scheduling, recovery, and rebuild behavior are reliable without changing the core Oracle trading thesis.

**Scope**

This design covers the live Oracle path only:

- `bridge/` direct signal generation and dry-run behavior
- `xauex/` runtime scheduling, session lifecycle, recovery, and state exposure
- `dashboard_web/` operator-facing state, diagnostics, and manual controls
- `ops/` services, timers, deployment assumptions, and rebuild/runbook documentation

This design does not remove the legacy MiroFish backend or website in this pass. It keeps them available for research and compatibility, but removes them from the live operator truth path where possible.

## Current Problems

1. Friday/session timing is inconsistent.
   The bot can be stopped before the configured force-flat time, and journaling/review can run before late trades are actually closed.

2. Recovery is not fully autonomous.
   Timers do not catch up after downtime, and the live trading service is not boot-safe if the host comes back up after the start window.

3. Dashboard truth is split.
   Top-level signal state comes from `cmd.json`, but diagnostics are derived from XAUEX strategy signal state, so the UI can show contradictory answers.

4. Deployment assumptions are implicit.
   The dashboard and health endpoints bind openly and depend on machine-local nftables rules that are not represented in repo code or installer behavior.

5. Rebuild docs and example configs drift from the live working setup.
   The cTrader example omits the TLS server-name override used by the live runtime, and some timing/python settings disagree across files.

6. Dry-run mutates operator artifacts.
   `bridge.run --dry-run` still rewrites the latest brief and evidence bundle even when it does not write `cmd.json`.

7. Analyst automation is on a separate toolchain.
   Journaling and weekly review use an external Gemini CLI path with its own model assumptions instead of the main configured provider stack.

## Design Principles

1. Single source of live truth.
   Operator-facing Oracle state must come from XAUEX live state plus the latest bridge command file, not a mix of unrelated strategy telemetry.

2. Safe by default.
   Service and timer behavior must fail toward “skip trading” rather than “trade unpredictably”.

3. Rebuildable from repo.
   A fresh host should be able to reconstruct the deployment from repo files and docs without rediscovering machine-local fixes.

4. Keep behavior changes narrow.
   This pass is hardening and consistency work, not a rewrite of trading logic.

## Architecture

### 1. Runtime Scheduling

The live day should be modeled explicitly:

- `xauex-start.timer` starts the bot before the first London window
- `mirofish-bridge.timer` generates the morning and midday Oracle signals
- XAUEX enforces trade-slot logic from the signal file
- Oracle session management keeps live positions protected and trailed
- a close/stop path ensures Friday cannot leave Oracle exposed after the trading window
- journaling and weekly review run only after Oracle-managed positions have been flattened

The stop service should be treated as an operational end-of-session boundary, not just a process kill.

### 2. Live Operator Truth Model

The dashboard payload should be composed from:

- `state.json` for live bot/account/open-position/runtime state
- `cmd.json` for the latest Oracle signal
- `risk_state.json` for persistent London-slot and risk counters
- brief/evidence files for the human-readable run summary

Diagnostics should consume the same normalized signal object that the top-level dashboard returns, instead of independently inferring signal state from XAUEX strategy signal history.

### 3. Recovery Model

The system should recover from:

- service crash
- host reboot before the London session
- host reboot during a London session
- bridge timer miss due to downtime

The minimum acceptable recovery behavior is:

- the backend and dashboard come back automatically
- XAUEX is restarted automatically when the host is up during a valid weekday live window
- the bridge refresh can be triggered after downtime without manual rediscovery
- stale state never causes misleading operator UI

### 4. Deployment Boundary

Dashboard and health services should be intentionally reachable only through:

- localhost
- VPN interfaces that are explicitly allowed

That boundary must be reflected in:

- service bind addresses where practical
- installer or docs that describe the network assumption
- runbook verification commands

### 5. Bridge Dry-Run Semantics

`--dry-run` must be non-destructive for live operator artifacts. It may print a signal and optionally write to explicit user-provided paths, but it must not silently overwrite the currently displayed brief/evidence pair used by the dashboard.

### 6. Analyst Tooling

The analyst layer should be treated as optional reporting, not a hidden live dependency. This pass should either:

- keep it, but document it clearly as an external CLI dependency with failure behavior, or
- migrate it onto the main provider stack if that can be done without destabilizing the rest of the runtime

The recommended choice for this pass is to keep behavior stable but make the dependency explicit and failure-tolerant.

## Component Changes

### `ops/`

- Align stop, journal, and weekly review timing with the configured force-flat behavior
- Add catch-up or boot-safe behavior for weekday timers where appropriate
- Remove hardcoded deployment assumptions that prevent rebuild portability
- Update installer/runbook so the deployed units match the docs

### `dashboard_web/`

- Make dashboard payload and diagnostics read from the same Oracle-normalized signal
- Make run-slot counts consistent between account metrics and diagnostics
- Improve helper status output so it reflects the live Oracle runtime instead of stale simulation leftovers

### `xauex/`

- Preserve Oracle/live/manual separation
- Expose enough normalized runtime information to support a coherent operator view
- Ensure stop/shutdown flow does not leave Friday Oracle exposure unmanaged

### `bridge/`

- Preserve direct predictor behavior
- Make dry-run truly read-only by default
- Keep Qdrant and brief/evidence generation optional and failure-tolerant

### `docs/`

- Reconcile config examples, timing values, Python version expectations, network assumptions, and cTrader connection requirements

## Error Handling

1. If the bridge cannot fetch context, it should fail cleanly without mutating the live command or dashboard artifacts unless a non-dry run completes successfully.

2. If dashboard diagnostics cannot derive one section, they should degrade only that section, not invent contradictory signal state.

3. If analyst tooling is unavailable, the runtime should log a clear reporting failure without affecting live trading.

4. If shutdown/stop happens with Oracle positions still open at an end-of-week boundary, the stop path should attempt flattening first and log any residual failure clearly.

## Testing Strategy

1. Add regression tests for schedule/slot interpretation and Friday stop behavior where unit-level logic exists.

2. Add dashboard payload tests to assert:
   - top-level signal and diagnostics signal agree
   - slot counts are consistent
   - dry-run does not alter live brief/evidence outputs

3. Add config/deployment tests for:
   - dashboard service environment/bind assumptions
   - cTrader TLS env parsing and documented example config shape

4. Run full repo verification after integration:
   - `pytest -q`
   - `python -m py_compile $(rg --files -g '*.py')`
   - targeted service/timer inspection
   - dashboard API smoke checks

## Out of Scope

- Replacing the legacy backend entirely
- Changing the core weighted Oracle prediction model
- Adding new trading windows or new instruments
- Reworking the dashboard visual design beyond fixes needed for correctness and clarity

## Success Criteria

1. Friday/session scheduling is internally consistent and cannot stop the bot before the configured close logic is complete.
2. Dashboard top-level signal and diagnostics always agree on the latest Oracle state.
3. A rebuild from repo can reproduce the working cTrader and dashboard deployment assumptions.
4. Dry-run testing does not overwrite live dashboard artifacts.
5. Full tests and runtime smoke verification pass after integration.
