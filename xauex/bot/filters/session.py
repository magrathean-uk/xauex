"""Session time filter for London + NY overlap trading window."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Tuple

logger = logging.getLogger(__name__)

LONDON = ZoneInfo("Europe/London")


class SessionFilter:
    """
    Restrict trade entry to London + NY overlap trading window.

    Rules:
    - Monday–Friday only (weekday 0–4)
    - 08:00–17:00 UK time (half-open: 08:00 permitted, 17:00 not)
    - No new entries Friday at or after 16:00 UK time
    - Weekend (Saturday=5, Sunday=6): never
    """

    def is_tradeable(self, now_utc: datetime) -> Tuple[bool, str]:
        """
        Returns (True, 'OK') or (False, reason_code).
        reason_code: WEEKEND | FRIDAY_CUTOFF | OUTSIDE_SESSION
        Pure function — no state, no async.

        Session windows:
        - Monday–Thursday: 08:00–17:00 London (London + NY overlap)
        - Friday: 08:00–16:00 London (hard cutoff at 16:00)
        """
        london = now_utc.astimezone(LONDON)
        weekday = london.weekday()   # 0=Monday, 6=Sunday
        hour = london.hour

        if weekday >= 5:
            return False, "WEEKEND"

        if weekday == 4:
            # Friday: hard cutoff at 16:00
            if hour >= 16:
                return False, "FRIDAY_CUTOFF"
            if hour < 8:
                return False, "OUTSIDE_SESSION"
            return True, "OK"

        # Monday–Thursday: 08:00–17:00
        if hour < 8 or hour >= 17:
            return False, "OUTSIDE_SESSION"

        return True, "OK"
