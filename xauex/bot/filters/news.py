"""Economic calendar news filter."""

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

import aiohttp

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

_FF_THIS_WEEK = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
_FF_NEXT_WEEK = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"


@dataclass
class NewsEvent:
    """Economic calendar event."""
    title: str
    currency: str
    impact: str           # "HIGH", "MEDIUM", "LOW"
    time_utc: datetime


def parse_ff_time(date_str: str, time_str: str) -> datetime:
    """
    Parse ForexFactory date+time strings to UTC datetime.
    date_str: "03-07-2026"  (MM-DD-YYYY)
    time_str: "8:30am" or "12:00pm"

    Times are US Eastern (ET), DST-aware.
    Returns UTC-aware datetime.
    """
    naive = datetime.strptime(f"{date_str} {time_str}", "%m-%d-%Y %I:%M%p")
    et_aware = naive.replace(tzinfo=ET)
    return et_aware.astimezone(timezone.utc)


def parse_ff_datetime(date_str: str, time_str: str = "") -> datetime:
    """
    Parse either legacy ForexFactory date+time fields or the newer ISO date field.

    Supported inputs:
    - Legacy: date="03-07-2026", time="8:30am"
    - Current JSON: date="2026-03-15T17:30:00-04:00"
    """
    if "T" in date_str:
        return datetime.fromisoformat(date_str).astimezone(timezone.utc)
    return parse_ff_time(date_str, time_str)


# Currencies whose HIGH-impact releases move XAUUSD materially. USD dominates,
# but EUR/GBP shape DXY and European-session liquidity; CHF/JPY matter for
# safe-haven flows (SNB decisions, BoJ interventions).
DEFAULT_BLOCK_CURRENCIES = frozenset({"USD", "EUR", "GBP", "CHF", "JPY"})


def is_news_clear(
    now_utc: datetime,
    events: List[NewsEvent],
    block_minutes: int,
    block_currencies: Optional[frozenset] = None,
) -> bool:
    """
    Pure function. Returns True if clear of HIGH events for the blocked currencies.
    Block window is symmetric: [event_time - block_minutes, event_time + block_minutes].
    """
    allowed = block_currencies or DEFAULT_BLOCK_CURRENCIES
    window = timedelta(minutes=block_minutes)
    for event in events:
        if event.currency.upper() not in allowed or event.impact.upper() != "HIGH":
            continue
        if abs(now_utc - event.time_utc) <= window:
            return False
    return True


