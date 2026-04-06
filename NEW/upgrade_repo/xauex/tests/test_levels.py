"""Tests for HTF level management — all spec cases from 10-TESTING-VALIDATION.md."""

import pytest
from datetime import datetime, timezone
from bot.levels.htf_levels import LevelManager, HTFLevels


@pytest.fixture
def config():
    class Cfg:
        level_proximity_dollars = 3.0
    return Cfg()


def make_bar(open_time, o, h, l, c):
    return {"open_time": open_time, "open": o, "high": h, "low": l, "close": c}


class MockApiClient:
    """Configurable mock for API trendbars."""

    def __init__(self, daily_bars=None, monthly_bars=None, weekly_bars=None):
        self._daily = daily_bars or []
        self._monthly = monthly_bars or []
        self._weekly = weekly_bars or []

    async def get_trendbar(self, period, count):
        if period == "D1":
            return self._daily
        if period == "MONTHLY":
            return self._monthly
        return self._weekly


T1 = datetime(2026, 3, 2, 0, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc)
T_OLDER = datetime(2026, 2, 23, 0, 0, tzinfo=timezone.utc)

# API never returns the forming bar; both bars here are "closed".
# bars[-1] = most recently closed (signal, used for levels).
# bars[-2] = previous closed bar (not used by level code, just to satisfy len >= 2 check).
VALID_MN = [
    make_bar(datetime(2026, 2, 2, 0, 0, tzinfo=timezone.utc), 2680, 2750, 2610, 2740),  # index -2 (prev)
    make_bar(datetime(2026, 3, 2, 0, 0, tzinfo=timezone.utc), 2700, 2760, 2650, 2720),  # index -1 (signal)
]
VALID_D1 = [
    make_bar(datetime(2026, 3, 17, 0, 0, tzinfo=timezone.utc), 2715, 2750, 2705, 2742),
    make_bar(datetime(2026, 3, 18, 0, 0, tzinfo=timezone.utc), 2742, 2764, 2728, 2758),
]
VALID_WK = [
    make_bar(T1, 2720, 2745, 2700, 2735),  # index -2 (prev)
    make_bar(T2, 2730, 2748, 2710, 2740),  # index -1 (signal)
]


@pytest.fixture
def manager_with_levels(config):
    """Level manager pre-loaded with valid levels (sourced from VALID_WK[-1] = T2 bar)."""
    api = MockApiClient(daily_bars=VALID_D1, monthly_bars=VALID_MN, weekly_bars=VALID_WK)
    mgr = LevelManager(config, api)
    mgr._raw = HTFLevels(
        day_open=2742,
        day_high=2764,
        day_low=2728,
        day_close=2758,
        mn_open=2700, mn_high=2760, mn_low=2650, mn_close=2720,  # from VALID_MN[-1]
        wk_open=2730, wk_high=2748, wk_low=2710, wk_close=2740,  # from VALID_WK[-1]
        last_refresh_utc=datetime.now(timezone.utc),
        weekly_bar_open_time_utc=T2,   # open_time of VALID_WK[-1]
    )
    mgr._deduped_levels = mgr._deduplicate(mgr._raw_values(mgr._raw))
    return mgr


class TestProximity:

    def test_price_within_threshold_returns_level(self, manager_with_levels):
        """Price within 3.0 of level → returns that level."""
        # VALID_WK[-1]: WK_O = 2730. Price 2731.5 → diff = 1.5 ≤ 3.0
        result = manager_with_levels.price_at_level(2731.5)
        assert result is not None
        assert abs(result - 2730.0) < 1e-6

    def test_price_outside_threshold_returns_none(self, manager_with_levels):
        """Price > 3.0 away from all levels → None."""
        result = manager_with_levels.price_at_level(2800.0)  # far from everything
        assert result is None

    def test_levels_near_range_returns_all_touched_levels(self, manager_with_levels):
        result = manager_with_levels.levels_near_range(2728.0, 2741.0)
        assert 2730.0 in result
        assert 2740.0 in result
        assert 2728.0 in result

    def test_levels_near_range_returns_empty_when_untouched(self, manager_with_levels):
        assert manager_with_levels.levels_near_range(2800.0, 2810.0) == []


class TestDeduplication:

    def test_levels_within_one_dollar_merged(self, config):
        """Two levels within $1.0 → merged to average."""
        mgr = LevelManager(config, MockApiClient())
        levels = mgr._deduplicate([2720.0, 2720.5])  # diff = 0.5 ≤ 1.0
        assert len(levels) == 1
        assert abs(levels[0] - 2720.25) < 1e-9

    def test_levels_beyond_one_dollar_retained(self, config):
        """Two levels > $1.0 apart → retained separately."""
        mgr = LevelManager(config, MockApiClient())
        levels = mgr._deduplicate([2720.0, 2721.5])  # diff = 1.5 > 1.0
        assert len(levels) == 2

    def test_three_close_levels_merged_iteratively(self, config):
        """Three levels within 1.0 chain → all merged."""
        mgr = LevelManager(config, MockApiClient())
        levels = mgr._deduplicate([2720.0, 2720.5, 2720.9])
        assert len(levels) == 1


