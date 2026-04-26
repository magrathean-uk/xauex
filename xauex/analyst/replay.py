"""Replay XAUEX lifecycle events from the append-only event journal."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from xauex.shared.event_journal import read_events


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _latency_seconds(start: str | None, end: str | None) -> float | None:
    start_dt = _parse_ts(start)
    end_dt = _parse_ts(end)
    if start_dt is None or end_dt is None:
        return None
    return (end_dt - start_dt).total_seconds()


def load_journal(path: str | Path) -> list[dict[str, Any]]:
    return read_events(path)


def build_replay_report(events: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = {}
    order_keys: list[str] = []

    for event in sorted(events, key=lambda item: str(item.get("timestamp_utc") or "")):
        event_type = str(event.get("event_type") or "")
        correlation_id = str(event.get("correlation_id") or "")
        if not correlation_id:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        record = grouped.setdefault(correlation_id, {"correlation_id": correlation_id, "events": []})
        record["events"].append(event_type)
        if event_type == "order_intent":
            if correlation_id not in order_keys:
                order_keys.append(correlation_id)
            record.update(
                {
                    "owner": payload.get("owner"),
                    "direction": payload.get("direction"),
                    "lot_size": payload.get("lot_size"),
                    "intended_entry": payload.get("entry_price"),
                    "stop_loss": payload.get("stop_loss_price"),
                    "take_profit": payload.get("take_profit_price"),
                    "bid": payload.get("bid"),
                    "ask": payload.get("ask"),
                    "intent_timestamp_utc": event.get("timestamp_utc"),
                }
            )
        elif event_type == "order_ack":
            record["ack"] = payload
            record["ack_timestamp_utc"] = event.get("timestamp_utc")
            record["status"] = str(payload.get("status") or "accepted").lower()
            if payload.get("position_id"):
                record["position_id"] = payload.get("position_id")
            if payload.get("order_id"):
                record["order_id"] = payload.get("order_id")
        elif event_type == "order_rejected":
            record["reject"] = payload
            record["reject_timestamp_utc"] = event.get("timestamp_utc")
            record["status"] = "rejected"
            record["reject_reason"] = payload.get("reason") or payload.get("status") or "REJECTED"
        elif event_type == "position_opened":
            record["open"] = payload
            record["status"] = "filled"
            record["position_id"] = payload.get("position_id", record.get("position_id"))
            record["actual_entry"] = payload.get("entry_price", record.get("actual_entry"))
        elif event_type == "position_closed":
            record["close"] = payload
            record["position_id"] = payload.get("position_id", record.get("position_id"))
        elif event_type == "manual_command_rejected":
            record["manual_reject_reason"] = payload.get("reason")

    orders: list[dict[str, Any]] = []
    for key in order_keys:
        record = grouped[key]
        status = str(record.get("status") or "").lower()
        if not status:
            status = "unfilled"
            record["unfilled_reason"] = "NO_BROKER_ACK"
        elif status == "accepted" and not record.get("open"):
            record["unfilled_reason"] = "ACCEPTED_NOT_FILLED"
        record["status"] = status
        ack_time = record.get("ack_timestamp_utc") or record.get("reject_timestamp_utc")
        record["ack_latency_seconds"] = _latency_seconds(record.get("intent_timestamp_utc"), ack_time)
        orders.append(record)

    summary = {
        "orders": len(orders),
        "filled": sum(1 for order in orders if order.get("status") == "filled"),
        "rejected": sum(1 for order in orders if order.get("status") == "rejected"),
        "unfilled": sum(1 for order in orders if order.get("status") in {"unfilled", "accepted"}),
        "closed": sum(1 for order in orders if order.get("close")),
    }
    return {"summary": summary, "orders": orders}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay XAUEX event journal")
    parser.add_argument("--journal", default="/var/lib/xauex/events.jsonl", help="Path to events.jsonl")
    args = parser.parse_args(argv)
    report = build_replay_report(load_journal(args.journal))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
