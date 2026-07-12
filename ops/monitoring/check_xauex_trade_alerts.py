#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import smtplib
import socket
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

REPO_ROOT_CANDIDATES = (
    Path(__file__).resolve().parents[2],
    Path("/home/bolyki/mirofish-gold-oracle"),
)

for candidate in REPO_ROOT_CANDIDATES:
    candidate_str = str(candidate)
    if (candidate / "xauex").is_dir():
        if candidate_str not in sys.path:
            sys.path.insert(0, candidate_str)
        break

DEFAULT_RECIPIENT = "bolyki@bolyki.eu"
DEFAULT_JOURNAL_PATH = Path("/var/lib/xauex/events.jsonl")
DEFAULT_STATE_PATH = Path("/var/lib/monit/xauex-trade-alerts.json")
DEFAULT_SMTP_HOST = "127.0.0.1"
DEFAULT_SMTP_PORT = 25
MAX_SENT_EVENT_IDS = 5000


@dataclass(frozen=True)
class TradeAlert:
    event_id: str
    correlation_id: str
    timestamp_utc: str
    symbol: str
    direction: str
    lot_size: Any
    entry_price: Any
    stop_loss: Any
    take_profit: Any
    owner: str
    order_id: str
    position_id: str


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")


def _default_sender_domain() -> str:
    fqdn = socket.getfqdn()
    if "." in fqdn:
        return fqdn.split(".", 1)[1]
    return socket.gethostname()


def _utc_now_text(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _event_key(event: dict[str, Any]) -> str:
    event_id = str(event.get("event_id") or "").strip()
    if event_id:
        return event_id
    return "|".join(
        [
            str(event.get("correlation_id") or ""),
            str(event.get("timestamp_utc") or ""),
            str(event.get("event_type") or ""),
        ]
    )


def _sent_event_ids(sent_state: dict[str, Any]) -> list[str]:
    raw_ids = sent_state.get("sent_event_ids", [])
    if not isinstance(raw_ids, list):
        return []
    return [str(item) for item in raw_ids if str(item)]


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def _value(*items: Any, default: Any = "n/a") -> Any:
    for item in items:
        if item is not None and item != "":
            return item
    return default


def _display(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value if value is not None and value != "" else "n/a")


def _build_trade_alerts(events: list[dict[str, Any]], sent_state: dict[str, Any]) -> list[TradeAlert]:
    sent_ids = set(_sent_event_ids(sent_state))
    intents_by_correlation: dict[str, dict[str, Any]] = {}
    alerts: list[TradeAlert] = []

    for event in sorted(events, key=lambda item: str(item.get("timestamp_utc") or "")):
        event_type = str(event.get("event_type") or "")
        correlation_id = str(event.get("correlation_id") or "")
        payload = _payload(event)
        if event_type == "order_intent" and correlation_id:
            intents_by_correlation[correlation_id] = payload
            continue
        if event_type != "position_opened":
            continue

        event_id = _event_key(event)
        if event_id in sent_ids:
            continue
        intent = intents_by_correlation.get(correlation_id, {})
        alerts.append(
            TradeAlert(
                event_id=event_id,
                correlation_id=correlation_id or "n/a",
                timestamp_utc=str(event.get("timestamp_utc") or "n/a"),
                symbol=str(_value(payload.get("symbol"), intent.get("symbol"), default="UNKNOWN")),
                direction=str(_value(payload.get("direction"), intent.get("direction"), default="UNKNOWN")),
                lot_size=_value(payload.get("lot_size"), intent.get("lot_size")),
                entry_price=_value(payload.get("entry_price"), intent.get("entry_price")),
                stop_loss=_value(payload.get("stop_loss"), intent.get("stop_loss_price")),
                take_profit=_value(payload.get("take_profit"), intent.get("take_profit_price")),
                owner=str(_value(payload.get("owner"), intent.get("owner"), default="unknown")),
                order_id=str(_value(payload.get("order_id"), default="n/a")),
                position_id=str(_value(payload.get("position_id"), default="n/a")),
            )
        )
    return alerts


def _build_message(alert: TradeAlert, recipient: str, hostname: str, sender_domain: str) -> EmailMessage:
    body = "\n".join(
        [
            f"Trade opened: {alert.symbol} {alert.direction}",
            "",
            f"Symbol: {alert.symbol}",
            f"Direction: {alert.direction}",
            f"Lot size: {_display(alert.lot_size)}",
            f"Entry: {_display(alert.entry_price)}",
            f"Stop loss: {_display(alert.stop_loss)}",
            f"Take profit: {_display(alert.take_profit)}",
            f"Owner: {alert.owner}",
            f"Position id: {alert.position_id}",
            f"Order id: {alert.order_id}",
            f"Correlation id: {alert.correlation_id}",
            f"Event id: {alert.event_id}",
            f"Opened at UTC: {alert.timestamp_utc}",
        ]
    )
    message = EmailMessage()
    message["From"] = f"monit@{sender_domain}"
    message["To"] = recipient
    message["Subject"] = (
        f"[Monit] XAUEX trade opened {alert.symbol} {alert.direction} "
        f"{_display(alert.lot_size)} on {hostname}"
    )
    message.set_content(body)
    return message


def _send_via_sendmail(message: EmailMessage, sendmail_bin: Path) -> None:
    proc = subprocess.run(
        [str(sendmail_bin), "-t"],
        input=message.as_string(),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"sendmail exited {proc.returncode}").strip())


