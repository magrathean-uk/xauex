from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from bot.filters.news import NewsFilter, _FF_NEXT_WEEK, _FF_THIS_WEEK


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

    with patch("bot.filters.news.datetime") as mock_datetime:
        from datetime import datetime, timezone

        mock_datetime.now.return_value = datetime(2026, 4, 5, 7, 0, tzinfo=timezone.utc)
        mock_datetime.fromisoformat = datetime.fromisoformat
        mock_datetime.strptime = datetime.strptime
        await filt._fetch_and_cache("2026-04-05")

    assert filt.feed_available is True
    assert filt.last_refresh_date == "2026-04-05"
    assert len(filt.events) == 1
    assert filt.events[0].title == "NFP"
