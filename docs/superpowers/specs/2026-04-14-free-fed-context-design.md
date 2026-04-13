# Free Fed Policy Context Design

## Goal

Improve XAUEX's gold decision packet with free, official U.S. policy-state context without adding paid FedWatch or brittle public scraping.

## Scope

In scope:
- official FOMC calendar awareness
- free FRED policy-rate series
- derived policy-state features for the signal packet
- dashboard/evidence visibility for operators
- failure-safe behavior when one or more free sources are unavailable

Out of scope:
- paid CME FedWatch API
- scraping CME QuikStrike internals
- using Forex Factory in live auto-trading logic
- treating policy-state inputs as standalone BUY/SELL signals

## Source Decisions

### Keep

- Federal Reserve FOMC calendar
  - purpose: official event timing
  - role: event-window awareness and risk-state flags

- FRED `DFF`
  - purpose: daily effective federal funds rate
  - role: actual daily effective rate context

- FRED `DFEDTARU`
  - purpose: target range upper bound
  - role: current policy stance

- FRED `DFEDTARL`
  - purpose: target range lower bound
  - role: current policy stance

### Reject

- FRED `FEDFUNDS`
  - monthly average
  - too slow and too lagged for London-session gold decisions

- Forex Factory in live auto path
  - useful for humans
  - not strong enough or stable enough for direct trade logic

- public CME FedWatch scraping
  - wrapper page leads to protected QuikStrike content
  - too fragile for production

## Data Model

Add a new free-policy block under the structured market snapshot:

```json
{
  "policy_context": {
    "status": "available|warning|unavailable",
    "source": "fed_fomc_calendar+fred",
    "next_fomc_date": "2026-05-06",
    "days_to_fomc": 22,
    "fomc_window_state": "normal",
    "effective_fed_funds_rate": 3.64,
    "target_lower": 3.50,
    "target_upper": 3.75,
    "target_mid": 3.625,
    "dff_minus_target_mid_bps": 1.5,
    "dff_minus_upper_bps": -11.0,
    "dff_minus_lower_bps": 14.0,
    "summary": "Daily effective fed funds remains near the current target range midpoint; next FOMC is 22 days away."
  }
}
```

This block is additive only. Existing consumers must continue to work if it is absent.

## Derived Features

XAUEX should compute these fields:

- `next_fomc_date`
- `days_to_fomc`
- `fomc_window_state`
  - `normal`
  - `approaching`
  - `today`
  - `recent`
- `effective_fed_funds_rate`
- `target_lower`
- `target_upper`
- `target_mid`
- `dff_minus_target_mid_bps`
- `dff_minus_upper_bps`
- `dff_minus_lower_bps`
- concise `summary`

### FOMC window logic

Default rule:
- `today`: calendar day of scheduled FOMC decision
- `approaching`: 1 to 3 calendar days before decision
- `recent`: 1 calendar day after decision
- `normal`: otherwise

This is simple by design and should be deterministic.

## Decision Behavior

These free policy inputs should affect XAUEX in three ways:

1. Event awareness
   - signal/validator can lower confidence when the current environment is too close to an FOMC decision and evidence is weak
   - this should not hard-block by itself on ordinary days

2. Policy-state context
   - parser and validator can reason about where daily effective fed funds sits relative to the current target range
   - this is context, not a direct trade instruction

3. Operator clarity
   - dashboard/evidence should explain policy backdrop in plain text

## Failure Handling

If one source fails:
- keep signal generation running
- mark `policy_context.status` as `warning` or `unavailable`
- include a short summary
- do not crash the run

If all policy-context inputs fail:
- keep the signal path alive
- omit directional bias from policy context
- let price structure, macro sources, and existing market snapshot continue to drive the signal

## Files To Touch

Expected implementation scope:
- `xauex/signal/config.py`
- `xauex/signal/market_snapshot.py`
- `xauex/signal/direct_predictor.py`
- `xauex/signal/run.py`
- new policy-context adapter module under `xauex/signal/`
- `tests/bridge/` for adapter, normalization, and integration tests
- optional docs/env example updates

## Testing Strategy

Required tests:
- official source parsing tests for FOMC calendar and FRED rate rows
- derived-metric tests for policy-state calculations
- integration tests proving payload and report include policy context
- dry-run tests proving missing policy data does not break signal generation

## Recommendation

Implement the free official-source path now.

Do not spend money on FedWatch.
Do not add Forex Factory to the live trade path.
If operator-facing event color is desired later, add Forex Factory as advisory-only metadata in the dashboard, not in the auto-trading packet.
