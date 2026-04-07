# Oracle Session Manager Design

Date: 2026-04-07
Status: Draft for review

## Goal

Redesign Oracle trade management so the live prediction can play out over several London-session hours without being killed by a rigid early stop, while keeping cash risk capped and preserving the existing automated daily flow.

This design also adds an independent manual-trade lane on the dashboard so the operator can place and close discretionary trades without affecting Oracle automation, limits, memory, or management logic.

## Scope

In scope:
- Replace the current rigid Oracle stop/exit behavior with a staged session manager.
- Keep risk capped in cash terms while allowing wider structure-aware initial stops.
- Keep Oracle confidence as an execution input, but shift its effect away from raw cash-risk expansion.
- Add dashboard controls for independent manual buy/sell/close actions with lot, SL, and TP.
- Add a lightweight chart on the dashboard using already-available local state.

Out of scope:
- Reintroducing Zep or the old MiroFish simulation website workflow.
- Adding a second daily Oracle run or second automated trade window.
- Letting manual trades participate in Oracle memory, trade caps, or Oracle-managed exits.

## Current Problems

The current live path has three structural weaknesses:

1. Oracle confidence scales lot size, but the stop model is still mostly fixed.
2. The initial broker stop can invalidate a multi-hour thesis too early.
3. The operator cannot intervene cleanly from the dashboard without using separate tools.

The result is that a trade can be technically valid at the session level while still being stopped out by early London noise.

## Proposed Architecture

### 1. Oracle Session Manager

Oracle-managed positions become a small state machine with explicit management phases:

- `ENTERED`
- `OBSERVE`
- `PROTECT`
- `TRAIL`
- `EXITED`

The state machine applies only to Oracle-owned positions.

The Oracle position manager will:
- assign an Oracle session-management record when an Oracle trade is opened
- monitor price progress in `R` terms
- decide when to move from observation to protection to trailing
- close the trade on catastrophe SL, managed stop, TP, time invalidation, or explicit thesis invalidation

### 2. Independent Manual Trade Lane

Manual trades are fully separate from Oracle-managed trades.

The dashboard will expose a small control panel that can:
- place a manual `BUY` or `SELL`
- specify lot size
- specify optional SL and TP
- close one chosen manual position

Manual trades will:
- be tagged as `manual`
- be visible on the dashboard
- be ignored by Oracle daily limits
- be ignored by Oracle predictor memory
- be ignored by Oracle session-management logic

Oracle trades will continue to be tagged as `oracle`.

### 3. Lightweight Charting

The dashboard will display a lightweight chart using local state only:
- recent H1 closes from `state.json`
- trade markers from `trade_entries_on_chart`
- open position markers where possible

This should be implemented with SVG or plain canvas in the current lightweight Flask dashboard, not with a heavy charting framework.

## Risk Model

### Core Principle

Cash risk stays capped. Confidence changes stop style and management behavior, not the maximum allowed cash exposure.

That means:
- wider stop => smaller lot
- narrower stop => potentially larger lot
- high confidence does not mean uncapped risk

### Initial Stop Construction

The Oracle signal still supplies a directional stop distance, but execution will not use it blindly.

The live initial stop will be derived as:

`initial_stop_distance = max(signal_stop, structure_stop, atr_stop)`

bounded by configured minimum and maximum Oracle stop limits.

Definitions:
- `signal_stop`: stop from the signal parser
- `structure_stop`: stop placed beyond recent market structure for the trade direction
- `atr_stop`: stop derived from local volatility, using an ATR-style distance

This initial stop is the broker catastrophe stop. It exists to cap disaster risk and protect against disconnects or sudden moves.

### Cash Risk and Lot Size

Lot size is recalculated from:
- account balance
- configured Oracle cash-risk cap
- selected initial stop distance
- symbol contract size and broker step rules
- confidence multiplier

Confidence multiplier affects the final lot within the cash cap, but the cash cap remains the upper bound.

Proposed confidence profile:
- low confidence directional trade: smaller lot, earlier protect, tighter trail
- medium confidence directional trade: moderate lot, standard protect, standard trail
- high confidence directional trade: full allowed lot, later protect, slower trail

## Session-Management Lifecycle

### ENTERED

Trade has just opened.

Actions:
- store session record
- store entry price
- store initial stop and take-profit
- store confidence bucket
- store position owner as `oracle`

### OBSERVE

Purpose:
- let the trade breathe through early post-entry noise

Rules:
- do not tighten immediately
- do not move to breakeven too early
- keep the catastrophe stop in place

Transition out of `OBSERVE`:
- move to `PROTECT` once price reaches a configured profit threshold, expressed in `R`
- or move to `EXITED` if catastrophe SL / TP / time exit happens first

### PROTECT

Purpose:
- remove most downside once the trade proves itself

Recommended behavior:
- move stop to breakeven plus a small cushion
- cushion should cover spread plus a small buffer, not be arbitrary

For longs:
- stop -> entry + protect_buffer

For shorts:
- stop -> entry - protect_buffer