class TestNextLevel:

    def test_next_level_above_for_long_tp(self, manager_with_levels):
        """direction=+1 → nearest level above price."""
        # Price 2722.0, levels include 2735, 2745, 2750
        result = manager_with_levels.next_level_from(2722.0, direction=1)
        assert result is not None
        assert result > 2722.0

    def test_next_level_below_for_short_tp(self, manager_with_levels):
        """direction=-1 → nearest level below price."""
        result = manager_with_levels.next_level_from(2722.0, direction=-1)
        assert result is not None
        assert result < 2722.0

    def test_no_level_above_returns_none(self, manager_with_levels):
        """Price above all levels → None for long TP."""
        result = manager_with_levels.next_level_from(9000.0, direction=1)
        assert result is None

    def test_no_level_below_returns_none(self, manager_with_levels):
        """Price below all levels → None for short TP."""
        result = manager_with_levels.next_level_from(100.0, direction=-1)
        assert result is None


class TestValidation:

    def test_invalid_ohlc_rejected(self, config):
        """Invalid OHLC (MN_H < MN_L) on signal bar (-1) → validation fails, previous retained."""
        api = MockApiClient(
            daily_bars=VALID_D1,
            monthly_bars=[
                make_bar(T1, 2680, 2750, 2610, 2740),   # prev bar (-2) — valid
                make_bar(T2, 2700, 2600, 2750, 2720),   # signal bar (-1) — H < L → invalid!
            ],
            weekly_bars=VALID_WK,
        )
        mgr = LevelManager(config, api)
        # Set some previous valid levels
        mgr._raw = HTFLevels(
            day_open=2715, day_high=2750, day_low=2705, day_close=2742,
            mn_open=2680, mn_high=2750, mn_low=2610, mn_close=2740,
            wk_open=2720, wk_high=2745, wk_low=2700, wk_close=2735,
            last_refresh_utc=datetime.now(timezone.utc),
            weekly_bar_open_time_utc=T1,
        )
        original_raw = mgr._raw

        import asyncio
        asyncio.run(mgr.refresh())

        # Previous levels retained unchanged
        assert mgr._raw is original_raw

    def test_valid_ohlc_accepted(self, config):
        """Valid OHLC within range → accepted and stored."""
        api = MockApiClient(daily_bars=VALID_D1, monthly_bars=VALID_MN, weekly_bars=VALID_WK)
        mgr = LevelManager(config, api)

        import asyncio
        asyncio.run(mgr.refresh())

        assert mgr._raw is not None
        assert mgr._raw.day_high == 2764.0
        assert mgr._raw.day_low == 2728.0
        # VALID_MN[-1] is the signal bar: open=2700, high=2760, low=2650, close=2720
        assert mgr._raw.mn_high == 2760.0
        assert mgr._raw.mn_low == 2650.0


class TestRefreshTrigger:

    def test_refresh_on_different_weekly_bar_time(self, config):
        """Different weekly bar open_time at index -1 → refresh performed → returns True."""
        # API returns a new week bar at -1 (open_time = T2), stored time is T1 → refresh
        api = MockApiClient(
            daily_bars=VALID_D1,
            monthly_bars=VALID_MN,
            weekly_bars=[
                make_bar(T1, 2720, 2745, 2700, 2735),   # index -2
                make_bar(T2, 2730, 2748, 2710, 2740),   # index -1 → new open_time T2
            ],
        )
        mgr = LevelManager(config, api)
        mgr._raw = HTFLevels(
            day_open=2715, day_high=2750, day_low=2705, day_close=2742,
            mn_open=2680, mn_high=2750, mn_low=2610, mn_close=2740,
            wk_open=2720, wk_high=2745, wk_low=2700, wk_close=2735,
            last_refresh_utc=datetime.now(timezone.utc),
            weekly_bar_open_time_utc=T1,   # old stored time
        )

        import asyncio
        refreshed = asyncio.run(mgr.refresh_if_needed())
        assert refreshed is True
        assert mgr._raw.weekly_bar_open_time_utc == T2

    def test_no_refresh_when_same_bar_time(self, config):
        """Same weekly bar open_time at index -1 → no refresh → returns False."""
        # API returns weekly[-1] with open_time T1 — same as stored → no refresh
        api = MockApiClient(
            daily_bars=VALID_D1,
            monthly_bars=VALID_MN,
            weekly_bars=[
                make_bar(T_OLDER, 2710, 2735, 2690, 2725),  # index -2
                make_bar(T1, 2720, 2745, 2700, 2735),        # index -1 = same as stored T1
            ],
        )
        mgr = LevelManager(config, api)
        mgr._raw = HTFLevels(
            day_open=2715, day_high=2750, day_low=2705, day_close=2742,
            mn_open=2680, mn_high=2750, mn_low=2610, mn_close=2740,
            wk_open=2720, wk_high=2745, wk_low=2700, wk_close=2735,
            last_refresh_utc=datetime.now(timezone.utc),
            weekly_bar_open_time_utc=T1,  # same as weekly[-1] open_time
        )

        import asyncio
        refreshed = asyncio.run(mgr.refresh_if_needed())
        assert refreshed is False
