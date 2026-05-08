from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.api.models import SymbolSpec
from bot.execution.executor import Executor, TrackedPosition
from bot.patterns.detector import PatternType
from bot.risk.trailing_stop import TrailingResult


def make_symbol_spec() -> SymbolSpec:
    return SymbolSpec(
        symbol="XAUUSD",
        lot_size=100.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        digits=2,
        pip_value=0.01,
    )


def make_config():
    return MagicMock()


def make_executor(amend_ok: bool = True) -> Executor:
    api = MagicMock()
    api.amend_position_sltp = AsyncMock(return_value=amend_ok)
    api.get_current_quote = MagicMock(return_value=(None, None))
    return Executor(
        config=make_config(),
        api_client=api,
        level_manager=MagicMock(),
    )


def make_position(
    *,
    position_id: str = "101",
    direction: str = "LONG",
    entry_price: float = 2700.0,
    stop_loss: float = 2680.0,
    lot_size: float = 0.05,
) -> TrackedPosition:
    return TrackedPosition(
        position_id=position_id,
        direction=direction,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=2740.0,
        lot_size=lot_size,
        open_time_utc=datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc),
        pattern=PatternType.NONE,
        level=entry_price,
    )


@pytest.mark.asyncio
async def test_short_trailing_uses_ask_not_mid_price():
    executor = make_executor()
    spec = make_symbol_spec()
    pos = make_position(direction="SHORT", stop_loss=2720.0)
    executor.position_manager.add(pos)
    executor.api_client.get_current_quote.return_value = (2677.8, 2678.2)

    await executor.update_trailing_stops(
        current_price=2678.0,
        account_balance=10_000.0,
        symbol_spec=spec,
    )

    executor.api_client.amend_position_sltp.assert_called_once()
    assert executor.api_client.amend_position_sltp.call_args.kwargs["stop_loss"] == 2698.2


@pytest.mark.asyncio
async def test_market_guard_rejects_short_stop_inside_live_ask():
    executor = make_executor()
    spec = make_symbol_spec()
    pos = make_position(direction="SHORT", stop_loss=2720.0)
    executor.position_manager.add(pos)
    executor.api_client.get_current_quote.return_value = (2698.0, 2698.1)

    with patch(
        "bot.risk.trailing_stop.evaluate_trailing_stop",
        return_value=TrailingResult(new_sl=2698.1, reason="trail"),
    ):
        await executor.update_trailing_stops(
            current_price=2698.0,
            account_balance=10_000.0,
            symbol_spec=spec,
        )

    executor.api_client.amend_position_sltp.assert_not_called()


@pytest.mark.asyncio
async def test_market_guard_rejects_long_stop_above_live_bid():
    executor = make_executor()
    spec = make_symbol_spec()
    pos = make_position(direction="LONG", stop_loss=2680.0)
    executor.position_manager.add(pos)
    executor.api_client.get_current_quote.return_value = (2701.2, 2701.4)

    with patch(
        "bot.risk.trailing_stop.evaluate_trailing_stop",
        return_value=TrailingResult(new_sl=2701.2, reason="trail"),
    ):
        await executor.update_trailing_stops(
            current_price=2701.3,
            account_balance=10_000.0,
            symbol_spec=spec,
        )

    executor.api_client.amend_position_sltp.assert_not_called()
