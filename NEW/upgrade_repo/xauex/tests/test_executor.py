"""Tests for Executor and PositionManager — mock-based, no live API needed."""

import pytest
from datetime import datetime, timezone

from bot.execution.executor import Executor, PositionManager, TrackedPosition
from bot.patterns.detector import PatternType


# ─────────────────────────────────────────────────────────
# Mock helpers
# ─────────────────────────────────────────────────────────

class MockApiClient:
    def __init__(self, order_result="pos123", raise_error=None):
        self._order_result = order_result
        self._raise = raise_error
        self.cancelled = []
        self.placed = []

    async def place_market_order(self, **kwargs):
        self.placed.append(kwargs)
        if self._raise:
            raise Exception(self._raise)
        return self._order_result

    async def place_stop_order(self, **kwargs):
        if self._raise:
            raise Exception(self._raise)
        return self._order_result

    async def cancel_order(self, order_id):
        self.cancelled.append(order_id)

    def get_current_spread(self):
        return 0.5


class MockLevelManager:
    def next_level_from(self, price, direction):
        return price + (20.0 * direction)


class MockStateWriter:
    def __init__(self):
        self.calls = []

    async def write(self, **kwargs):
        self.calls.append(kwargs)


@pytest.fixture
def config(minimal_config):
    return minimal_config


@pytest.fixture
def executor(config):
    return Executor(
        config=config,
        api_client=MockApiClient(),
        level_manager=MockLevelManager(),
    )


@pytest.fixture
def tracked_long():
    return TrackedPosition(
        position_id="p1",
        direction="LONG",
        entry_price=2720.0,
        stop_loss=2708.0,
        take_profit=2745.0,
        lot_size=0.02,
        open_time_utc=datetime.now(timezone.utc),
        pattern=PatternType.BULLISH_PIN_BAR,
        level=2720.0,
    )


@pytest.fixture
def tracked_short():
    return TrackedPosition(
        position_id="p2",
        direction="SHORT",
        entry_price=2720.0,
        stop_loss=2732.0,
        take_profit=2700.0,
        lot_size=0.02,
        open_time_utc=datetime.now(timezone.utc),
        pattern=PatternType.BEARISH_PIN_BAR,
        level=2720.0,
    )


# ─────────────────────────────────────────────────────────
# PositionManager
# ─────────────────────────────────────────────────────────

class TestPositionManager:

    def test_add_and_count(self, tracked_long):
        pm = PositionManager()
        assert pm.count() == 0
        pm.add(tracked_long)
        assert pm.count() == 1

    def test_remove(self, tracked_long):
        pm = PositionManager()
        pm.add(tracked_long)
        pm.remove("p1")
        assert pm.count() == 0

    def test_remove_nonexistent_no_error(self):
        pm = PositionManager()
        pm.remove("does_not_exist")   # should not raise

    def test_get_open_positions(self, tracked_long, tracked_short):
        pm = PositionManager()
        pm.add(tracked_long)
        pm.add(tracked_short)
        positions = pm.get_open_positions()
        assert len(positions) == 2
        ids = {p.position_id for p in positions}
        assert ids == {"p1", "p2"}


# ─────────────────────────────────────────────────────────
# SL modification guard
# ─────────────────────────────────────────────────────────

class TestSLGuard:

    def test_long_sl_moved_closer_allowed(self, executor, tracked_long):
        """Moving SL closer to entry (higher for long) → allowed."""
        assert executor.validate_sl_modification(tracked_long, 2710.0) is True

    def test_long_sl_moved_further_rejected(self, executor, tracked_long):
        """Moving SL further from entry (lower for long) → rejected."""
        assert executor.validate_sl_modification(tracked_long, 2706.0) is False

    def test_short_sl_moved_closer_allowed(self, executor, tracked_short):
        """Moving SL closer to entry (lower for short) → allowed."""
        assert executor.validate_sl_modification(tracked_short, 2730.0) is True

    def test_short_sl_moved_further_rejected(self, executor, tracked_short):
        """Moving SL further from entry (higher for short) → rejected."""
        assert executor.validate_sl_modification(tracked_short, 2735.0) is False

    def test_sl_same_value_allowed(self, executor, tracked_long):
        """SL unchanged → allowed."""
        assert executor.validate_sl_modification(tracked_long, tracked_long.stop_loss) is True


# ─────────────────────────────────────────────────────────
# Market order placement
# ─────────────────────────────────────────────────────────