class NewsFilter:
    """
    Block trade entry within ±30 minutes of high-impact USD events.

    Data source: ForexFactory JSON calendar.
    Cache: in memory, refresh daily at 00:05 UTC.
    Fallback: halt trading (raise RuntimeError) if feed unreachable.
    """

    def __init__(self, config):
        self.config = config
        self.events: List[NewsEvent] = []
        self.last_refresh_date: Optional[str] = None  # "YYYY-MM-DD"
        self.feed_available = False
        self._session: Optional[aiohttp.ClientSession] = None
        base_dir = os.path.dirname(getattr(config, "state_file_path", "/tmp/xauex_state.json")) or "."
        self._cache_path = os.path.join(base_dir, "news_calendar_cache.json")

    async def _get_session(self) -> aiohttp.ClientSession:
        """Return a shared session, creating it on first use."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self._session

    async def close(self) -> None:
        """Close the shared HTTP session. Call during bot shutdown."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def refresh_if_needed(self) -> None:
        """Fetch calendar if not yet fetched today (UTC date). Called once per day."""
        today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.last_refresh_date == today_utc:
            return
        await self._fetch_and_cache(today_utc)

    async def _fetch_and_cache(self, date_str: str) -> None:
        """Fetch both this-week and next-week calendars and cache in memory."""
        urls = [_FF_THIS_WEEK]
        # Also fetch next week on Sunday
        if datetime.now(timezone.utc).weekday() == 6:
            urls.append(_FF_NEXT_WEEK)

        events: List[NewsEvent] = []
        try:
            session = await self._get_session()
            for url in urls:
                try:
                    async with session.get(url) as resp:
                        resp.raise_for_status()
                        data = await resp.json(content_type=None)
                except aiohttp.ClientResponseError as exc:
                    if url == _FF_NEXT_WEEK and exc.status == 404:
                        logger.info(
                            "[NEWS] Optional next-week calendar is unavailable yet; continuing with this-week feed only."
                        )
                        continue
                    raise
                events.extend(self._parse_events(data))

            # Deduplicate and sort
            self.events = sorted(events, key=lambda e: e.time_utc)
            self.last_refresh_date = date_str
            self.feed_available = True
            self._save_cache(date_str)

            high_usd = sum(1 for e in events if e.currency == "USD" and e.impact.upper() == "HIGH")
            logger.info(f"[NEWS] Calendar refreshed. {high_usd} high-impact USD events this week.")

        except Exception as e:
            if self._load_cache():
                logger.info(
                    "[NEWS] Calendar fetch failed (%s). Using cached calendar from %s.",
                    e,
                    self.last_refresh_date,
                )
                return
            self.feed_available = False
            logger.critical(f"[NEWS] CRITICAL: Calendar feed unreachable. Trading blocked until refresh succeeds. Error: {e}")
            return

    def _parse_events(self, data: list) -> List[NewsEvent]:
        """Parse raw ForexFactory JSON list into NewsEvent objects."""
        events = []
        for item in data:
            try:
                currency = item.get("country", "").upper()
                impact = item.get("impact", "").upper()
                title = item.get("title", "")
                date_str = item.get("date", "")
                time_str = item.get("time", "")

                if not date_str:
                    continue

                # Legacy feed may have "All Day" events with no usable time
                if time_str and ("all day" in time_str.lower() or ":" not in time_str):
                    continue

                time_utc = parse_ff_datetime(date_str, time_str)
                events.append(NewsEvent(
                    title=title,
                    currency=currency,
                    impact=impact,
                    time_utc=time_utc,
                ))
            except Exception as e:
                logger.debug(f"[NEWS] Skipping unparseable event: {item} — {e}")
                continue
        return events

    def is_clear(self, now_utc: datetime) -> Tuple[bool, Optional[str]]:
        """
        Returns (True, None) if clear of HIGH events for all blocked currencies.
        Returns (False, event_title) if within the block window.
        """
        if not self.feed_available:
            return False, "NEWS_FEED_UNAVAILABLE"

        block = getattr(self.config, 'news_block_minutes', 30)
        window = timedelta(minutes=block)

        configured = getattr(self.config, 'news_block_currencies', None)
        if configured:
            allowed = frozenset(str(c).upper() for c in configured)
        else:
            allowed = DEFAULT_BLOCK_CURRENCIES

        for event in self.events:
            if event.currency.upper() not in allowed or event.impact.upper() != "HIGH":
                continue
            if abs(now_utc - event.time_utc) <= window:
                logger.info(
                    f'[NEWS] Trade blocked. Event: "{event.title}" ({event.currency}) at '
                    f'{event.time_utc.isoformat()}. Window: ±{block}min.'
                )
                return False, event.title

        return True, None

    def _save_cache(self, date_str: str) -> None:
        os.makedirs(os.path.dirname(self._cache_path) or ".", exist_ok=True)
        payload = {
            "last_refresh_date": date_str,
            "events": [
                {
                    "title": event.title,
                    "currency": event.currency,
                    "impact": event.impact,
                    "time_utc": event.time_utc.isoformat(),
                }
                for event in self.events
            ],
        }
        with open(self._cache_path, "w") as f:
            json.dump(payload, f)

    def _load_cache(self) -> bool:
        try:
            with open(self._cache_path, "r") as f:
                payload = json.load(f)
        except OSError:
            return False
        except json.JSONDecodeError:
            return False

        refresh_date = payload.get("last_refresh_date")
        raw_events = payload.get("events", [])
        if not refresh_date or not isinstance(raw_events, list):
            return False

        try:
            events = [
                NewsEvent(
                    title=item["title"],
                    currency=item["currency"],
                    impact=item["impact"],
                    time_utc=datetime.fromisoformat(item["time_utc"]),
                )
                for item in raw_events
            ]
        except Exception:
            return False

        self.events = sorted(events, key=lambda e: e.time_utc)
        self.last_refresh_date = refresh_date
        self.feed_available = True
        return True
