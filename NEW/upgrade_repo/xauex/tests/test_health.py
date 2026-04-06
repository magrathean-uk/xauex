"""
Tests for bot/health.py — HealthCheck class.

Covers:
- record_tick() updates last-tick timestamp
- _handle_health() returns "ok" when connected + recent tick
- _handle_health() returns "degraded" when no tick for > 60s
- _handle_health() returns "disconnected" when api.is_connected() is False
- HTTP 200 for "ok", HTTP 503 for degraded/disconnected
- open_positions count reflected in response
- reconnect_count reflected when watchdog is present
- observe_only flag mirrored from config
- uptime_seconds is non-negative
"""

import json
import time
from unittest.mock import MagicMock, AsyncMock
import pytest

from bot.health import HealthCheck


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_orchestrator(
    connected: bool = True,
    pos_count: int = 0,
    observe_only: bool = True,
    state_file_path: str = "/tmp/xauex_test_state.json",
    tradeable: bool = True,
):
    orc = MagicMock()
    orc.api_client.is_connected.return_value = connected
    orc.executor.position_manager.count.return_value = pos_count
    orc.session_filter.is_tradeable.return_value = (tradeable, "OK" if tradeable else "OUTSIDE_SESSION")
    orc.bot_status = "RUNNING"
    orc.config.observe_only = observe_only
    orc.config.state_file_path = state_file_path
    orc.current_strategy_health.return_value = (True, None)
    return orc


def make_watchdog(reconnect_count: int = 0):
    wd = MagicMock()
    wd.reconnect_count = reconnect_count
    return wd


def make_health(
    connected=True,
    pos_count=0,
    observe_only=True,
    watchdog=None,
    state_file_path: str = "/tmp/xauex_test_state.json",
    tradeable: bool = True,
):
    orc = make_orchestrator(
        connected=connected,
        pos_count=pos_count,
        observe_only=observe_only,
        state_file_path=state_file_path,
        tradeable=tradeable,
    )
    return HealthCheck(orc, port=9999, watchdog=watchdog)


async def _call_health(hc: HealthCheck):
    """Call the handler and return the parsed JSON body + status code."""
    request = MagicMock()
    response = await hc._handle_health(request)
    import json
    body = json.loads(response.body)
    return body, response.status


async def _call_state(hc: HealthCheck):
    request = MagicMock()
    response = await hc._handle_state(request)
    body = json.loads(response.body)
    return body, response.status


# ---------------------------------------------------------------------------
# record_tick()
# ---------------------------------------------------------------------------

class TestRecordTick:
    def test_record_tick_updates_last_tick_time(self):
        hc = make_health()
        assert hc._last_tick_time is None
        hc.record_tick()
        assert hc._last_tick_time is not None

    def test_record_tick_called_twice_updates_to_latest(self):
        hc = make_health()
        hc.record_tick()
        first = hc._last_tick_time
        time.sleep(0.01)
        hc.record_tick()
        assert hc._last_tick_time > first  # type: ignore[operator]

    def test_last_tick_age_none_before_first_tick(self):
        hc = make_health()
        # _last_tick_time is None ⇒ last_tick_age_seconds in response is None
        assert hc._last_tick_time is None


# ---------------------------------------------------------------------------
# Status classification
# ---------------------------------------------------------------------------

