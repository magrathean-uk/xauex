# 03 — XAUEX: HTF Level Management

## Purpose

Extract previous monthly and weekly OHLC for XAUUSD on startup and refresh weekly. Store 8 price levels as the active map. All trade signals require price to be within proximity of one of these levels.

---

## The 8 Levels

| Label | Source |
|---|---|
| `MN_O` | Previous monthly open |
| `MN_H` | Previous monthly high |
| `MN_L` | Previous monthly low |
| `MN_C` | Previous monthly close |
| `WK_O` | Previous weekly open |
| `WK_H` | Previous weekly high |
| `WK_L` | Previous weekly low |
| `WK_C` | Previous weekly close |

---

## Data Source

Use `ProtoOAGetTrendbarsReq` from the cTrader Open API.

- **Monthly:** period `MONTHLY`, request 2 bars, use index -2 (last fully closed)
- **Weekly:** period `WEEKLY`, request 2 bars, use index -2

Always request 2 bars and take index -2. Index -1 is the current forming bar — never use it for level extraction.

---

## Timezone Anchor

cTrader bar timestamps are UTC. The broker's weekly bar boundary is typically Sunday 00:00 UTC but must not be assumed — derive it empirically.

On every refresh: log the `open_time` of the weekly bar at index -2. Store this timestamp alongside the levels. On subsequent refresh checks, compare new bar `open_time` to stored — if different, levels have changed and refresh proceeds.

---

## Weekly Refresh Trigger

1. Always on startup
2. On every H1 candle close: check if weekly bar at index -2 has a new `open_time`
3. If new → refresh all 8 levels, log the change, update state file

Do not refresh on a timer. Derive refresh need from actual bar data.

---

## Proximity Check

Price is "at a level" if `abs(price - level) <= LEVEL_PROXIMITY_DOLLARS` (config default: 3.0 USD).

```python
def price_at_level(price: float, levels: list[float], proximity: float) -> float | None:
    candidates = [
        (abs(price - lvl), lvl)
        for lvl in levels
        if abs(price - lvl) <= proximity
    ]
    if not candidates:
        return None
    return min(candidates)[1]  # return nearest level
```

---

## Level Deduplication

If two levels are within $1.0 of each other (e.g. monthly close coincides with weekly open), merge to their average. Prevents double-triggering at the same zone.

---

## LevelManager Interface

```python
class LevelManager:
    async def refresh(self) -> None:
        """Fetch W1 and MN1 bars, extract and store levels with timestamps."""

    async def refresh_if_needed(self) -> bool:
        """Check if weekly bar open_time has changed. Refresh if so. Return True if refreshed."""

    def price_at_level(self, price: float) -> float | None:
        """Return nearest level within proximity, else None."""

    def all_levels(self) -> list[float]:
        """Return all 8 levels after deduplication."""

    def next_level_from(self, price: float, direction: int) -> float | None:
        """
        Return nearest level in given direction from price.
        direction: +1 = above (TP for longs), -1 = below (TP for shorts).
        Returns None if no level exists in that direction.
        """
```

---

## Validation on Refresh

After extracting levels, validate:
- All 8 values are non-zero positive floats
- Monthly high > monthly low
- Weekly high > weekly low
- All values within plausible XAUUSD range: $500–$5,000

If validation fails: log error, retain previous levels, do not overwrite.

---

## Logging

On refresh:
```
[LEVELS] Refreshed. MN: O=2680.00 H=2750.50 L=2610.20 C=2740.10 | WK: O=2720.00 H=2745.00 L=2700.00 C=2735.00 | Bar time: 2026-03-02T00:00:00Z
```

On proximity match:
```
[LEVELS] Price 2720.45 within 3.0 of level 2720.00 (WK_O)
```

On validation failure:
```
[LEVELS] ERROR: Refresh validation failed (MN_H=0.0). Retaining previous levels.
```
