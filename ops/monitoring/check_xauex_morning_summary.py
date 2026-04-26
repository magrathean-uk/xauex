#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import smtplib
import socket
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT_CANDIDATES = (
    Path(__file__).resolve().parents[2],
    Path("/home/bolyki/mirofish-gold-oracle"),
)

for candidate in REPO_ROOT_CANDIDATES:
    candidate_str = str(candidate)
    if candidate.is_dir():
        if candidate_str not in sys.path:
            sys.path.insert(0, candidate_str)
        break

from xauex.live_windows import all_live_windows, get_live_window

LONDON_TZ = ZoneInfo("Europe/London")
MORNING_NOTIFY_AFTER = dt_time(hour=8, minute=6)
DEFAULT_RECIPIENT = "bolyki@bolyki.eu"
DEFAULT_STATE_PATH = Path("/var/lib/monit/xauex-morning-summary.json")
DEFAULT_XAUEX_STATE_PATH = Path("/var/lib/xauex/state.json")
DEFAULT_CMD_PATH = Path("/var/lib/xauex/cmd.json")
DEFAULT_SMTP_HOST = "127.0.0.1"
DEFAULT_SMTP_PORT = 25


@dataclass
class SessionSummary:
    london_date: str
    slot: str
    window_label: str
    signal_id: str
    action: str
    confidence: float | None
    trade_outcome: str
    trade_type: str
    reasoning: str
    stop_loss_distance: float | None
    take_profit_distance: float | None
    distance_unit: str
    confirm_status: str
    confirm_reason: str


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")


def _today_london(now: datetime) -> str:
    return now.astimezone(LONDON_TZ).strftime("%Y-%m-%d")


def _default_sender_domain() -> str:
    fqdn = socket.getfqdn()
    if "." in fqdn:
        return fqdn.split(".", 1)[1]
    return socket.gethostname()


