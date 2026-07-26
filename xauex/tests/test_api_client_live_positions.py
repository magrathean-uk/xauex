import asyncio
# ruff: noqa: E402

import sys
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

XAUEX_ROOT = REPO_ROOT / "xauex"
if str(XAUEX_ROOT) not in sys.path:
    sys.path.insert(0, str(XAUEX_ROOT))

from xauex.bot.api.client import ApiClient
from xauex.bot.api.models import SymbolSpec


class _FakeTradeData:
    def __init__(self, trade_side, volume, open_timestamp):
        self.tradeSide = trade_side
        self.volume = volume
        self.openTimestamp = open_timestamp


class _FakePosition:
    def __init__(self, *, position_id, trade_side, volume, price, stop_loss, take_profit):
        self.positionId = position_id
        self.tradeData = _FakeTradeData(trade_side=trade_side, volume=volume, open_timestamp=1_700_000_000_000)
        self.price = price
        self.stopLoss = stop_loss
        self.takeProfit = take_profit

    def HasField(self, name):
        return name in {"stopLoss", "takeProfit"}


class _FakePnlEntry:
    def __init__(self, position_id, gross_unrealized_pnl):
        self.positionId = position_id
        self.grossUnrealizedPnL = gross_unrealized_pnl


def test_reconcile_uses_live_quote_and_unrealized_pnl_response():
    config = SimpleNamespace(
        ctrader_account_id="123",
        ctrader_host="",
        ctrader_port=0,
        ctrader_client_id="",
        ctrader_client_secret="",
        ctrader_access_token="",
    )
    client = ApiClient(config)
    client._symbol_spec = SymbolSpec(
        symbol="XAUUSD",
        lot_size=100.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        digits=2,
        pip_value=0.01,
    )
    client._last_bid = 2349.8
    client._last_ask = 2350.4

    reconcile_res = SimpleNamespace(
        position=[
            _FakePosition(position_id=1, trade_side=1, volume=300, price=2352.0, stop_loss=2339.5, take_profit=2370.0),
            _FakePosition(position_id=2, trade_side=2, volume=200, price=2348.0, stop_loss=2360.5, take_profit=2330.0),
        ],
        order=[],
    )
    pnl_res = SimpleNamespace(
        positionUnrealizedPnL=[
            _FakePnlEntry(position_id=1, gross_unrealized_pnl=-660),
            _FakePnlEntry(position_id=2, gross_unrealized_pnl=-480),
        ],
        moneyDigits=2,
    )

    async def _fake_send_and_wait(message, timeout=10.0):
        name = message.__class__.__name__
        if name == "ProtoOAReconcileReq":
            return reconcile_res
        if name == "ProtoOAGetPositionUnrealizedPnLReq":
            return pnl_res
        raise AssertionError(f"unexpected request {name}")

    client._send_and_wait = _fake_send_and_wait

    positions, pending_orders = asyncio.run(client.reconcile())

    assert pending_orders == []
    assert positions[0].position_id == "1"
    assert positions[0].current_price == 2349.8
    assert positions[0].unrealised_pnl == -6.6
    assert positions[1].position_id == "2"
    assert positions[1].current_price == 2350.4
    assert positions[1].unrealised_pnl == -4.8
