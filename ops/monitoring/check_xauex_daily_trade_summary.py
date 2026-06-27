#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import smtplib
import socket
import subprocess
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


LONDON_TZ = ZoneInfo("Europe/London")
DEFAULT_RECIPIENT = "bolyki@bolyki.eu"
DEFAULT_JOURNAL_PATH = Path("/var/lib/xauex/trade_journal.json")
DEFAULT_EVENT_JOURNAL_PATH = Path("/var/lib/xauex/events.jsonl")
DEFAULT_STATE_PATH = Path("/var/lib/monit/xauex-daily-trade-summary.json")
DEFAULT_SMTP_HOST = "127.0.0.1"
DEFAULT_SMTP_PORT = 25
DEFAULT_NOTIFY_AFTER = dt_time(hour=15, minute=20)
MAX_SENT_DATES = 750


@dataclass(frozen=True)
class TradeResult:
    position_id: str
    direction: str
    pnl: float
    close_time_utc: str
    entry_price: Any = "n/a"
    close_price: Any = "n/a"
    lot_size: Any = "n/a"
    confidence: float | None = None
    window_label: str = ""
    source: str = "journal"

    @property
    def outcome(self) -> str:
        if self.pnl > 0:
            return "WIN"
        if self.pnl < 0:
            return "LOSS"
        return "FLAT"


@dataclass(frozen=True)
class DailyTradeSummary:
    london_date: str
    trades: list[TradeResult]

    @property
    def wins(self) -> int:
        return sum(1 for trade in self.trades if trade.pnl > 0)

    @property
    def losses(self) -> int:
        return sum(1 for trade in self.trades if trade.pnl < 0)

    @property
    def flats(self) -> int:
        return sum(1 for trade in self.trades if trade.pnl == 0)

    @property
    def net_pnl(self) -> float:
        return round(sum(trade.pnl for trade in self.trades), 2)


def _load_json(path: Path, default: Any) -> Any:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    return payload


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
    except OSError:
        return []
    return rows


def _default_sender_domain() -> str:
    fqdn = socket.getfqdn()
    if "." in fqdn:
        return fqdn.split(".", 1)[1]
    return socket.gethostname()


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _display(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value if value not in (None, "") else "n/a")


def _money(value: float) -> str:
    return f"{value:+.2f}"


def _direction(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"BUY", "LONG"}:
        return "LONG"
    if text in {"SELL", "SHORT"}:
        return "SHORT"
    return text or "UNKNOWN"


def _entry_payload(row: dict[str, Any]) -> dict[str, Any]:
    entry = row.get("entry")
    return entry if isinstance(entry, dict) else row


def _session_payload(entry: dict[str, Any]) -> dict[str, Any]:
    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    session = metadata.get("session") if isinstance(metadata.get("session"), dict) else {}
    return session


def _confidence(entry: dict[str, Any]) -> float | None:
    value = entry.get("signal_confidence")
    if value is None:
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        value = metadata.get("signal_confidence")
    if value is None:
        value = _session_payload(entry).get("confidence")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _trade_from_journal_row(row: dict[str, Any]) -> TradeResult | None:
    entry = _entry_payload(row)
    position_id = str(entry.get("position_id") or row.get("trade_id") or "").strip()
    if not position_id:
        return None
    close_time = str(entry.get("close_time_utc") or row.get("close_time_utc") or row.get("journalled_at_utc") or "")
    session = _session_payload(entry)
    return TradeResult(
        position_id=position_id,
        direction=_direction(entry.get("direction")),
        pnl=round(_safe_float(entry.get("pnl"), 0.0), 2),
        close_time_utc=close_time,
        entry_price=entry.get("entry_price", "n/a"),
        close_price=entry.get("close_price", "n/a"),
        lot_size=entry.get("lot_size", "n/a"),
        confidence=_confidence(entry),
        window_label=str(session.get("window_label") or entry.get("window_label") or ""),
        source="journal",
    )


def _trades_from_journal(path: Path) -> list[TradeResult]:
    payload = _load_json(path, [])
    rows = payload if isinstance(payload, list) else []
    trades: list[TradeResult] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        trade = _trade_from_journal_row(row)
        if trade is not None:
            trades.append(trade)
    return trades


def _trades_from_events(path: Path) -> list[TradeResult]:
    trades: list[TradeResult] = []
    for event in _read_jsonl(path):
        if str(event.get("event_type") or "") != "position_closed":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        position_id = str(payload.get("position_id") or "").strip()
        if not position_id:
            continue
        trades.append(
            TradeResult(
                position_id=position_id,
                direction=_direction(payload.get("direction")),
                pnl=round(_safe_float(payload.get("pnl"), 0.0), 2),
                close_time_utc=str(event.get("timestamp_utc") or payload.get("close_time_utc") or ""),
                entry_price=payload.get("entry_price", "n/a"),
                close_price=payload.get("close_price", "n/a"),
                lot_size=payload.get("lot_size", "n/a"),
                source="events",
            )
        )
    return trades


def _dedupe_trades(*trade_lists: list[TradeResult]) -> list[TradeResult]:
    trades_by_id: dict[str, TradeResult] = {}
    for trades in trade_lists:
        for trade in trades:
            existing = trades_by_id.get(trade.position_id)
            if existing is None or existing.source == "events":
                trades_by_id[trade.position_id] = trade
    return list(trades_by_id.values())


def _london_date_from_trade(trade: TradeResult) -> str:
    timestamp = _parse_timestamp(trade.close_time_utc)
    if timestamp is None:
        return ""
    return timestamp.astimezone(LONDON_TZ).strftime("%Y-%m-%d")


