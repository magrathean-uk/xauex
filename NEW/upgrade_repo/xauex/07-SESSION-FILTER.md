# 07 — XAUEX: Session Filter

## Purpose

Restrict trade entry to the London open window and enforce the Friday cutoff. Pre-entry gate only.

---

## Rules

| Rule | Value |
|---|---|
| Trading days | Monday–Friday |
| Trading window | 08:00–12:00 UK time (half-open: 08:00 permitted, 12:00 not) |
| Friday cutoff | No new entries at or after 16:00 UK time |
| Weekend | Never trade Saturday or Sunday |

"UK time" = `Europe/London` timezone, observing GMT in winter and BST (GMT+1) in summer. DST handling is mandatory — use `zoneinfo.ZoneInfo("Europe/London")`, never hardcode UTC offsets.

---

## Logic

```python
from zoneinfo import ZoneInfo
LONDON = ZoneInfo("Europe/London")

def is_tradeable(now_utc: datetime) -> tuple[bool, str]:
    london = now_utc.astimezone(LONDON)
    weekday = london.weekday()   # 0=Monday, 6=Sunday
    hour    = london.hour
    minute  = london.minute

    if weekday >= 5:
        return False, "WEEKEND"

    if weekday == 4 and hour >= 16:
        return False, "FRIDAY_CUTOFF"

    if hour < 8 or hour >= 12:
        return False, "OUTSIDE_SESSION"

    return True, "OK"
```

---

## Inside Bar Pending Orders

The entry gate applies at signal detection time, not at fill time. A stop order placed at 11:55 that fills at 12:05 is valid.

However: any pending inside bar stop orders not filled within 2 H1 candles are cancelled regardless of session. Do not leave stop orders open outside the trading window.

---

## SessionFilter Interface

```python
class SessionFilter:
    def is_tradeable(self, now_utc: datetime) -> tuple[bool, str]:
        """
        Returns (True, 'OK') or (False, reason_code).
        reason_code: WEEKEND | FRIDAY_CUTOFF | OUTSIDE_SESSION
        Pure function — no state, no async.
        """
```

---

## Test Cases for `tests/test_filters.py` (session section)

- Monday 09:00 London → `(True, 'OK')`
- Monday 07:59 London → `(False, 'OUTSIDE_SESSION')`
- Monday 08:00 London → `(True, 'OK')` (boundary: permitted)
- Monday 12:00 London → `(False, 'OUTSIDE_SESSION')` (boundary: not permitted)
- Monday 11:59 London → `(True, 'OK')`
- Friday 15:59 London → `(True, 'OK')`
- Friday 16:00 London → `(False, 'FRIDAY_CUTOFF')` (boundary: not permitted)
- Friday 10:00 London → `(True, 'OK')`
- Saturday 10:00 London → `(False, 'WEEKEND')`
- Sunday 10:00 London → `(False, 'WEEKEND')`
- DST spring-forward date: verify 08:00 London still maps to correct UTC
- DST autumn fallback date: verify 08:00 London still maps to correct UTC
