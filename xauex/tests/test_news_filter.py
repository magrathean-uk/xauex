from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from datetime import datetime, timedelta, timezone

from xauex.bot.filters.news import (
    DEFAULT_BLOCK_CURRENCIES,
    NewsEvent,
    NewsFilter,
    _FF_NEXT_WEEK,
    _FF_THIS_WEEK,
    is_news_clear,
)


class FakeResponse:
    def __init__(self, url: str, status: int, payload):
        self.url = url
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                request_info=SimpleNamespace(real_url=self.url),
                history=(),
                status=self.status,
                message="Not Found",
                headers=None,
            )

    async def json(self, content_type=None):
        return self.payload


class FakeSession:
    def __init__(self, mapping):
        self.mapping = mapping
        self.closed = False

    def get(self, url):
        return self.mapping[url]


def make_config(tmp_path):
    return MagicMock(
        state_file_path=str(tmp_path / "state.json"),
        news_block_minutes=30,
    )


@pytest.mark.asyncio
async def test_optional_next_week_404_does_not_poison_refresh(tmp_path):
    filt = NewsFilter(make_config(tmp_path))
    session = FakeSession(
        {
            _FF_THIS_WEEK: FakeResponse(
                _FF_THIS_WEEK,
                200,
                [
                    {
                        "title": "NFP",
                        "country": "USD",
                        "impact": "HIGH",
                        "date": "2026-04-06T12:30:00-04:00",
                    }
                ],
            ),
            _FF_NEXT_WEEK: FakeResponse(_FF_NEXT_WEEK, 404, None),
        }
    )
    filt._get_session = AsyncMock(return_value=session)

    with patch("xauex.bot.filters.news.datetime") as mock_datetime:
        from datetime import datetime, timezone

        mock_datetime.now.return_value = datetime(2026, 4, 5, 7, 0, tzinfo=timezone.utc)
        mock_datetime.fromisoformat = datetime.fromisoformat
        mock_datetime.strptime = datetime.strptime
        await filt._fetch_and_cache("2026-04-05")

    assert filt.feed_available is True
    assert filt.last_refresh_date == "2026-04-05"
    assert len(filt.events) == 1
    assert filt.events[0].title == "NFP"


def _ev(currency: str, impact: str, minutes_ahead: int, title: str = "Event") -> NewsEvent:
    now = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
    return NewsEvent(
        title=title,
        currency=currency,
        impact=impact,
        time_utc=now + timedelta(minutes=minutes_ahead),
    )


def test_is_news_clear_blocks_high_usd_event():
    now = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
    events = [_ev("USD", "HIGH", 10, "NFP")]
    assert is_news_clear(now, events, block_minutes=30) is False


def test_is_news_clear_now_blocks_high_eur_event():
    """Previously only USD events blocked; ECB/EU data moved gold without notice.

    Gold tracks DXY and safe-haven flows, so EUR, GBP, CHF, and JPY HIGH
    releases must also produce a blackout window.
    """
    now = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
    events = [_ev("EUR", "HIGH", 5, "ECB Decision")]
    assert is_news_clear(now, events, block_minutes=30) is False


def test_is_news_clear_allows_low_impact_events():
    now = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
    events = [_ev("USD", "LOW", 5), _ev("EUR", "MEDIUM", 5)]
    assert is_news_clear(now, events, block_minutes=30) is True


def test_is_news_clear_respects_custom_block_currencies():
    """Config may narrow the list to USD-only to avoid over-blocking."""
    now = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
    events = [_ev("EUR", "HIGH", 5, "ECB")]
    usd_only = frozenset({"USD"})
    assert is_news_clear(now, events, block_minutes=30, block_currencies=usd_only) is True


def test_default_block_currencies_covers_gold_drivers():
    assert {"USD", "EUR", "GBP", "CHF", "JPY"}.issubset(DEFAULT_BLOCK_CURRENCIES)