def _sent_dates(sent_state: dict[str, Any]) -> list[str]:
    raw_dates = sent_state.get("sent_dates", [])
    if not isinstance(raw_dates, list):
        return []
    return [str(item) for item in raw_dates if str(item)]


def _should_send(now: datetime, sent_state: dict[str, Any]) -> tuple[bool, str]:
    now_london = now.astimezone(LONDON_TZ)
    london_date = now_london.strftime("%Y-%m-%d")
    if now_london.weekday() >= 5:
        return False, london_date
    if now_london.timetz().replace(tzinfo=None) < DEFAULT_NOTIFY_AFTER:
        return False, london_date
    if london_date in set(_sent_dates(sent_state)):
        return False, london_date
    return True, london_date


def _build_summary(*, journal_path: Path, event_journal_path: Path, london_date: str) -> DailyTradeSummary:
    trades = _dedupe_trades(_trades_from_events(event_journal_path), _trades_from_journal(journal_path))
    day_trades = [trade for trade in trades if _london_date_from_trade(trade) == london_date]
    day_trades.sort(key=lambda trade: trade.close_time_utc)
    return DailyTradeSummary(london_date=london_date, trades=day_trades)


def _direction_line(summary: DailyTradeSummary, direction: str) -> str:
    trades = [trade for trade in summary.trades if trade.direction == direction]
    pnl = round(sum(trade.pnl for trade in trades), 2)
    return f"{direction}: {len(trades)} trades, PnL {_money(pnl)}"


def _trade_line(trade: TradeResult) -> str:
    confidence = f" conf={trade.confidence:.2f}" if trade.confidence is not None else ""
    window = f" window={trade.window_label}" if trade.window_label else ""
    return (
        f"- {trade.position_id} {trade.direction} {trade.outcome} {_money(trade.pnl)} "
        f"close={trade.close_time_utc or 'n/a'} lot={_display(trade.lot_size)} "
        f"entry={_display(trade.entry_price)} exit={_display(trade.close_price)}{confidence}{window}"
    )


def _build_message(summary: DailyTradeSummary, recipient: str, hostname: str, sender_domain: str) -> EmailMessage:
    lines = [
        f"XAUEX daily trade results for {summary.london_date}",
        "",
        f"Closed trades: {len(summary.trades)}",
        f"Wins: {summary.wins}",
        f"Losses: {summary.losses}",
        f"Flats: {summary.flats}",
        f"Net PnL: {_money(summary.net_pnl)}",
        _direction_line(summary, "LONG"),
        _direction_line(summary, "SHORT"),
        "",
    ]
    if summary.trades:
        lines.extend(["Trades:", *[_trade_line(trade) for trade in summary.trades]])
    else:
        lines.append("No XAUEX trades closed on this London date.")

    message = EmailMessage()
    message["From"] = f"monit@{sender_domain}"
    message["To"] = recipient
    message["Subject"] = f"[Monit] XAUEX daily trade results {summary.london_date} on {hostname}"
    message.set_content("\n".join(lines), cte="8bit")
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
    event_journal_path: Path,
    sent_state_path: Path,
    sendmail_bin: Path | None,
    smtp_host: str,
    smtp_port: int,
    hostname: str | None = None,
    sender_domain: str | None = None,
    now: datetime | None = None,
) -> int:
    current_time = now or datetime.now(LONDON_TZ)
    sent_state = _load_json(sent_state_path, {})
    sent_state = sent_state if isinstance(sent_state, dict) else {}
    should_send, london_date = _should_send(current_time, sent_state)
    if not should_send:
        print(f"xauex-daily-trade-summary status=ok date_london={london_date} sent=false")
        return 0

    summary = _build_summary(
        journal_path=journal_path,
        event_journal_path=event_journal_path,
        london_date=london_date,
    )
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

    sent_dates = [* _sent_dates(sent_state), london_date][-MAX_SENT_DATES:]
    _save_json(
        sent_state_path,
        {
            "sent_dates": sent_dates,
            "last_sent_date_london": london_date,
            "last_sent_at_utc": current_time.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "last_trade_count": len(summary.trades),
            "last_net_pnl": summary.net_pnl,
        },
    )
    print(
        f"xauex-daily-trade-summary status=sent date_london={london_date} "
        f"trades={len(summary.trades)} net_pnl={summary.net_pnl:+.2f}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send a once-per-London-day XAUEX trade result summary email.")
    parser.add_argument("--recipient", default=DEFAULT_RECIPIENT)
    parser.add_argument("--journal-path", type=Path, default=DEFAULT_JOURNAL_PATH)
    parser.add_argument("--event-journal-path", type=Path, default=DEFAULT_EVENT_JOURNAL_PATH)
    parser.add_argument("--sent-state-path", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--sendmail-bin", type=Path)
    parser.add_argument("--smtp-host", default=DEFAULT_SMTP_HOST)
    parser.add_argument("--smtp-port", type=int, default=DEFAULT_SMTP_PORT)
    args = parser.parse_args(argv)

    try:
        return run_once(
            recipient=args.recipient,
            journal_path=args.journal_path,
            event_journal_path=args.event_journal_path,
            sent_state_path=args.sent_state_path,
            sendmail_bin=args.sendmail_bin,
            smtp_host=args.smtp_host,
            smtp_port=args.smtp_port,
        )
    except Exception as exc:
        print(f"xauex-daily-trade-summary status=fail root_cause={exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
