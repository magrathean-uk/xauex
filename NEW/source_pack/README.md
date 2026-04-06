# MiroFish source pack

This package contains curated source lists for three assets:

- `XAUUSD`
- `WTI`
- `GBPJPY`

The goal is to give the swarm higher-quality public inputs than a hand-written headline blob.

## What is compatible with MiroFish?

MiroFish itself only needs **plain text**. That means nearly any source is compatible **after** you normalize it into text or markdown.

In practice:

- **RSS** is the easiest format and should be your default.
- **HTML pages** work well once you extract titles, summaries and dates.
- **APIs** are excellent, but only after you choose the series or endpoint you actually want.
- **PDF-only sources** are usable, but they need a screenshot or PDF parser stage and are slower to automate.

The improved bridge in the upgraded repo automatically ingests the `auto_fetch=true` sources in this pack.

## How to use this pack

1. Start with official macro and policy feeds.
2. Add one or two flow/positioning sources.
3. Add one specialized news source only after dedupe.
4. Keep the lookback window short enough that the swarm is reacting to the current regime rather than stale narratives.

See `compatibility_guide.md` and the `context_templates/` folder for recommended mixes.
