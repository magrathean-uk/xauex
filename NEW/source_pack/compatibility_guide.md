# Compatibility guide

## Recommended source mix by asset

### XAUUSD

- 50% official macro and central-bank releases
- 25% positioning and flow
- 25% gold-specific market structure and narrative

Suggested lookback: **24 to 72 hours**

### WTI

- 40% official inventory and outlook releases
- 30% supply and weather disruption sources
- 30% positioning and specialized energy news

Suggested lookback: **12 to 72 hours**

### GBPJPY

- 50% BoE, BoJ and official macro releases
- 20% release calendars and intervention-risk monitoring
- 30% positioning and structured macro data

Suggested lookback: **24 to 96 hours**

## Best-practice ingestion rules

- Deduplicate very similar headlines before they reach the swarm.
- Prefer official releases over third-party commentary when they conflict.
- Keep release calendars in the context so the swarm knows when information is stale or event risk is imminent.
- Mark `auto_fetch=false` sources as manual or adapter-backed inputs instead of pretending they are already automated.
- Do not let one category dominate the context file. A good dossier mixes policy, macro, flow and market-structure inputs.

## File formats in this pack

- `*.json` -> easiest for code
- `*.csv` -> easiest for filtering or spreadsheet review
- `*.md` -> easiest to read manually

## Important realism note

This source pack can improve the quality and consistency of the context you feed into the swarm, but it **does not guarantee profitable trading**. Treat it as an input-quality upgrade, then validate everything with backtests and demo trading before any live use.
