"""
Tests for trailing stop integration in Executor.update_trailing_stops().

Covers:
- No-op when no positions
- Breakeven SL update sent to broker
- Trail SL update sent to broker
- Hard rule: SL moving further from entry is blocked
- OBSERVE_ONLY mode: logs but doesn't call amend_position_sltp
- Failed amend: SL not updated in local state
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from bot.execution.executor import Executor, TrackedPosition, PositionManager
from bot.patterns.detector import PatternType
from bot.api.models import SymbolSpec


# ─── Helpers ─────────────────────────────────────────────────────────────────

def make_symbol_spec():
    return SymbolSpec(
        symbol="XAUUSD",
        lot_size=100.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        digits=2,
        pip_value=0.01,
    )


def make_config(observe_only=False):
    cfg = MagicMock()
    cfg.observe_only = observe_only
    cfg.sl_min_dollars = 10
    cfg.sl_max_dollars = 200
    cfg.max_lot_size = 100.0
    return cfg


def make_tracked(position_id="101", direction="LONG", entry=2700.0, sl=2680.0, lot=0.05):
    return TrackedPosition(
        position_id=position_id,
        direction=direction,
        entry_price=entry,
        stop_loss=sl,
        take_profit=2740.0,
        lot_size=lot,
        open_time_utc=datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc),
        pattern=PatternType.BULLISH_ENGULFING,
        level=entry,
    )


def make_executor(observe_only=False, amend_ok=True):
    cfg = make_config(observe_only=observe_only)
    api = MagicMock()
    api.amend_position_sltp = AsyncMock(return_value=amend_ok)
    executor = Executor(config=cfg, api_client=api, level_manager=MagicMock())
    return executor


class TestUpdateTrailingStops:
    @pytest.mark.asyncio
    async def test_no_positions_no_calls(self):
        executor = make_executor()
        spec = make_symbol_spec()
        # Should complete without error and without calling amend
        await executor.update_trailing_stops(current_price=2700.0, account_balance=10000.0, symbol_spec=spec)
        executor.api_client.amend_position_sltp.assert_not_called()

    @pytest.mark.asyncio
    async def test_in_drawdown_no_sl_change(self):
        """Price below entry for LONG → no trailing action."""
        executor = make_executor()
        spec = make_symbol_spec()
        # Entry 2700, SL 2680 → current 2695 (below entry, losing)
        pos = make_tracked(entry=2700.0, sl=2680.0)
        executor.position_manager.add(pos)

        await executor.update_trailing_stops(current_price=2695.0, account_balance=10000.0, symbol_spec=spec)
        executor.api_client.amend_position_sltp.assert_not_called()

    @pytest.mark.asyncio
    async def test_breakeven_triggers_amend(self):
        """1R+ profit on LONG → SL should move to breakeven (entry)."""
        executor = make_executor()
        spec = make_symbol_spec()
        # Entry 2700, SL 2680 → SL dist = 20, risk = 20 * 0.05 * 100 = 100
        # 1R = 20pts → price >= 2720 triggers breakeven
        pos = make_tracked(entry=2700.0, sl=2680.0, lot=0.05)
        executor.position_manager.add(pos)

        await executor.update_trailing_stops(current_price=2722.0, account_balance=10000.0, symbol_spec=spec)

        executor.api_client.amend_position_sltp.assert_called_once()
        call_kwargs = executor.api_client.amend_position_sltp.call_args.kwargs
        assert call_kwargs["position_id"] == "101"
        # New SL should be >= entry
        assert call_kwargs["stop_loss"] >= 2700.0

    @pytest.mark.asyncio
    async def test_sl_updated_in_local_state_on_success(self):
        """After successful amend, TrackedPosition.stop_loss reflects new SL."""
        executor = make_executor(amend_ok=True)
        spec = make_symbol_spec()
        pos = make_tracked(entry=2700.0, sl=2680.0, lot=0.05)
        executor.position_manager.add(pos)

        await executor.update_trailing_stops(current_price=2730.0, account_balance=10000.0, symbol_spec=spec)

        updated_pos = executor.position_manager.get_position("101")
        # SL should have moved beyond original 2680
        assert updated_pos.stop_loss > 2680.0

    @pytest.mark.asyncio
    async def test_sl_not_updated_on_failed_amend(self):
        """If amend_position_sltp returns False, local SL unchanged."""
        executor = make_executor(amend_ok=False)
        spec = make_symbol_spec()
        pos = make_tracked(entry=2700.0, sl=2680.0, lot=0.05)
        executor.position_manager.add(pos)
        original_sl = pos.stop_loss

        await executor.update_trailing_stops(current_price=2730.0, account_balance=10000.0, symbol_spec=spec)

        updated_pos = executor.position_manager.get_position("101")
        assert updated_pos.stop_loss == original_sl

    @pytest.mark.asyncio
    async def test_observe_only_does_not_send_amend(self):
        """In OBSERVE_ONLY mode, amend_position_sltp must never be called."""
        executor = make_executor(observe_only=True)
        spec = make_symbol_spec()
        pos = make_tracked(entry=2700.0, sl=2680.0, lot=0.05)
        executor.position_manager.add(pos)

        await executor.update_trailing_stops(current_price=2730.0, account_balance=10000.0, symbol_spec=spec)

        executor.api_client.amend_position_sltp.assert_not_called()

    @pytest.mark.asyncio
    async def test_hard_rule_sl_cannot_move_further(self):
        """Hard rule: SL moving further from entry is blocked even if trailing says so."""
        executor = make_executor()
        spec = make_symbol_spec()
        # Manually create a scenario where trailing would try to move SL wrong direction
        # For a LONG: current_sl = 2710 (above entry = unusual/error state)
        # Any trailing result below 2710 for a LONG would be rejected
        pos = make_tracked(entry=2700.0, sl=2710.0, lot=0.05)  # SL above entry (abnormal)
        executor.position_manager.add(pos)

        # Price is 2730 — profit, but trailing would give SL = 2730-20 = 2710 (same) or 2700 (worse)
        # The validate_sl_modification check: for LONG, new_sl < current_sl (2710) → rejected
        await executor.update_trailing_stops(current_price=2720.0, account_balance=10000.0, symbol_spec=spec)
        # amend may or may not be called depending on trailing result
        # Key: if called, must not send SL that is worse than 2710 for LONG
        for call in executor.api_client.amend_position_sltp.call_args_list:
            new_sl = call.kwargs.get("stop_loss", call.args[1] if len(call.args) > 1 else None)
            if new_sl is not None:
                assert new_sl >= 2710.0, f"SL {new_sl} would be further from entry for LONG"

    @pytest.mark.asyncio
    async def test_short_position_trailing(self):
        """SHORT: trailing should move SL downward toward price."""
        executor = make_executor()
        spec = make_symbol_spec()
        # SHORT: entry 2700, SL 2720 → sl_dist=20, risk=100
        # Price drops to 2678 → 2R profit → trail SL at 2678+20=2698 (below 2720)
        pos = make_tracked(entry=2700.0, sl=2720.0, direction="SHORT", lot=0.05)
        executor.position_manager.add(pos)

        await executor.update_trailing_stops(current_price=2678.0, account_balance=10000.0, symbol_spec=spec)

        executor.api_client.amend_position_sltp.assert_called_once()
        call_kwargs = executor.api_client.amend_position_sltp.call_args.kwargs
        # New SL for SHORT should be < original SL of 2720
        assert call_kwargs["stop_loss"] < 2720.0
