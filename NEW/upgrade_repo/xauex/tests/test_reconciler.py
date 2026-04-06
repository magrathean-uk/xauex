"""Tests for bot/api/reconciler.py — mock-based, no live API needed."""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from bot.api.reconciler import reconcile_positions
from bot.api.models import Position
from bot.execution.executor import PositionManager, TrackedPosition
from bot.patterns.detector import PatternType


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_position(pid: str, direction: str = "LONG", volume: float = 0.05) -> Position:
    return Position(
        position_id=pid,
        symbol="XAUUSD",
        direction=direction,
        volume=volume,
        entry_price=2700.0,
        current_price=2700.0,
        unrealised_pnl=0.0,
        open_time=datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc),
        stop_loss=2680.0,
        take_profit=2740.0,
    )


def _make_tracked(pid: str, direction: str = "LONG") -> TrackedPosition:
    return TrackedPosition(
        position_id=pid,
        direction=direction,
        entry_price=2700.0,
        stop_loss=2680.0,
        take_profit=2740.0,
        lot_size=0.05,
        open_time_utc=datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc),
        pattern=PatternType.BULLISH_ENGULFING,
        level=2700.0,
    )


def _make_api(positions):
    api = MagicMock()
    api.reconcile = AsyncMock(return_value=(positions, []))
    return api


def _make_pm(*tracked_positions):
    pm = PositionManager()
    for tp in tracked_positions:
        pm.add(tp)
    return pm


# ─── Tests ───────────────────────────────────────────────────────────────────

class TestReconcilePositions:
    @pytest.mark.asyncio
    async def test_empty_both_sides(self):
        """No broker positions, no local positions → nothing changes."""
        api = _make_api([])
        pm  = _make_pm()
        await reconcile_positions(api, pm)
        assert pm.count() == 0

    @pytest.mark.asyncio
    async def test_orphan_added_from_broker(self):
        """Broker has a position not in local state → added."""
        broker_pos = _make_position("999")
        api = _make_api([broker_pos])
        pm  = _make_pm()   # empty

        await reconcile_positions(api, pm)

        assert pm.count() == 1
        assert pm.get_position("999") is not None

    @pytest.mark.asyncio
    async def test_ghost_removed_from_local(self):
        """Local has a position broker doesn't know → removed."""
        api = _make_api([])  # broker: empty
        pm  = _make_pm(_make_tracked("888"))

        await reconcile_positions(api, pm)

        assert pm.count() == 0
        assert pm.get_position("888") is None

    @pytest.mark.asyncio
    async def test_matching_position_stays(self):
        """Position exists in both broker and local → count unchanged."""
        broker_pos = _make_position("101")
        api = _make_api([broker_pos])
        pm  = _make_pm(_make_tracked("101"))

        await reconcile_positions(api, pm)

        assert pm.count() == 1
        assert pm.get_position("101") is not None

    @pytest.mark.asyncio
    async def test_mixed_scenario(self):
        """
        Broker: [A, B]   Local: [B, C]
        Expected: A added, C removed, B stays.
        """
        broker_positions = [_make_position("A"), _make_position("B")]
        api = _make_api(broker_positions)
        pm  = _make_pm(_make_tracked("B"), _make_tracked("C"))

        await reconcile_positions(api, pm)

        assert pm.count() == 2
        assert pm.get_position("A") is not None
        assert pm.get_position("B") is not None
        assert pm.get_position("C") is None

    @pytest.mark.asyncio
    async def test_direction_preserved_for_orphan(self):
        """Orphan position from broker should carry over its direction."""
        broker_pos = _make_position("42", direction="SHORT")
        api = _make_api([broker_pos])
        pm  = _make_pm()

        await reconcile_positions(api, pm)

        pos = pm.get_position("42")
        assert pos is not None
        assert pos.direction == "SHORT"

    @pytest.mark.asyncio
    async def test_volume_preserved_for_orphan(self):
        """Orphan position volume should carry from broker Position.volume."""
        broker_pos = _make_position("77", volume=0.1)
        api = _make_api([broker_pos])
        pm  = _make_pm()

        await reconcile_positions(api, pm)

        pos = pm.get_position("77")
        assert pos is not None
        assert abs(pos.lot_size - 0.1) < 1e-9

    @pytest.mark.asyncio
    async def test_reconcile_called_once(self):
        """reconcile() on the api should be called exactly once."""
        api = _make_api([])
        pm  = _make_pm()

        await reconcile_positions(api, pm)

        api.reconcile.assert_called_once()