Protect trigger:
- configurable, default around `+0.75R` to `+1.0R`

### TRAIL

Purpose:
- let stronger moves continue without freezing profit too early

Rules:
- activate only after stronger confirmation, default around `+1.25R` to `+1.5R`
- trail by structure or ATR, whichever is more appropriate for the direction and volatility
- do not widen a stop once tightened

### EXITED

Exit reasons:
- broker catastrophe stop hit
- managed stop hit
- take profit hit
- force-flat London session cutoff
- thesis invalidation condition
- manual close for Oracle-owned position, if ever added in future

Oracle-managed exits must only apply to Oracle-owned positions.

## Confidence Policy

Confidence should no longer behave as a crude participation gate.

New policy:
- `HOLD` remains available, but only for genuine hard blockers or very strong conflict
- directional trades with lower confidence still trade if they clear the directional threshold
- confidence changes:
  - lot multiplier
  - protect trigger
  - trail trigger
  - trail tightness

Example policy shape:
- low confidence:
  - smaller lot
  - earlier `PROTECT`
  - tighter trailing
- medium confidence:
  - current default management
- high confidence:
  - full lot inside risk cap
  - later `PROTECT`
  - slower trailing

## Dashboard Manual Controls

### Operator Requirements

The dashboard should expose:
- side: `BUY` / `SELL`
- lot size
- optional stop loss
- optional take profit
- submit button
- manual position list
- close button per manual position

### Execution Design

The dashboard must not talk directly to the broker.

Instead:
- dashboard writes a manual command to a separate local command file or API path
- XAUEX polls and executes that command
- XAUEX records owner `manual`

This keeps one execution engine and avoids duplicating broker logic in the dashboard.

### Isolation Rules

Manual positions:
- do not count toward `mirofish_trades_taken_london`
- do not trigger Oracle “already in market” logic
- do not get touched by Oracle force-flat logic
- do not get touched by Oracle TP/SL management
- do not go into Oracle retrieval memory or prediction context

The dashboard will show both lanes, but must label them clearly.

## Data Flow

### Oracle Trade Path

1. Bridge writes signal.
2. XAUEX validates signal and sizes trade.
3. XAUEX opens Oracle-owned position.
4. XAUEX creates/updates Oracle session-management state.
5. Position monitor advances trade through `OBSERVE -> PROTECT -> TRAIL -> EXITED`.
6. Dashboard renders current signal, trade state, and recent outcomes.

### Manual Trade Path

1. Operator submits manual order from dashboard.
2. Dashboard writes manual intent.
3. XAUEX consumes manual intent.
4. XAUEX opens manual-owned position.
5. Dashboard shows manual trade separately.
6. Oracle ignores that position for its own rules.

## State and Storage

New persistent fields will be needed for Oracle session management:
- position owner: `oracle` or `manual`
- Oracle session phase
- initial risk `R`
- protect trigger
- trail trigger
- last managed stop
- management timestamps

This state should live alongside existing state in local JSON persistence so restart recovery remains reliable.

## Error Handling

### Oracle Management Failures

If session-management logic cannot compute structure/ATR/protection correctly:
- keep catastrophe stop in place
- do not crash the bot
- log the failure clearly
- continue polling for the next valid management cycle

### Manual Order Failures

If manual order execution fails:
- preserve the error on the dashboard
- do not affect Oracle state
- do not retry indefinitely without operator action

### Restart Recovery

On restart:
- Oracle-owned open positions must be reloaded and remapped to session-management state where possible
- manual positions must also be recognized as manual so Oracle does not accidentally manage them

## Testing Strategy

Required tests:
- stop-construction tests for `max(signal, structure, atr)` behavior
- lot-sizing tests proving wider stops reduce lot while preserving cash cap
- session-phase transition tests:
  - `ENTERED -> OBSERVE`
  - `OBSERVE -> PROTECT`
  - `PROTECT -> TRAIL`
  - `TRAIL -> EXITED`
- restart recovery tests for Oracle-owned and manual-owned positions
- dashboard tests for manual order submission and manual close controls
- integration tests proving Oracle ignores manual positions

## Rollout Plan

Phase 1:
- implement Oracle session manager
- implement manual-trade dashboard lane
- keep one automated Oracle trade per London day
- keep current single morning Oracle run

Phase 2, only after stable live behavior:
- consider optional second Oracle run later in the London session
- tune protect/trail thresholds using live outcomes

## Recommended Defaults

Starting defaults:
- protect trigger: around `+0.85R`
- protect action: breakeven plus spread-and-buffer cushion
- trail trigger: around `+1.35R`
- trail style: structure-or-ATR, never widening stop

These are defaults, not permanent truths. They should be kept configurable because live gold behavior changes with volatility regime.

## Success Criteria

The redesign is successful if:
- Oracle trades survive early London noise better than the current rigid stop model
- cash risk remains capped
- Oracle still runs automatically without operator intervention
- manual dashboard trades work independently and do not corrupt Oracle logic
- dashboard clearly shows Oracle and manual positions separately
- the system remains lightweight and restart-safe