class TestMarketOrder:

    async def test_observe_only_returns_none(self, config):
        """In OBSERVE_ONLY mode, no order placed → returns None."""
        config.observe_only = True
        ex = Executor(config=config, api_client=MockApiClient(), level_manager=MockLevelManager())
        result = await ex.place_market_order(
            direction=1, lot_size=0.02,
            stop_loss_price=2708.0, take_profit_price=2745.0,
            pattern=PatternType.BULLISH_PIN_BAR, level=2720.0,
        )
        assert result is None
        assert ex.api_client.placed == []

    async def test_observe_only_does_not_track_position(self, config):
        """OBSERVE_ONLY: position manager stays empty."""
        config.observe_only = True
        ex = Executor(config=config, api_client=MockApiClient(), level_manager=MockLevelManager())
        await ex.place_market_order(
            direction=1, lot_size=0.02,
            stop_loss_price=2708.0, take_profit_price=2745.0,
            pattern=PatternType.BULLISH_PIN_BAR, level=2720.0,
        )
        assert ex.position_manager.count() == 0

    async def test_live_mode_calls_api(self, config):
        """Live mode → API called → position tracked."""
        config.observe_only = False
        api = MockApiClient(order_result="pos999")
        ex = Executor(config=config, api_client=api, level_manager=MockLevelManager())
        result = await ex.place_market_order(
            direction=1, lot_size=0.02,
            stop_loss_price=2708.0, take_profit_price=2745.0,
            pattern=PatternType.BULLISH_PIN_BAR, level=2720.0,
        )
        assert result == "pos999"
        assert ex.position_manager.count() == 1

    async def test_zero_lot_returns_none(self, config):
        """lot_size=0 → returns None, no API call."""
        config.observe_only = False
        api = MockApiClient()
        ex = Executor(config=config, api_client=api, level_manager=MockLevelManager())
        result = await ex.place_market_order(
            direction=1, lot_size=0.0,
            stop_loss_price=2708.0, take_profit_price=2745.0,
            pattern=PatternType.BULLISH_PIN_BAR, level=2720.0,
        )
        assert result is None
        assert api.placed == []

    async def test_api_error_returns_none(self, config):
        """API raises exception → returns None."""
        config.observe_only = False
        api = MockApiClient(raise_error="NOT_ENOUGH_MONEY")
        ex = Executor(config=config, api_client=api, level_manager=MockLevelManager())
        result = await ex.place_market_order(
            direction=1, lot_size=0.02,
            stop_loss_price=2708.0, take_profit_price=2745.0,
            pattern=PatternType.BULLISH_PIN_BAR, level=2720.0,
        )
        assert result is None


# ─────────────────────────────────────────────────────────
# Inside bar order lifecycle
# ─────────────────────────────────────────────────────────

class TestInsideBarLifecycle:

    async def test_pending_orders_cancelled_after_configured_expiry(self, config):
        """Inside bar orders cancel once the configured expiry is reached."""
        config.observe_only = False
        api = MockApiClient(order_result="order1")
        ex = Executor(config=config, api_client=api, level_manager=MockLevelManager())

        await ex.place_inside_bar_orders(
            mother_bar_high=2730.0, mother_bar_low=2710.0,
            lot_size=0.02, level=2720.0,
            current_candle_index=5,
        )
        assert len(ex._pending_pairs) == 1

        await ex.check_pending_inside_bar_orders(7)
        assert len(ex._pending_pairs) == 1

        await ex.check_pending_inside_bar_orders(8)
        assert len(ex._pending_pairs) == 0
        assert len(api.cancelled) >= 1   # at least buy or sell cancel attempted

    async def test_pending_orders_not_cancelled_before_configured_expiry(self, config):
        """Inside bar orders remain active before the configured expiry."""
        config.observe_only = False
        api = MockApiClient(order_result="order1")
        ex = Executor(config=config, api_client=api, level_manager=MockLevelManager())

        await ex.place_inside_bar_orders(
            mother_bar_high=2730.0, mother_bar_low=2710.0,
            lot_size=0.02, level=2720.0,
            current_candle_index=5,
        )
        await ex.check_pending_inside_bar_orders(6)
        assert len(ex._pending_pairs) == 1
        assert api.cancelled == []

    async def test_pending_orders_use_configurable_expiry(self, config):
        config.observe_only = False
        config.inside_bar_expiry_candles = 4
        api = MockApiClient(order_result="order1")
        ex = Executor(config=config, api_client=api, level_manager=MockLevelManager())

        await ex.place_inside_bar_orders(
            mother_bar_high=2730.0, mother_bar_low=2710.0,
            lot_size=0.02, level=2720.0,
            current_candle_index=5,
        )
        await ex.check_pending_inside_bar_orders(8)
        assert len(ex._pending_pairs) == 1

        await ex.check_pending_inside_bar_orders(9)
        assert len(ex._pending_pairs) == 0

    async def test_inside_bar_buy_only_when_directional_bias_is_long(self, config):
        """Bullish bias should place only the BUY_STOP side."""
        config.observe_only = False
        api = MockApiClient(order_result="order1")
        ex = Executor(config=config, api_client=api, level_manager=MockLevelManager())

        buy_id, sell_id = await ex.place_inside_bar_orders(
            mother_bar_high=2730.0, mother_bar_low=2710.0,
            lot_size=0.02, level=2720.0,
            current_candle_index=5,
            allowed_direction=1,
        )

        assert buy_id == "order1"
        assert sell_id is None

    async def test_inside_bar_sell_only_when_directional_bias_is_short(self, config):
        """Bearish bias should place only the SELL_STOP side."""
        config.observe_only = False
        api = MockApiClient(order_result="order1")
        ex = Executor(config=config, api_client=api, level_manager=MockLevelManager())

        buy_id, sell_id = await ex.place_inside_bar_orders(
            mother_bar_high=2730.0, mother_bar_low=2710.0,
            lot_size=0.02, level=2720.0,
            current_candle_index=5,
            allowed_direction=-1,
        )

        assert buy_id is None
        assert sell_id == "order1"


class TestPositionClose:

    async def test_position_close_records_signed_pnl_and_state(self, config, tracked_long):
        config.observe_only = False
        writer = MockStateWriter()
        ex = Executor(config=config, api_client=MockApiClient(), level_manager=MockLevelManager(), state_writer=writer)
        ex.position_manager.add(tracked_long)

        await ex.on_position_closed(
            position_id="p1",
            close_price=2700.0,
            pnl=-20.5,
        )

        assert ex.position_manager.count() == 0
        assert ex._closed_trades_today[-1]["pnl"] == -20.5
        assert writer.calls
        assert writer.calls[-1]["closed_trades"][-1]["pnl"] == -20.5
