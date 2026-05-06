# XAUEX Stock Transition Plan

## Goal

Move XAUEX from gold-only execution (`XAUUSD`) to one target stock instrument, while keeping the same live safety discipline:

- no bypass of risk gates
- no bypass of kill switch logic
- no bypass of execution confirmation rules
- no widening of daily/weekly risk limits without explicit decision

This plan assumes we migrate one symbol first (for example `AAPL`), prove it in shadow mode, then enable live.

## Current Constraints

Today the system is still gold-centric in key runtime paths:

- bot startup and tick subscription hardcode `XAUUSD`
- signal timer wrapper hardcodes `--asset XAUUSD`
- level validation uses a gold-only price range
- macro snapshot bias logic is gold-specific

The signal layer already has multi-asset scaffolding (`XAUUSD`, `WTI`, `GBPJPY`), but live execution is still tuned for gold behavior.

## Delivery Phases

## Phase 1: Runtime Symbol Configuration (No Strategy Change)

Introduce one runtime symbol config and remove hardcoded symbol strings from runtime entry points.

### Changes

- Add config key: `XAUEX_TRADE_SYMBOL` (default `XAUUSD`).
- Use this symbol in:
  - bot symbol spec load
  - tick subscription
  - signal-run wrapper script
- Keep symbol mismatch protection active (already present), so wrong-asset signals are skipped safely.

### Success Criteria

- Bot starts and loads symbol spec for configured symbol.
- Tick stream is subscribed for configured symbol.
- No change to risk-limit logic, kill switch, or position-count gates.

## Phase 2: Price/Levels Normalization

Replace gold-only level validation assumptions with symbol-aware bounds.

### Changes

- Move static gold range validation into symbol profile or config.
- Add per-symbol value sanity bounds for daily/weekly/monthly levels.
- Preserve existing dedup and proximity behavior.

### Success Criteria

- Level refresh works for the stock symbol without false rejection.
- Invalid/garbage prices still fail closed.

## Phase 3: Signal Pipeline Alignment for the Stock

Switch the signal context and bias logic from gold heuristics to stock-appropriate drivers.

### Changes

- Add stock profile in asset registry with:
  - distance unit
  - stop-loss guardrails
  - take-profit RR bounds
- Add stock source registry entries (earnings, guidance, macro sensitivity, sector flow).
- Refactor market snapshot bias rules so they are symbol-aware instead of gold-only.

### Success Criteria

- Signal output distances and reasoning align to stock behavior.
- Parser/validator still fail closed on malformed signals.
- Cost controls and evidence logging remain unchanged.

## Phase 4: Execution Calibration

Calibrate sizing and execution assumptions for stock microstructure.

### Changes

- Validate lot/volume conversion assumptions with broker symbol spec.
- Tune spread guards and minimum stop distance by symbol.
- Confirm SL/TP distance interpretation stays correct for the stock instrument.

### Success Criteria

- No zero-volume or rejected-volume orders due to symbol unit mismatch.
- Live spread guard blocks only when intended.
- Risk per trade stays within configured cap.

## Phase 5: Shadow Trial and Live Cutover

Run a controlled trial before live activation.

### Changes

- Run shadow decisions for the stock while keeping live execution off.
- Compare hit-rate, false signals, blocked signals, and drawdown profile.
- Execute go/no-go checklist before enabling live.

### Success Criteria

- Stable shadow performance over agreed trial window.
- No critical runtime errors.
- Operator sign-off before live trading.

## Validation and Test Gates

Run in this order after each phase:

1. targeted unit tests for edited modules
2. `python3 -m pytest tests/bridge -q`
3. `python3 -m pytest tests/dashboard -q`
4. `python3 -m pytest xauex/tests -q`
5. `python3 -m pytest -q`

For host rollout:

1. redeploy units/scripts
2. restart service
3. verify health endpoint, timers, and state updates
4. verify runtime symbol from live process environment and logs

## Estimated Effort

- Phase 1: 0.5-1 day
- Phase 2: 0.5-1 day
- Phase 3: 1-2 days
- Phase 4: 0.5-1 day
- Phase 5: 1-2 days (depends on trial duration)

Total engineering effort: ~3-5 days plus agreed shadow-trial time.

## Risks and Controls

- Risk: wrong unit assumptions for stock volume.
  - Control: strict symbol-spec checks + dry-run sizing verification before live.
- Risk: stock data context quality lower than gold context.
  - Control: asset-specific source curation + validator strictness retained.
- Risk: accidental risk-policy drift during migration.
  - Control: no edits to risk caps/kill-switch semantics without explicit approval.

## Definition of Done

Done means:

- runtime symbol is configurable and not hardcoded to gold
- signal pipeline produces stock-specific decisions with validated geometry
- full test suite passes
- shadow trial is completed and reviewed
- live enablement is an explicit operator choice, not automatic
