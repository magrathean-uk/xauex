# ruff: noqa: E402
"""Asymmetric news blackout: full pre-event block, short post-event block."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from xauex.bot.filters.news import NewsEvent, NewsFilter, is_news_clear


_EVENT_TIME = datetime(2026, 6, 11, 12, 30, tzinfo=timezone.utc)
_EVENTS = [NewsEvent(title="CPI m/m", currency="USD", impact="HIGH", time_utc=_EVENT_TIME)]


def _at(minutes_from_event: int) -> datetime:
    return _EVENT_TIME + timedelta(minutes=minutes_from_event)


def test_pure_function_stays_symmetric_without_overrides():
    assert is_news_clear(_at(-31), _EVENTS, 30) is True
    assert is_news_clear(_at(-29), _EVENTS, 30) is False
    assert is_news_clear(_at(29), _EVENTS, 30) is False
    assert is_news_clear(_at(31), _EVENTS, 30) is True


def test_pure_function_asymmetric_window():
    kwargs = {"block_minutes_before": 30, "block_minutes_after": 10}
    assert is_news_clear(_at(-29), _EVENTS, 30, **kwargs) is False
    assert is_news_clear(_at(9), _EVENTS, 30, **kwargs) is False
    assert is_news_clear(_at(11), _EVENTS, 30, **kwargs) is True
    assert is_news_clear(_at(-31), _EVENTS, 30, **kwargs) is True


def _filter_with(config) -> NewsFilter:
    news_filter = NewsFilter(config)
    news_filter.events = list(_EVENTS)
    news_filter.feed_available = True
    return news_filter


def test_filter_uses_asymmetric_config():
    config = SimpleNamespace(
        news_block_minutes=30,
        news_block_minutes_before=30,
        news_block_minutes_after=10,
        news_block_currencies=None,
        state_file_path="/tmp/xauex_state.json",
    )
    news_filter = _filter_with(config)
    clear_before, reason_before = news_filter.is_clear(_at(-15))
    assert clear_before is False and reason_before == "CPI m/m"
    clear_inside_after, _ = news_filter.is_clear(_at(9))
    assert clear_inside_after is False
    clear_past_after, reason_past = news_filter.is_clear(_at(12))
    assert clear_past_after is True and reason_past is None


def test_filter_falls_back_to_legacy_symmetric_block():
    config = SimpleNamespace(
        news_block_minutes=30,
        news_block_currencies=None,
        state_file_path="/tmp/xauex_state.json",
    )
    news_filter = _filter_with(config)
    clear, _ = news_filter.is_clear(_at(20))
    assert clear is False
    clear_after, _ = news_filter.is_clear(_at(31))
    assert clear_after is True
