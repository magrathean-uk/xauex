from __future__ import annotations

import json

from xauex.analyst.replay import build_replay_report, load_journal


def _event(event_id: str, event_type: str, correlation_id: str, timestamp: str, payload: dict) -> dict:
    return {
        "schema_version": 1,
        "event_id": event_id,
        "correlation_id": correlation_id,
        "timestamp_utc": timestamp,
        "source": "test",
        "event_type": event_type,
        "payload": payload,
    }


def test_replay_reconstructs_accepted_rejected_opened_and_closed_trades(tmp_path):
    journal = tmp_path / "events.jsonl"
    events = [
        _event(
            "evt-1",
            "order_intent",
            "sig-1",
            "2026-04-07T01:00:00Z",
            {
                "owner": "xauex",
                "direction": "BUY",
                "lot_size": 0.02,
                "entry_price": 2362.7,
                "stop_loss_price": 2350.2,
                "take_profit_price": 2387.7,
                "bid": 2362.2,
                "ask": 2362.7,
            },
        ),
        _event("evt-2", "order_ack", "sig-1", "2026-04-07T01:00:02Z", {"status": "filled", "position_id": "p-1"}),
        _event("evt-3", "position_opened", "sig-1", "2026-04-07T01:00:03Z", {"position_id": "p-1", "entry_price": 2362.8}),
        _event("evt-4", "position_closed", "sig-1", "2026-04-07T02:00:00Z", {"position_id": "p-1", "close_price": 2370.0, "pnl": 14.4}),
        _event(
            "evt-5",
            "order_intent",
            "manual-1",
            "2026-04-07T03:00:00Z",
            {
                "owner": "manual",
                "direction": "SELL",
                "lot_size": 0.01,
                "entry_price": 2371.0,
                "stop_loss_price": 2383.0,
                "take_profit_price": 2347.0,
                "bid": 2371.0,
                "ask": 2371.4,
            },
        ),
        _event("evt-6", "order_rejected", "manual-1", "2026-04-07T03:00:01Z", {"reason": "MARKET_CLOSED"}),
    ]
    journal.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")

    report = build_replay_report(load_journal(journal))

    assert report["summary"] == {"orders": 2, "filled": 1, "rejected": 1, "unfilled": 0, "closed": 1}
    accepted = report["orders"][0]
    assert accepted["correlation_id"] == "sig-1"
    assert accepted["intended_entry"] == 2362.7
    assert accepted["stop_loss"] == 2350.2
    assert accepted["take_profit"] == 2387.7
    assert accepted["ack_latency_seconds"] == 2.0
    assert accepted["close"]["pnl"] == 14.4
    rejected = report["orders"][1]
    assert rejected["correlation_id"] == "manual-1"
    assert rejected["status"] == "rejected"
    assert rejected["reject_reason"] == "MARKET_CLOSED"


def test_replay_marks_unfilled_orders_without_ack(tmp_path):
    events = [
        _event(
            "evt-1",
            "order_intent",
            "sig-unfilled",
            "2026-04-07T01:00:00Z",
            {"direction": "BUY", "entry_price": 2362.7, "stop_loss_price": 2350.2, "take_profit_price": 2387.7},
        )
    ]

    report = build_replay_report(events)

    assert report["summary"]["unfilled"] == 1
    assert report["orders"][0]["status"] == "unfilled"
    assert report["orders"][0]["unfilled_reason"] == "NO_BROKER_ACK"
