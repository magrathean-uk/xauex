"""Tests for session and news filters — all spec cases from 06/07-FILTER.md."""

import pytest
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from bot.filters.session import SessionFilter
from bot.filters.news import NewsFilter, NewsEvent, parse_ff_datetime, parse_ff_time, is_news_clear
from bot.filters.trend import TrendFilter, calculate_ema


LONDON = ZoneInfo("Europe/London")
UTC = timezone.utc


def london_dt(year, month, day, hour, minute=0) -> datetime:
    """Create a UTC datetime from a London local time."""
    naive = datetime(year, month, day, hour, minute)
    london_aware = naive.replace(tzinfo=LONDON)
    return london_aware.astimezone(UTC)


@pytest.fixture
def sf():
    return SessionFilter()


class TestSessionFilter:
    """Session filter tests — all boundary cases from 07-SESSION-FILTER.md."""

    def test_monday_09_00_london_tradeable(self, sf):
        """Monday 09:00 London → (True, 'OK')"""
        dt = london_dt(2026, 3, 9, 9, 0)   # Monday March 9 2026
        ok, reason = sf.is_tradeable(dt)
        assert ok is True and reason == "OK"

    def test_monday_07_59_outside_session(self, sf):
        """Monday 07:59 London → (False, 'OUTSIDE_SESSION')"""
        dt = london_dt(2026, 3, 9, 7, 59)
        ok, reason = sf.is_tradeable(dt)
        assert ok is False and reason == "OUTSIDE_SESSION"

    def test_monday_08_00_boundary_permitted(self, sf):
        """Monday 08:00 London → (True, 'OK') — boundary is permitted"""
        dt = london_dt(2026, 3, 9, 8, 0)
        ok, reason = sf.is_tradeable(dt)
        assert ok is True and reason == "OK"

    def test_monday_17_00_boundary_not_permitted(self, sf):
        """Monday 17:00 London → (False, 'OUTSIDE_SESSION') — boundary not permitted"""
        dt = london_dt(2026, 3, 9, 17, 0)
        ok, reason = sf.is_tradeable(dt)
        assert ok is False and reason == "OUTSIDE_SESSION"

    def test_monday_16_59_tradeable(self, sf):
        """Monday 16:59 London → (True, 'OK') — within extended window"""
        dt = london_dt(2026, 3, 9, 16, 59)
        ok, reason = sf.is_tradeable(dt)
        assert ok is True and reason == "OK"

    def test_monday_12_00_now_tradeable(self, sf):
        """Monday 12:00 London → (True, 'OK') — now within extended London+NY window"""
        dt = london_dt(2026, 3, 9, 12, 0)
        ok, reason = sf.is_tradeable(dt)
        assert ok is True and reason == "OK"

    def test_friday_15_59_tradeable(self, sf):
        """Friday 15:59 London → (True, 'OK')"""
        dt = london_dt(2026, 3, 13, 15, 59)  # Friday March 13
        ok, reason = sf.is_tradeable(dt)
        assert ok is True and reason == "OK"

    def test_friday_16_00_cutoff(self, sf):
        """Friday 16:00 London → (False, 'FRIDAY_CUTOFF') — boundary not permitted"""
        dt = london_dt(2026, 3, 13, 16, 0)
        ok, reason = sf.is_tradeable(dt)
        assert ok is False and reason == "FRIDAY_CUTOFF"

    def test_friday_10_00_tradeable(self, sf):
        """Friday 10:00 London → (True, 'OK')"""
        dt = london_dt(2026, 3, 13, 10, 0)
        ok, reason = sf.is_tradeable(dt)
        assert ok is True and reason == "OK"

    def test_saturday_10_00_weekend(self, sf):
        """Saturday 10:00 London → (False, 'WEEKEND')"""
        dt = london_dt(2026, 3, 14, 10, 0)  # Saturday
        ok, reason = sf.is_tradeable(dt)
        assert ok is False and reason == "WEEKEND"

    def test_sunday_10_00_weekend(self, sf):
        """Sunday 10:00 London → (False, 'WEEKEND')"""
        dt = london_dt(2026, 3, 15, 10, 0)  # Sunday
        ok, reason = sf.is_tradeable(dt)
        assert ok is False and reason == "WEEKEND"

    def test_dst_spring_forward(self, sf):
        """DST spring-forward (Mar 29 2026 UK clocks go forward). 08:00 London = 07:00 UTC."""
        # In 2026 UK clocks go forward on March 29 at 01:00 (BST starts)
        # 08:00 London on March 30 = 07:00 UTC (BST = UTC+1)
        dt_utc = datetime(2026, 3, 30, 7, 0, tzinfo=UTC)  # 08:00 BST = tradeable
        ok, reason = sf.is_tradeable(dt_utc)
        assert ok is True and reason == "OK"

    def test_dst_autumn_fallback(self, sf):
        """DST autumn fallback. 08:00 London = 08:00 UTC (GMT = UTC+0)."""
        # In 2026 UK clocks go back on October 25 at 02:00
        # 08:00 London on October 26 = 08:00 UTC (GMT)
        dt_utc = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)  # 08:00 GMT = tradeable
        ok, reason = sf.is_tradeable(dt_utc)
        assert ok is True and reason == "OK"


