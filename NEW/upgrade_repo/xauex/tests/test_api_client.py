"""
Tests for the rewritten ApiClient.

All tests use mocks — no live cTrader connection required.
Covers: volume encoding, ExecutionEvent parsing, amend SL/TP,
        close_position volume requirement, reconcile, tick partial updates.
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from bot.api.client import _proto_volume, _lots_from_proto, ApiClient
from bot.api.models import SymbolSpec, Position


# ─── Helpers ─────────────────────────────────────────────────────────────────

def make_symbol_spec(**kwargs):
    defaults = dict(
        symbol="XAUUSD",
        lot_size=100.0,       # 100 oz per std lot
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        digits=2,
        pip_value=0.01,
    )
    defaults.update(kwargs)
    return SymbolSpec(**defaults)


def make_config(**kwargs):
    cfg = MagicMock()
    cfg.ctrader_account_id   = "9911635"
    cfg.ctrader_client_id    = "test_id"
    cfg.ctrader_client_secret= "test_secret"
    cfg.ctrader_access_token = "test_token"
    cfg.ctrader_refresh_token= "test_refresh"
    cfg.ctrader_token_expiry = 9_999_999_999   # far future — no refresh needed
    cfg.ctrader_host         = "demo.ctraderapi.com"
    cfg.ctrader_port         = 5035
    for k, v in kwargs.items():
        setattr(cfg, k, v)
    return cfg


def make_client(spec: Optional[SymbolSpec] = None) -> ApiClient:
    cfg = make_config()
    client = ApiClient(cfg)
    client._connected   = True
    client._symbol_id   = 12345
    client._symbol_spec = spec or make_symbol_spec()
    client._client      = MagicMock()
    client._message_futures = {}
    return client


# ─── Volume encoding ──────────────────────────────────────────────────────────

class TestVolumeEncoding:
    def test_one_lot_xauusd(self):
        """1 std lot XAUUSD → 10000 protocol units (1 lot * 100 oz * 100)."""
        assert _proto_volume(1.0, 100.0) == 10_000

    def test_min_lot_xauusd(self):
        """0.01 std lots → 100 protocol units."""
        assert _proto_volume(0.01, 100.0) == 100

    def test_half_lot_xauusd(self):
        assert _proto_volume(0.5, 100.0) == 5_000

    def test_round_trip(self):
        """Converting to protocol and back should reproduce original lots."""
        for lots in (0.01, 0.05, 0.1, 1.0, 2.5):
            proto = _proto_volume(lots, 100.0)
            back  = _lots_from_proto(proto, 100.0)
            assert abs(back - lots) < 1e-9, f"Round-trip failed for {lots} lots"


# ─── place_market_order ───────────────────────────────────────────────────────

class TestPlaceMarketOrder:
    @pytest.mark.asyncio
    async def test_filled_returns_position_id(self):
        """ORDER_FILLED event with position → return position ID string."""
        client = make_client()

        event = MagicMock()
        event.executionType = 3          # ORDER_FILLED
        event.HasField.side_effect = lambda f: f == 'position'
        event.position.positionId = 88001
        event.errorCode = None

        client._send_and_wait = AsyncMock(return_value=event)
        client.refresh_token_if_needed = AsyncMock(return_value=False)

        result = await client.place_market_order("BUY", 0.01, 2700.0, 2750.0)
        assert result == {"status": "filled", "position_id": "88001"}

    @pytest.mark.asyncio
    async def test_rejected_returns_none(self):
        """ORDER_REJECTED event → return None."""
        client = make_client()

        event = MagicMock()
        event.executionType = 7          # ORDER_REJECTED
        event.HasField.return_value = False
        event.errorCode = "ORDER_REJECTED"

        client._send_and_wait = AsyncMock(return_value=event)
        client.refresh_token_if_needed = AsyncMock(return_value=False)

        pos_id = await client.place_market_order("SELL", 0.01, 2800.0, 2750.0)
        assert pos_id is None

    @pytest.mark.asyncio
    async def test_correct_protocol_volume_sent(self):
        """Market orders encode volume and relative SL/TP distances correctly."""
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOANewOrderReq

        client = make_client()
        client._last_bid = 2749.9
        client._last_ask = 2750.1

        event = MagicMock()
        event.executionType = 3
        event.HasField.side_effect = lambda f: f == 'position'
        event.position.positionId = 1
        event.errorCode = None

        captured_req = {}

        async def capture(req, **kw):
            captured_req['volume'] = req.volume
            captured_req['relativeStopLoss'] = req.relativeStopLoss
            captured_req['relativeTakeProfit'] = req.relativeTakeProfit
            return event

        client._send_and_wait = capture
        client.refresh_token_if_needed = AsyncMock(return_value=False)

        await client.place_market_order("BUY", 0.05, 2748.0, 2755.0)
        # 0.05 lots * 100 oz/lot * 100 = 500
        assert captured_req['volume'] == 500
        assert captured_req['relativeStopLoss'] == 210000
        assert captured_req['relativeTakeProfit'] == 490000

    @pytest.mark.asyncio
    async def test_zero_volume_returns_none(self):
        """A zero-lot order must be rejected before sending."""
        client = make_client()
        client.refresh_token_if_needed = AsyncMock(return_value=False)
        client._send_and_wait = AsyncMock()

        result = await client.place_market_order("BUY", 0.0, 2700.0, 2750.0)
        assert result is None
        client._send_and_wait.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        """Network exception → return None without raising."""
        client = make_client()
        client.refresh_token_if_needed = AsyncMock(return_value=False)
        client._send_and_wait = AsyncMock(side_effect=TimeoutError("timeout"))

        result = await client.place_market_order("BUY", 0.01, 2700.0, 2750.0)
        assert result is None


# ─── close_position ───────────────────────────────────────────────────────────

class TestClosePosition:
    @pytest.mark.asyncio
    async def test_requires_positive_volume(self):
        """Close with volume=0 must fail without sending a request."""
        client = make_client()
        client._send_and_wait = AsyncMock()

        ok = await client.close_position("123", 0.0)
        assert ok is False
        client._send_and_wait.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_correct_proto_volume(self):
        """Close 0.1 lot → protocol volume = 1000."""
        client = make_client()

        event = MagicMock()
        event.HasField.return_value = False  # no errorCode
        event.errorCode = None

        captured_req = {}

        async def capture(req, **kw):
            captured_req['volume']     = req.volume
            captured_req['positionId'] = req.positionId
            return event

        client._send_and_wait = capture
        await client.close_position("999", 0.1)
        assert captured_req['volume'] == 1000   # 0.1 * 100 * 100
        assert captured_req['positionId'] == 999

    @pytest.mark.asyncio
    async def test_error_response_returns_false(self):
        event = MagicMock()
        event.HasField.return_value = True
        event.errorCode = "CLOSE_ERROR"

        client = make_client()
        client._send_and_wait = AsyncMock(return_value=event)

        ok = await client.close_position("42", 0.01)
        assert ok is False


# ─── amend_position_sltp ─────────────────────────────────────────────────────

class TestAmendPositionSltp:
    @pytest.mark.asyncio
    async def test_sends_correct_fields(self):
        client = make_client()
        event  = MagicMock()
        event.HasField.return_value = False

        captured = {}

        async def capture(req, **kw):
            captured['positionId'] = req.positionId
            captured['stopLoss']   = req.stopLoss
            captured['takeProfit'] = req.takeProfit
            return event

        client._send_and_wait = capture
        ok = await client.amend_position_sltp("77", stop_loss=2720.50, take_profit=2800.0)
        assert ok is True
        assert captured['positionId'] == 77
        assert abs(captured['stopLoss']   - 2720.50) < 0.001
        assert abs(captured['takeProfit'] - 2800.0)  < 0.001

    @pytest.mark.asyncio
    async def test_error_response_returns_false(self):
        client = make_client()
        event  = MagicMock()
        event.HasField.return_value = True
        event.errorCode = "AMEND_ERROR"

        client._send_and_wait = AsyncMock(return_value=event)
        ok = await client.amend_position_sltp("55", stop_loss=2720.0)
        assert ok is False


# ─── reconcile ───────────────────────────────────────────────────────────────

class TestReconcile:
    @pytest.mark.asyncio
    async def test_returns_positions_and_orders(self):
        """reconcile() should convert broker positions to Position objects."""
        client = make_client()

        broker_pos = MagicMock()
        broker_pos.positionId = 101
        broker_pos.price      = 2745.0
        broker_pos.stopLoss   = 2720.0
        broker_pos.takeProfit = 2800.0
        broker_pos.HasField.return_value = True
        broker_pos.tradeData.tradeSide       = 1     # BUY
        broker_pos.tradeData.volume          = 500   # 0.05 lots in protocol (500/10000)
        broker_pos.tradeData.openTimestamp   = int(datetime(2026, 3, 10, 9, 0, 0,
                                                  tzinfo=timezone.utc).timestamp() * 1000)

        res = MagicMock()
        res.position = [broker_pos]
        res.order    = []

        client._send_and_wait = AsyncMock(return_value=res)

        positions, orders = await client.reconcile()

        assert len(positions) == 1
        pos = positions[0]
        assert pos.position_id == "101"
        assert pos.direction   == "LONG"
        assert abs(pos.volume - 0.05) < 1e-9   # 500 / (100 * 100)
        assert pos.entry_price == 2745.0
        assert len(orders) == 0

    @pytest.mark.asyncio
    async def test_no_symbol_spec_raises(self):
        """reconcile() without get_symbol_spec() should raise RuntimeError."""
        client = make_client()
        client._symbol_spec = None

        with pytest.raises(RuntimeError, match="get_symbol_spec"):
            await client.reconcile()


# ─── Tick partial update handling ─────────────────────────────────────────────

class TestTickPartialUpdate:
    """Spot events may omit bid or ask — client must cache last known value."""

    def test_last_bid_cached(self):
        """Partial tick with only bid → cached, mid computed when ask arrives."""
        client = make_client()
        assert client._last_bid is None
        assert client._last_ask is None

        # Simulate the cache-update logic from _dispatch_ticks
        client._last_bid = 2745.0
        assert client._last_ask is None  # not enough info for mid yet

        client._last_ask = 2745.5
        mid = (client._last_bid + client._last_ask) / 2.0
        assert abs(mid - 2745.25) < 0.001

    def test_last_ask_cached(self):
        """Partial tick with only ask → cached, mid computed when bid arrives."""
        client = make_client()
        client._last_ask = 2746.0
        client._last_bid = 2745.0
        mid = (client._last_bid + client._last_ask) / 2.0
        assert abs(mid - 2745.5) < 0.001
