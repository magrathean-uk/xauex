from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from bot.execution.executor import Executor
from bot.patterns.detector import PatternType
from xauex.bot.api.broker import OrderAck, OrderIntent


class _FakeBroker:
    def __init__(self, ack: OrderAck):
        self.ack = ack
        self.intent: OrderIntent | None = None

    async def place_market_order(self, intent: OrderIntent) -> OrderAck:
        self.intent = intent
        return self.ack


def test_executor_journals_normalized_order_intent_and_ack(tmp_path):
    journal = tmp_path / "events.jsonl"
    broker = _FakeBroker(OrderAck(status="accepted", order_id="o-1", raw_result={"status": "accepted", "order_id": "o-1"}))
    executor = Executor(
        config=SimpleNamespace(observe_only=False),
        api_client=None,
        level_manager=None,
        broker_adapter=broker,
        event_journal_path=journal,
    )

    result = asyncio.run(
        executor.place_market_order(
            direction=1,
            lot_size=0.02,
            stop_loss_price=2350.0,
            take_profit_price=2385.0,
            pattern=PatternType.NONE,
            level=2362.5,
            owner="xauex",
            metadata={"correlation_id": "sig-1"},
        )
    )

    assert result == "order:o-1"
    assert broker.intent == OrderIntent(
        direction="BUY",
        lot_size=0.02,
        stop_loss_price=2350.0,
        take_profit_price=2385.0,
        entry_price=2362.5,
        owner="xauex",
        pattern="NONE",
        correlation_id="sig-1",
        metadata={"correlation_id": "sig-1"},
    )
    events = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    assert [event["event_type"] for event in events] == ["order_intent", "order_ack"]
    assert events[0]["correlation_id"] == "sig-1"
    assert events[0]["payload"]["stop_loss_price"] == 2350.0
    assert events[1]["payload"]["status"] == "accepted"
    assert events[1]["payload"]["order_id"] == "o-1"


def test_executor_journals_broker_rejects(tmp_path):
    journal = tmp_path / "events.jsonl"
    broker = _FakeBroker(OrderAck(status="rejected", reason="MARKET_CLOSED", raw_result=None))
    executor = Executor(
        config=SimpleNamespace(observe_only=False),
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