def _sent_slots(sent_state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    slots = sent_state.get("sent_slots", {})
    return slots if isinstance(slots, dict) else {}


def _slot_cutoff_passed(now: datetime, slot: str) -> bool:
    window = get_live_window(slot=slot)
    if window is None:
        return False
    return now.astimezone(ZoneInfo("UTC")) >= window.summary_cutoff_dt_utc(now.astimezone(ZoneInfo("UTC")))


def _parse_morning_summary(
    *,
    now: datetime,
    state_payload: dict[str, Any],
    cmd_payload: dict[str, Any],
    sent_state: dict[str, Any],
) -> SessionSummary | None:
    now_london = now.astimezone(LONDON_TZ)
    if now_london.timetz().replace(tzinfo=None) < MORNING_NOTIFY_AFTER:
        return None

    london_date = _today_london(now)
    signal_runs = [
        item
        for item in (state_payload.get("risk", {}) or {}).get("xauex_signal_runs_london", [])
        if isinstance(item, dict)
        and str(item.get("date_london", "")) == london_date
        and bool(item.get("terminal", True))
    ]
    if not signal_runs:
        return None

    sent_slots = _sent_slots(sent_state)
    signal = cmd_payload.get("xauex_signal") or {}
    for window in all_live_windows():
        if not _slot_cutoff_passed(now, window.slot):
            continue
        runs = [item for item in signal_runs if str(item.get("slot", "")).upper() == window.slot]
        if not runs:
            continue
        run = runs[-1]
        signal_id = str(run.get("signal_id", "") or "").strip()
        if not signal_id:
            continue
        if (
            str(sent_slots.get(window.slot, {}).get("signal_id", "")) == signal_id
            and bool(sent_slots.get(window.slot, {}).get("sent"))
        ):
            continue
        if (
            str(sent_state.get("date_london", "")) == london_date
            and str(sent_state.get("signal_id", "")) == signal_id
            and bool(sent_state.get("sent"))
        ):
            continue

        signal_timestamp = str(signal.get("timestamp_utc", "") or "").strip()
        signal_payload = signal if signal_timestamp == signal_id else {}
        action = str(signal_payload.get("action", run.get("signal_action", run.get("action", "HOLD"))) or "HOLD").upper()
        try:
            confidence = (
                float(signal_payload.get("confidence"))
                if signal_payload.get("confidence") is not None
                else None
            )
        except (TypeError, ValueError):
            confidence = None

        trade_outcome = str(run.get("reason", "") or "").upper()
        if trade_outcome == "ORDER_PLACED":
            trade_type = "LONG" if action == "BUY" else "SHORT" if action == "SELL" else "UNKNOWN"
        elif action == "HOLD":
            trade_type = "NONE"
        else:
            trade_type = "NONE"

        try:
            stop_loss_distance = float(signal_payload.get("stop_loss_distance")) if signal_payload.get("stop_loss_distance") is not None else None
        except (TypeError, ValueError):
            stop_loss_distance = None
        try:
            take_profit_distance = float(signal_payload.get("take_profit_distance")) if signal_payload.get("take_profit_distance") is not None else None
        except (TypeError, ValueError):
            take_profit_distance = None

        return SessionSummary(
            london_date=london_date,
            slot=window.slot,
            window_label=window.window_label,
            signal_id=signal_id,
            action=action,
            confidence=confidence,
            trade_outcome=trade_outcome or "UNKNOWN",
            trade_type=trade_type,
            reasoning=str(signal_payload.get("reasoning", "") or "").strip(),
            stop_loss_distance=stop_loss_distance,
            take_profit_distance=take_profit_distance,
            distance_unit=str(signal_payload.get("distance_unit", "usd") or "usd"),
            confirm_status=str(signal_payload.get("confirm_status", run.get("confirm_status", "UNKNOWN")) or "UNKNOWN"),
            confirm_reason=str(signal_payload.get("confirm_reason", run.get("confirm_reason", "")) or ""),
        )
    return None


def _build_message(summary: SessionSummary, recipient: str, hostname: str, sender_domain: str) -> EmailMessage:
    confidence_text = f"{summary.confidence:.2f}" if summary.confidence is not None else "n/a"
    trade_line = "No live trade opened."
    if summary.trade_outcome == "ORDER_PLACED":
        trade_line = f"Live trade opened: {summary.trade_type}."
    elif summary.trade_outcome not in {"HOLD", "ORDER_NOT_PLACED"}:
        trade_line = f"No live trade opened. Outcome: {summary.trade_outcome}."

    sl_text = f"{summary.stop_loss_distance:g} {summary.distance_unit}" if summary.stop_loss_distance is not None else "n/a"
    tp_text = f"{summary.take_profit_distance:g} {summary.distance_unit}" if summary.take_profit_distance is not None else "n/a"

    body = "\n".join(
        [
            f"XAUEX {summary.window_label} summary for {summary.london_date}",
            "",
            f"Signal: {summary.action}",
            f"Confidence: {confidence_text}",
            f"Confirm status: {summary.confirm_status}",
            f"Confirm reason: {summary.confirm_reason or 'n/a'}",
            f"Trade outcome: {summary.trade_outcome}",
            trade_line,
            f"Signal id: {summary.signal_id}",
            f"Stop loss distance: {sl_text}",
            f"Take profit distance: {tp_text}",
            "",
            f"Reasoning: {summary.reasoning or 'n/a'}",
        ]
    )

    message = EmailMessage()
    message["From"] = f"monit@{sender_domain}"
    message["To"] = recipient
    message["Subject"] = f"[Monit] XAUEX {summary.window_label} {summary.action} on {hostname}"
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
    now: datetime,
    state_path: Path,
    cmd_path: Path,
    sent_state_path: Path,
    sendmail_bin: Path | None,
    smtp_host: str,
    smtp_port: int,
    hostname: str | None = None,
    sender_domain: str | None = None,
) -> int:
    state_payload = _load_json(state_path)
    cmd_payload = _load_json(cmd_path)
    sent_state = _load_json(sent_state_path)

    summary = _parse_morning_summary(
        now=now,
        state_payload=state_payload,
        cmd_payload=cmd_payload,
        sent_state=sent_state,
    )
    if summary is None:
        return 0

    message = _build_message(
        summary,
        recipient,
        hostname or socket.gethostname(),
        sender_domain or _default_sender_domain(),
    )
    if sendmail_bin is not None:
        _send_via_sendmail(message, sendmail_bin)
    else:
        _send_via_smtp(message, smtp_host, smtp_port)
    _save_json(
        sent_state_path,
        {
            "date_london": summary.london_date,
            "signal_id": summary.signal_id,
            "slot": summary.slot,
            "sent": True,
            "sent_at_utc": now.astimezone(ZoneInfo("UTC")).isoformat(timespec="seconds"),
            "action": summary.action,
            "trade_outcome": summary.trade_outcome,
            "sent_slots": {
                **_sent_slots(sent_state),
                summary.slot: {
                    "signal_id": summary.signal_id,
                    "sent": True,
                    "sent_at_utc": now.astimezone(ZoneInfo("UTC")).isoformat(timespec="seconds"),
                },
            },
        },
    )
    print(
        f"xauex-morning-summary status=sent date_london={summary.london_date} "
        f"slot={summary.slot} action={summary.action} trade_outcome={summary.trade_outcome}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send a once-per-morning XAUEX summary email.")
    parser.add_argument("--recipient", default=DEFAULT_RECIPIENT)
    parser.add_argument("--state-path", type=Path, default=DEFAULT_XAUEX_STATE_PATH)
    parser.add_argument("--cmd-path", type=Path, default=DEFAULT_CMD_PATH)
    parser.add_argument("--sent-state-path", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--sendmail-bin", type=Path)
    parser.add_argument("--smtp-host", default=DEFAULT_SMTP_HOST)
    parser.add_argument("--smtp-port", type=int, default=DEFAULT_SMTP_PORT)
    args = parser.parse_args(argv)

    try:
        return run_once(
            recipient=args.recipient,
            now=datetime.now(LONDON_TZ),
            state_path=args.state_path,
            cmd_path=args.cmd_path,
            sent_state_path=args.sent_state_path,
            sendmail_bin=args.sendmail_bin,
            smtp_host=args.smtp_host,
            smtp_port=args.smtp_port,
        )
    except Exception as exc:
        print(f"xauex-morning-summary status=fail root_cause={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
