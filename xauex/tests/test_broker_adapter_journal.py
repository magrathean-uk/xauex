from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from bot.execution.executor import Executor
from bot.patterns.detector import PatternType
from xauex.bot.api.broker import CTraderBrokerAdapter, OrderAck, OrderIntent


class _FakeBroker:
    def __init__(self, ack: OrderAck):
        self.ack = ack
        self.intent: OrderIntent | None = None

    async def place_market_order(self, intent: OrderIntent) -> OrderAck:
        self.intent = intent
        return self.ack


class _FakeApiClient:
    def __init__(self, result=None, last_order_reject=None):
        self.result = result
        self.last_order_reject = last_order_reject
        self.calls = []

    async def place_market_order(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def test_ctrader_adapter_preserves_structured_broker_rejection():
    api_client = _FakeApiClient(
        result=None,
        last_order_reject={"reason": "MARKET_CLOSED", "description": "Market is closed."},
    )
    adapter = CTraderBrokerAdapter(api_client)
    intent = OrderIntent(
        direction="BUY",
        lot_size=0.01,
        stop_loss_price=2350.0,
        take_profit_price=2385.0,
        entry_price=2362.5,
        owner="manual",
        pattern="NONE",
        correlation_id="cmd-1",
    )

    ack = asyncio.run(adapter.place_market_order(intent))

    assert ack.status == "rejected"
    assert ack.reason == "MARKET_CLOSED"
    assert ack.raw_result == {"reason": "MARKET_CLOSED", "description": "Market is closed."}


def test_executor_journals_normalized_order_intent_and_ack(tmp_path):
    journal = tmp_path / "events.jsonl"
    broker = _FakeBroker(OrderAck(status="accepted", order_id="o-1", raw_result={"status": "accepted", "order_id": "o-1"}))
    api_client = SimpleNamespace(_symbol_spec=SimpleNamespace(symbol="LTCUSD"), _last_bid=55.74, _last_ask=56.83)
    executor = Executor(
        config=SimpleNamespace(),
        api_client=api_client,
        level_manager=None,
        broker_adapter=broker,
        event_journal_path=journal,
    )

    result = asyncio.run(
        executor.place_market_order(
            direction=1,
            lot_size=0.02,
            stop_loss_price=55.0,
            take_profit_price=58.0,
            pattern=PatternType.NONE,
            level=56.83,
            owner="xauex",
            metadata={"correlation_id": "sig-1"},
        )
    )

    assert result == "order:o-1"
    assert broker.intent == OrderIntent(
        direction="BUY",
        lot_size=0.02,
        stop_loss_price=55.0,
        take_profit_price=58.0,
        entry_price=56.83,
        owner="xauex",
        pattern="NONE",
        correlation_id="sig-1",
        metadata={"correlation_id": "sig-1"},
        symbol="LTCUSD",
        bid=55.74,
        ask=56.83,
    )
    events = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    assert [event["event_type"] for event in events] == ["order_intent", "order_ack"]
    assert events[0]["correlation_id"] == "sig-1"
    assert events[0]["payload"]["symbol"] == "LTCUSD"
    assert events[0]["payload"]["stop_loss_price"] == 55.0
    assert events[1]["payload"]["status"] == "accepted"
    assert events[1]["payload"]["order_id"] == "o-1"


def test_executor_journals_broker_rejects(tmp_path):
    journal = tmp_path / "events.jsonl"
    broker = _FakeBroker(OrderAck(status="rejected", reason="MARKET_CLOSED", raw_result=None))
    executor = Executor(
        config=SimpleNamespace(),
        api_client=None,
        level_manager=None,
        broker_adapter=broker,
        event_journal_path=journal,
    )

    result = asyncio.run(
        executor.place_market_order(
            direction=-1,
            lot_size=0.01,
            stop_loss_price=2380.0,
            take_profit_price=2345.0,
            pattern=PatternType.NONE,
            level=2362.0,
            owner="manual",
            metadata={"correlation_id": "cmd-1"},
        )
    )

    assert result is None
    events = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    assert [event["event_type"] for event in events] == ["order_intent", "order_rejected"]
    assert events[-1]["correlation_id"] == "cmd-1"
    assert events[-1]["payload"]["reason"] == "MARKET_CLOSED"