def _send_via_smtp(message: EmailMessage, smtp_host: str, smtp_port: int) -> None:
    with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as client:
        client.send_message(message)


def run_once(
    *,
    recipient: str,
    journal_path: Path,
    sent_state_path: Path,
    sendmail_bin: Path | None,
    smtp_host: str,
    smtp_port: int,
    hostname: str | None = None,
    sender_domain: str | None = None,
    now: datetime | None = None,
) -> int:
    from xauex.shared.event_journal import read_events

    sent_state = _load_json(sent_state_path)
    sent_ids = _sent_event_ids(sent_state)
    alerts = _build_trade_alerts(read_events(journal_path), sent_state)
    if not alerts:
        return 0

    for alert in alerts:
        message = _build_message(
            alert,
            recipient,
            hostname or socket.gethostname(),
            sender_domain or _default_sender_domain(),
        )
        if sendmail_bin is not None:
            _send_via_sendmail(message, sendmail_bin)
        else:
            _send_via_smtp(message, smtp_host, smtp_port)
        sent_ids.append(alert.event_id)
        sent_ids = sent_ids[-MAX_SENT_EVENT_IDS:]
        _save_json(
            sent_state_path,
            {
                "sent_event_ids": sent_ids,
                "last_sent_event_id": alert.event_id,
                "last_sent_at_utc": _utc_now_text(now),
                "last_symbol": alert.symbol,
                "last_direction": alert.direction,
                "last_position_id": alert.position_id,
            },
        )
        print(
            f"xauex-trade-alert status=sent symbol={alert.symbol} "
            f"direction={alert.direction} position_id={alert.position_id}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Email XAUEX trade-open alerts from the event journal.")
    parser.add_argument("--recipient", default=DEFAULT_RECIPIENT)
    parser.add_argument("--journal-path", type=Path, default=DEFAULT_JOURNAL_PATH)
    parser.add_argument("--sent-state-path", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--sendmail-bin", type=Path)
    parser.add_argument("--smtp-host", default=DEFAULT_SMTP_HOST)
    parser.add_argument("--smtp-port", type=int, default=DEFAULT_SMTP_PORT)
    args = parser.parse_args(argv)

    try:
        return run_once(
            recipient=args.recipient,
            journal_path=args.journal_path,
            sent_state_path=args.sent_state_path,
            sendmail_bin=args.sendmail_bin,
            smtp_host=args.smtp_host,
            smtp_port=args.smtp_port,
        )
    except Exception as exc:
        print(f"xauex-trade-alert status=fail root_cause={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
