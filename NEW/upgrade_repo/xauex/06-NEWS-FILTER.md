# 06 — XAUEX: News Filter

## Purpose

Block trade entry within ±30 minutes of any high-impact USD economic event. Pre-entry gate only — does not modify or close existing open positions.

---

## Target Events

Block on any event matching ALL of:
- Currency: `USD`
- Impact: `High`

Do not hardcode a named event list. Filter dynamically on currency + impact so new events are caught automatically.

---

## Data Source

ForexFactory JSON calendar feed — free, no API key required:

```
https://nfs.faireconomy.media/ff_calendar_thisweek.json
https://nfs.faireconomy.media/ff_calendar_nextweek.json
```

Fetch `thisweek` on startup. Fetch `nextweek` on Sunday (same day as level refresh). Cache in memory. Refresh once per day at 00:05 UTC — do not fetch on every tick.

**Fallback:** If the feed is unreachable, set `bot_status = HALTED_NEWS_FEED`, halt trading, write state file. Do not trade without a valid calendar. Retry every 30 minutes until successful.

---

## Filter Logic

```python
def is_news_clear(now_utc: datetime, events: list[NewsEvent], block_minutes: int) -> bool:
    window = timedelta(minutes=block_minutes)
    for event in events:
        if event.currency != "USD" or event.impact != "HIGH":
            continue
        if abs(now_utc - event.time_utc) <= window:
            return False
    return True
```

Window is symmetric: blocks from `event_time - 30min` to `event_time + 30min` inclusive.

---

## Timezone Conversion

ForexFactory times are US Eastern Time (ET). Convert to UTC using `zoneinfo.ZoneInfo("America/New_York")`. Never hardcode a fixed offset — ET is UTC-5 in winter and UTC-4 in summer (DST).

```python
from zoneinfo import ZoneInfo
ET = ZoneInfo("America/New_York")

def parse_ff_time(date_str: str, time_str: str) -> datetime:
    # date_str: "03-07-2026", time_str: "8:30am"
    naive = datetime.strptime(f"{date_str} {time_str}", "%m-%d-%Y %I:%M%p")
    et_aware = naive.replace(tzinfo=ET)
    return et_aware.astimezone(timezone.utc)
```

---

## NewsFilter Interface

```python
@dataclass
class NewsEvent:
    title: str
    currency: str
    impact: str       # "HIGH", "MEDIUM", "LOW"
    time_utc: datetime

class NewsFilter:
    async def refresh_if_needed(self) -> None:
        """Fetch calendar if not fetched today. Called once per day at 00:05 UTC."""

    def is_clear(self, now_utc: datetime) -> tuple[bool, str | None]:
        """
        Returns (True, None) if clear.
        Returns (False, event_title) if within block window.
        """
```

---

## Logging

```
[NEWS] Calendar refreshed. 8 high-impact USD events this week.
[NEWS] Trade blocked. Event: "Non-Farm Employment Change" at 2026-03-06T13:30:00Z. Window: ±30min.
[NEWS] CRITICAL: Calendar feed unreachable. Trading halted. Retrying in 30min.
```

---

## Test Cases for `tests/test_filters.py` (news section)

- Event at 13:30 UTC, check at 13:01 → blocked (29min before, inside window)
- Event at 13:30 UTC, check at 13:00 → blocked (30min before, boundary inclusive)
- Event at 13:30 UTC, check at 12:59 → clear (31min before)
- Event at 13:30 UTC, check at 14:00 → blocked (30min after, boundary inclusive)
- Event at 13:30 UTC, check at 14:01 → clear (31min after)
- Non-USD high-impact event → does not block
- USD medium-impact event → does not block
- Empty calendar → clear
- DST winter date: ET 08:30 → UTC 13:30 correct
- DST summer date: ET 08:30 → UTC 12:30 correct