class TestStatusClassification:
    @pytest.mark.asyncio
    async def test_ok_when_connected_and_recent_tick(self):
        hc = make_health(connected=True)
        hc.record_tick()
        body, status = await _call_health(hc)
        assert body["status"] == "ok"
        assert status == 200

    @pytest.mark.asyncio
    async def test_disconnected_when_api_not_connected(self):
        hc = make_health(connected=False)
        hc.record_tick()
        body, status = await _call_health(hc)
        assert body["status"] == "disconnected"
        assert status == 503

    @pytest.mark.asyncio
    async def test_degraded_when_tick_stale(self):
        hc = make_health(connected=True)
        # Simulate a tick that happened 61 seconds ago
        hc._last_tick_time = time.monotonic() - 61
        body, status = await _call_health(hc)
        assert body["status"] == "degraded"
        assert status == 503

    @pytest.mark.asyncio
    async def test_ok_when_tick_just_within_window(self):
        hc = make_health(connected=True)
        hc._last_tick_time = time.monotonic() - 59  # 59 s old — still ok
        body, status = await _call_health(hc)
        assert body["status"] == "ok"
        assert status == 200

    @pytest.mark.asyncio
    async def test_ok_status_when_no_tick_yet_but_connected(self):
        """No ticks yet → last_tick_age is None → not degraded (cannot be stale)."""
        hc = make_health(connected=True)
        body, status = await _call_health(hc)
        assert body["status"] == "ok"
        assert status == 200

    @pytest.mark.asyncio
    async def test_ok_when_tick_stale_outside_session_and_flat(self):
        hc = make_health(connected=True, pos_count=0, tradeable=False)
        hc._last_tick_time = time.monotonic() - 120
        body, status = await _call_health(hc)
        assert body["status"] == "ok"
        assert status == 200

    @pytest.mark.asyncio
    async def test_degraded_when_strategy_data_unavailable_in_session(self):
        hc = make_health(connected=True, pos_count=0, tradeable=True)
        hc.record_tick()
        hc.orchestrator.current_strategy_health.return_value = (False, "H1_CONFIRMATION_UNAVAILABLE")
        body, status = await _call_health(hc)
        assert body["status"] == "degraded"
        assert body["strategy_ready"] is False
        assert body["strategy_reason"] == "H1_CONFIRMATION_UNAVAILABLE"
        assert status == 503

    @pytest.mark.asyncio
    async def test_ok_when_strategy_data_unavailable_outside_session(self):
        hc = make_health(connected=True, pos_count=0, tradeable=False)
        hc.record_tick()
        hc.orchestrator.current_strategy_health.return_value = (False, "H1_CONFIRMATION_UNAVAILABLE")
        body, status = await _call_health(hc)
        assert body["status"] == "ok"
        assert body["strategy_ready"] is False
        assert status == 200


# ---------------------------------------------------------------------------
# Body fields
# ---------------------------------------------------------------------------

class TestResponseBody:
    @pytest.mark.asyncio
    async def test_uptime_seconds_nonnegative(self):
        hc = make_health()
        body, _ = await _call_health(hc)
        assert body["uptime_seconds"] >= 0

    @pytest.mark.asyncio
    async def test_open_positions_reflected(self):
        hc = make_health(pos_count=3)
        body, _ = await _call_health(hc)
        assert body["open_positions"] == 3

    @pytest.mark.asyncio
    async def test_reconnect_count_from_watchdog(self):
        wd = make_watchdog(reconnect_count=5)
        hc = make_health(watchdog=wd)
        body, _ = await _call_health(hc)
        assert body["reconnect_count"] == 5

    @pytest.mark.asyncio
    async def test_reconnect_count_zero_without_watchdog(self):
        hc = make_health(watchdog=None)
        body, _ = await _call_health(hc)
        assert body["reconnect_count"] == 0

    @pytest.mark.asyncio
    async def test_observe_only_true(self):
        hc = make_health(observe_only=True)
        body, _ = await _call_health(hc)
        assert body["observe_only"] is True

    @pytest.mark.asyncio
    async def test_observe_only_false(self):
        hc = make_health(observe_only=False)
        body, _ = await _call_health(hc)
        assert body["observe_only"] is False

    @pytest.mark.asyncio
    async def test_last_tick_age_none_before_first_tick(self):
        hc = make_health()
        body, _ = await _call_health(hc)
        assert body["last_tick_age_seconds"] is None

    @pytest.mark.asyncio
    async def test_last_tick_age_nonnegative_after_tick(self):
        hc = make_health()
        hc.record_tick()
        body, _ = await _call_health(hc)
        assert body["last_tick_age_seconds"] >= 0

    @pytest.mark.asyncio
    async def test_timestamp_utc_present(self):
        hc = make_health()
        body, _ = await _call_health(hc)
        ts = body.get("timestamp_utc", "")
        assert ts.endswith("Z") and "T" in ts


class TestStateEndpoint:
    @pytest.mark.asyncio
    async def test_state_endpoint_returns_state_json(self, tmp_path):
        state_path = tmp_path / "state.json"
        expected = {"meta": {"bot_status": "RUNNING"}, "risk": {"daily_pnl": 1.23}}
        state_path.write_text(json.dumps(expected))
        hc = make_health(state_file_path=str(state_path))

        body, status = await _call_state(hc)

        assert status == 200
        assert body == expected

    @pytest.mark.asyncio
    async def test_state_endpoint_returns_503_when_missing(self, tmp_path):
        missing = tmp_path / "missing.json"
        hc = make_health(state_file_path=str(missing))

        body, status = await _call_state(hc)

        assert status == 503
        assert body["error"] == "state file unavailable"
        assert body["path"] == str(missing)