class TestNewsFilter:
    """News filter tests — all cases from 06-NEWS-FILTER.md."""

    def _make_usd_high(self, time_utc: datetime) -> NewsEvent:
        return NewsEvent(title="NFP", currency="USD", impact="HIGH", time_utc=time_utc)

    def _filter_with_events(self, events, now_utc, block=30):
        """Helper: use pure is_news_clear function."""
        return is_news_clear(now_utc, events, block)

    EVENT_TIME = datetime(2026, 3, 6, 13, 30, tzinfo=UTC)   # 13:30 UTC

    def test_29min_before_blocked(self):
        """Event at 13:30, check at 13:01 → blocked (29min inside window)"""
        now = self.EVENT_TIME - timedelta(minutes=29)
        event = self._make_usd_high(self.EVENT_TIME)
        assert self._filter_with_events([event], now) is False

    def test_30min_before_blocked_boundary_inclusive(self):
        """Event at 13:30, check at 13:00 → blocked (30min, boundary inclusive)"""
        now = self.EVENT_TIME - timedelta(minutes=30)
        event = self._make_usd_high(self.EVENT_TIME)
        assert self._filter_with_events([event], now) is False

    def test_31min_before_clear(self):
        """Event at 13:30, check at 12:59 → clear (31min before)"""
        now = self.EVENT_TIME - timedelta(minutes=31)
        event = self._make_usd_high(self.EVENT_TIME)
        assert self._filter_with_events([event], now) is True

    def test_30min_after_blocked_boundary_inclusive(self):
        """Event at 13:30, check at 14:00 → blocked (30min after, boundary inclusive)"""
        now = self.EVENT_TIME + timedelta(minutes=30)
        event = self._make_usd_high(self.EVENT_TIME)
        assert self._filter_with_events([event], now) is False

    def test_31min_after_clear(self):
        """Event at 13:30, check at 14:01 → clear (31min after)"""
        now = self.EVENT_TIME + timedelta(minutes=31)
        event = self._make_usd_high(self.EVENT_TIME)
        assert self._filter_with_events([event], now) is True

    def test_non_usd_high_does_not_block(self):
        """Non-USD high-impact event → does not block"""
        event = NewsEvent(title="EUR event", currency="EUR", impact="HIGH", time_utc=self.EVENT_TIME)
        now = self.EVENT_TIME
        assert self._filter_with_events([event], now) is True

    def test_usd_medium_does_not_block(self):
        """USD medium-impact event → does not block"""
        event = NewsEvent(title="USD medium", currency="USD", impact="MEDIUM", time_utc=self.EVENT_TIME)
        now = self.EVENT_TIME
        assert self._filter_with_events([event], now) is True

    def test_empty_calendar_clear(self):
        """Empty calendar → always clear"""
        assert self._filter_with_events([], datetime.now(UTC)) is True

    def test_dst_winter_et_to_utc(self):
        """DST winter: ET 08:30 → UTC 13:30 (ET = UTC-5 in winter)"""
        # February 2026 — ET is UTC-5 (no DST)
        result = parse_ff_time("02-06-2026", "8:30am")
        assert result.hour == 13
        assert result.minute == 30
        assert result.tzinfo == UTC

    def test_dst_summer_et_to_utc(self):
        """DST summer: ET 08:30 → UTC 12:30 (EDT = UTC-4 in summer)"""
        # June 2026 — EDT is UTC-4
        result = parse_ff_time("06-05-2026", "8:30am")
        assert result.hour == 12
        assert result.minute == 30
        assert result.tzinfo == UTC

    def test_iso_datetime_parses_to_utc(self):
        result = parse_ff_datetime("2026-03-15T17:30:00-04:00")
        assert result.hour == 21
        assert result.minute == 30
        assert result.tzinfo == UTC

    @pytest.mark.asyncio
    async def test_fetch_failure_uses_cached_calendar(self, tmp_path):
        class Cfg:
            news_block_minutes = 30
            state_file_path = str(tmp_path / "state.json")

        nf = NewsFilter(Cfg())
        nf.events = [NewsEvent(title="NFP", currency="USD", impact="HIGH", time_utc=self.EVENT_TIME)]
        nf._save_cache("2026-03-17")
        nf.events = []
        nf.last_refresh_date = None

        async def fail_session():
            raise RuntimeError("rate limited")

        nf._get_session = fail_session

        await nf._fetch_and_cache("2026-03-17")

        assert len(nf.events) == 1
        assert nf.last_refresh_date == "2026-03-17"

    @pytest.mark.asyncio
    async def test_fetch_failure_without_cache_blocks_trading(self, tmp_path):
        class Cfg:
            news_block_minutes = 30
            state_file_path = str(tmp_path / "state.json")

        nf = NewsFilter(Cfg())

        async def fail_session():
            raise RuntimeError("rate limited")

        nf._get_session = fail_session

        await nf._fetch_and_cache("2026-03-17")
        assert nf.feed_available is False
        ok, reason = nf.is_clear(datetime.now(UTC))
        assert ok is False
        assert reason == "NEWS_FEED_UNAVAILABLE"


