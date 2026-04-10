# London Two-Window Oracle Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second configurable London execution slot for Oracle signals while preserving one deterministic per-slot run, two-run max per London day, and no weekend trading.

**Architecture:** Extend MIROFISH risk state to track daily run attempts separately from executed trades, gate poller execution by slot state, and expose slot/run telemetry through diagnostics and dashboard.

**Tech Stack:** Python, Flask.

### Task 1: Introduce configurable second window and run-slot helpers

- Modify: `xauex/config.py`
- Modify: `xauex/main.py`

- [ ] Add `MIROFISH_ENTRY_SECOND_START_LONDON` and `MIROFISH_ENTRY_SECOND_END_LONDON` with sane defaults.
- [ ] Add helpers to identify current London slot and whether the slot was already used today.
- [ ] Ensure all time-gating remains weekend-safe.

### Task 2: Track run attempts independently

- Modify: `xauex/bot/risk/gates.py`
- Modify: `xauex/main.py`
- Modify: `dashboard_web/app.py`

- [ ] Add persisted daily run-attempt ledger in `RiskState`.
- [ ] Persist/restore ledger via existing risk persistence pipeline.
- [ ] Expose `signal_runs_taken_today` and max cap in dashboard account/risk payloads.

### Task 3: Poller behavior for two slots

- Modify: `xauex/main.py`

- [ ] Update `_poll_mirofish_signal` to process both morning and midday slots once per day.
- [ ] Keep HOLD path active and allowed; allow directional actions only when slot is eligible and within cap.
- [ ] Preserve de-duplication logic per slot and signal id.
- [ ] Keep trade cap behavior with `MIROFISH_MAX_TRADES_PER_DAY`.

### Task 4: Docs + schedule

- Modify: `ops/mirofish-bridge.timer`
- Modify: `ops/RUNBOOK.md`
- Modify: `.env`, `xauex/.env`, and `.env.example`

- [ ] Add schedule and sample configuration for both windows.
- [ ] Update docs to describe two-slot flow and slot/run counters.

### Task 5: Tests and validation

- Add: `tests/xauex/test_mirofish_windows.py`

- [ ] Add tests for slot parsing and window behavior.
- [ ] Add tests for run-slot one-time-per-day behavior and cap logic.
- [ ] Add tests for new payload fields if present.

