"""
Tests for Watchdog (bot/watchdog.py) — mock-based.

Covers: no-op when connected, reconnect triggered on disconnect,
        reconcile called after reconnect, exponential backoff capped.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.watchdog import Watchdog, _INITIAL_BACKOFF, _BACKOFF_FACTOR, _MAX_BACKOFF


# ─── Helpers ─────────────────────────────────────────────────────────────────

def make_orchestrator(connected=True):
    orc = MagicMock()
    orc.running = True
    orc.config.observe_only = True
    orc.last_tick_time = 0.0
    orc.api_client = MagicMock()
    orc.api_client.is_connected.return_value = connected
    orc.api_client.connect      = AsyncMock()
    orc.api_client.disconnect   = AsyncMock()
    orc.api_client.subscribe_ticks = AsyncMock()
    orc.on_tick     = AsyncMock()
    orc.executor    = MagicMock()
    orc.executor.position_manager = MagicMock()
    orc.executor.position_manager.count.return_value = 0
    orc.session_filter = MagicMock()
    orc.session_filter.is_tradeable.return_value = (True, "OK")
    orc.set_status  = MagicMock()
    orc.write_state = AsyncMock()
    return orc


def make_watchdog(orc, **kwargs):
    w = Watchdog(orc, **kwargs)
    w._sleep = AsyncMock()   # instant sleep — no real delay
    return w


# ─── Tests ───────────────────────────────────────────────────────────────────

class TestWatchdog:
    @pytest.mark.asyncio
    async def test_no_reconnect_when_connected(self):
        """Watchdog should not trigger reconnect if connection is healthy."""
        orc = make_orchestrator(connected=True)
        w   = make_watchdog(orc)

        # Run exactly one iteration: sleep → check → exit
        async def one_iteration():
            orc.running = False   # stop after first sleep

        w._sleep = AsyncMock(side_effect=lambda _: asyncio.create_task(
            asyncio.coroutine(lambda: None)()  # no-op coro
        ) and asyncio.sleep(0))

        # Simpler approach: patch _sleep to stop loop after first call
        call_count = 0

        async def controlled_sleep(_):
            nonlocal call_count
            call_count += 1
            orc.running = False  # stop the loop after first sleep

        w._sleep = controlled_sleep
        await w.run()

        orc.api_client.connect.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconnect_called_on_disconnect(self):
        """When is_connected() returns False, connect() should be called."""
        orc = make_orchestrator(connected=False)
        w   = make_watchdog(orc)

        connect_calls = 0

        async def mock_connect():
            nonlocal connect_calls
            connect_calls += 1
            orc.api_client.is_connected.return_value = True
            orc.running = False  # stop after first successful reconnect

        orc.api_client.connect.side_effect = mock_connect

        # First sleep (check interval) lets the loop proceed to reconnect
        # Subsequent sleeps (backoff) are instant
        sleep_count = [0]

        async def controlled_sleep(_):
            sleep_count[0] += 1

        w._sleep = controlled_sleep
        await w.run()

        assert connect_calls >= 1
        assert w.reconnect_count >= 1

    @pytest.mark.asyncio
    async def test_reconcile_called_after_reconnect(self):
        """After successful reconnect, reconcile_positions should be called."""
        orc = make_orchestrator(connected=False)
        w   = make_watchdog(orc)

        async def mock_connect():
            orc.api_client.is_connected.return_value = True
            orc.running = False

        orc.api_client.connect.side_effect = mock_connect

        with patch("bot.api.reconciler.reconcile_positions", new=AsyncMock()) as mock_reconcile, \
             patch("bot.watchdog.reconcile_positions", new=AsyncMock()) as mock_reconcile2:
            await w.run()
            # Either the direct mock or the module-level one should be called
            assert mock_reconcile.called or mock_reconcile2.called

    @pytest.mark.asyncio
    async def test_subscribe_ticks_after_reconnect(self):
        """Tick subscription is re-established after reconnect."""
        orc = make_orchestrator(connected=False)
        w   = make_watchdog(orc)

        async def mock_connect():
            orc.api_client.is_connected.return_value = True
            orc.running = False

        orc.api_client.connect.side_effect = mock_connect

        with patch("bot.watchdog.reconcile_positions", new=AsyncMock()):
            await w.run()

        orc.api_client.subscribe_ticks.assert_called_once_with("XAUUSD", orc.on_tick)

    @pytest.mark.asyncio
    async def test_backoff_increases_on_repeated_failure(self):
        """Backoff sleep duration should grow with each failed reconnect attempt."""
        orc = make_orchestrator(connected=False)
        w   = make_watchdog(orc, max_backoff=300.0)

        fail_count  = [0]
        sleep_calls = []
        in_reconnect = [False]

        async def mock_connect():
            fail_count[0] += 1
            in_reconnect[0] = True
            if fail_count[0] >= 4:
                orc.running = False
            raise ConnectionError("refused")

        async def tracked_sleep(duration):
            sleep_calls.append(duration)

        orc.api_client.connect.side_effect = mock_connect
        w._sleep = tracked_sleep

        await w.run()

        # First sleep is check-interval (_CHECK_INTERVAL=10); skip it.
        # Remaining sleeps are backoff: 5, 10, 20, 40...
        backoff_sleeps = sleep_calls[1:]  # drop check-interval sleep
        assert len(backoff_sleeps) >= 2, "Expected at least 2 backoff sleeps"
        for i in range(1, len(backoff_sleeps)):
            assert backoff_sleeps[i] >= backoff_sleeps[i - 1], \
                f"Backoff didn't increase: {backoff_sleeps}"

    @pytest.mark.asyncio
    async def test_backoff_cap_applied(self):
        """Backoff should never exceed max_backoff."""
        orc = make_orchestrator(connected=False)
        max_b = 30.0
        w   = make_watchdog(orc, max_backoff=max_b)

        fail_count  = [0]
        sleep_calls = []

        async def mock_connect():
            fail_count[0] += 1
            if fail_count[0] >= 8:
                orc.running = False
            raise ConnectionError("refused")

        async def tracked_sleep(duration):
            sleep_calls.append(duration)

        orc.api_client.connect.side_effect = mock_connect
        w._sleep = tracked_sleep

        await w.run()

        backoff_sleeps = [d for d in sleep_calls if d > 1]
        if backoff_sleeps:
            assert max(backoff_sleeps) <= max_b, \
                f"Backoff exceeded max: {max(backoff_sleeps)}"

    @pytest.mark.asyncio
    async def test_reconnect_count_tracks_successes(self):
        """reconnect_count only increments on successful reconnects."""
        orc = make_orchestrator(connected=False)
        w   = make_watchdog(orc)

        calls = [0]

        async def mock_connect():
            calls[0] += 1
            if calls[0] == 1:
                raise ConnectionError("first attempt fails")
            # Second attempt succeeds
            orc.api_client.is_connected.return_value = True
            orc.running = False

        orc.api_client.connect.side_effect = mock_connect

        with patch("bot.watchdog.reconcile_positions", new=AsyncMock()):
            await w.run()

        assert w.reconnect_count == 1  # only one SUCCESS

    def test_ignore_stale_ticks_when_outside_session_and_flat(self):
        orc = make_orchestrator(connected=True)
        orc.session_filter.is_tradeable.return_value = (False, "OUTSIDE_SESSION")
        orc.executor.position_manager.count.return_value = 0
        w = make_watchdog(orc)
        assert w._should_ignore_stale_ticks() is True