class TestTrendFilter:
    def test_calculate_ema_returns_none_if_insufficient_data(self):
        assert calculate_ema([1.0, 2.0], 3) is None

    def test_calculate_ema_uses_sma_seed(self):
        assert calculate_ema([1.0, 2.0, 3.0, 4.0, 5.0], 3) == 4.0

    def test_long_requires_daily_and_h1_bullish_alignment(self):
        tf = TrendFilter()
        daily = list(range(100, 140))
        h1 = list(range(100, 340))
        ok, reason, snapshot = tf.evaluate(direction=1, daily_closes=daily, execution_closes=h1)
        assert ok is True
        assert reason == "OK"
        assert snapshot is not None
        assert snapshot.daily_ema_8 > snapshot.daily_ema_21
        assert snapshot.exec_ema_50 > snapshot.exec_ema_200

    def test_long_rejected_when_h1_bearish(self):
        tf = TrendFilter()
        daily = list(range(100, 140))
        h1 = list(range(340, 100, -1))
        ok, reason, _ = tf.evaluate(direction=1, daily_closes=daily, execution_closes=h1)
        assert ok is False
        assert reason == "EXEC_EMA_SELL_ONLY"

    def test_short_requires_daily_and_h1_bearish_alignment(self):
        tf = TrendFilter()
        daily = list(range(140, 100, -1))
        h1 = list(range(340, 100, -1))
        ok, reason, snapshot = tf.evaluate(direction=-1, daily_closes=daily, execution_closes=h1)
        assert ok is True
        assert reason == "OK"
        assert snapshot is not None
        assert snapshot.daily_ema_21 > snapshot.daily_ema_8
        assert snapshot.exec_ema_200 > snapshot.exec_ema_50

    def test_inside_bar_bias_returns_bullish_direction(self):
        tf = TrendFilter()
        daily = list(range(100, 140))
        h1 = list(range(100, 340))
        bias, reason, _ = tf.aligned_bias(daily_closes=daily, execution_closes=h1)
        assert bias == 1
        assert reason == "OK"
