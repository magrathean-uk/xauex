#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import smtplib
import socket
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo


DEFAULT_RECIPIENT = "bolyki@bolyki.eu"
DEFAULT_ARCHIVE_ROOT = Path(os.getenv("XAUEX_SIGNAL_ARCHIVE_DIR", "/var/lib/xauex/signal_runs"))
DEFAULT_STATE_PATH = Path("/var/lib/monit/xauex-signal-stall.json")
DEFAULT_SMTP_HOST = "127.0.0.1"
DEFAULT_SMTP_PORT = 25
DEFAULT_LOOKBACK_HOURS = 96
DEFAULT_MIN_PROBLEM_RUNS = 3
MAX_SENT_ALERT_KEYS = 1000
LONDON_TZ = ZoneInfo("Europe/London")

_SOURCE_PROBLEM_TERMS = (
    "archive",
    "fallback",
    "fred",
    "hard-stale",
    "missing",
    "source fetch",
    "stale",
    "structured market snapshot",
    "timeout",
    "timed out",
    "unavailable",
)


@dataclass(frozen=True)
class SignalRun:
    run_id: str
    timestamp_utc: datetime
    window_label: str
    action: str
    reasoning: str
    validator_summary: str
    market_snapshot_state: str
    hard_blocker: bool
    missing_series_count: int
    stale_block_series_count: int
    cache_fallback_series_count: int
    fed_h15_fallback_series_count: int
    freshness_summary: str


@dataclass(frozen=True)
class SignalAlert:
    kind: str
    subject_label: str
    count_label: str
    runs: list[SignalRun]
    alert_key: str


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


def _sent_alert_keys(sent_state: dict[str, Any]) -> list[str]:
    raw_keys = sent_state.get("sent_alert_keys", [])
    if not isinstance(raw_keys, list):
        return []
    return [str(item) for item in raw_keys if str(item)]


def _utc_now_text(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _parse_timestamp_from_run_id(run_id: str) -> datetime | None:
    prefix = str(run_id or "").split("_", 1)[0]
    try:
        return datetime.strptime(prefix, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _freshness_from_payload(signal_payload: dict[str, Any], evidence_payload: dict[str, Any]) -> dict[str, Any]:
    decision_packet = signal_payload.get("decision_packet")
    if isinstance(decision_packet, dict):
        freshness = decision_packet.get("input_freshness")
        if isinstance(freshness, dict):
            return freshness
    freshness = signal_payload.get("input_freshness")
    if isinstance(freshness, dict):
        return freshness
    freshness = evidence_payload.get("input_freshness")
    if isinstance(freshness, dict):
        return freshness
    return {}


def _load_signal_run(run_dir: Path) -> SignalRun | None:
    try:
        signal_payload = json.loads((run_dir / "signal.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(signal_payload, dict):
        return None
    evidence_payload = _load_json(run_dir / "evidence.json")
    freshness = _freshness_from_payload(signal_payload, evidence_payload)
    timestamp = _parse_timestamp(signal_payload.get("timestamp_utc")) or _parse_timestamp_from_run_id(run_dir.name)
    if timestamp is None:
        return None
    return SignalRun(
        run_id=run_dir.name,
        timestamp_utc=timestamp,
        window_label=str(signal_payload.get("window_label") or freshness.get("window_label") or "unknown"),
        action=str(signal_payload.get("action") or "HOLD").upper(),
        reasoning=str(signal_payload.get("reasoning") or ""),
        validator_summary=str(signal_payload.get("validator_summary") or ""),
        market_snapshot_state=str(freshness.get("market_snapshot_state") or "").lower(),
        hard_blocker=_coerce_bool(freshness.get("hard_blocker")),
        missing_series_count=_coerce_int(freshness.get("missing_series_count")),
        stale_block_series_count=_coerce_int(freshness.get("stale_block_series_count")),
        cache_fallback_series_count=_coerce_int(freshness.get("cache_fallback_series_count")),
        fed_h15_fallback_series_count=_coerce_int(freshness.get("fed_h15_fallback_series_count")),
        freshness_summary=str(freshness.get("summary") or ""),
    )


def _load_recent_signal_runs(*, archive_root: Path, now: datetime, lookback_hours: int) -> list[SignalRun]:
    if not archive_root.exists():
        return []
    cutoff = now.astimezone(timezone.utc) - timedelta(hours=max(1, lookback_hours))
    runs: list[SignalRun] = []
    try:
        run_dirs = [path for path in archive_root.iterdir() if path.is_dir()]
    except OSError:
        return []
    for run_dir in run_dirs:
        run = _load_signal_run(run_dir)
        if run is None or run.timestamp_utc < cutoff:
            continue
        runs.append(run)
    return sorted(runs, key=lambda item: item.timestamp_utc)


def _is_source_degraded(run: SignalRun) -> bool:
    if run.hard_blocker:
        return True
    if run.market_snapshot_state in {"warning", "stale", "blocked"}:
        return True
    return any(
        count > 0
        for count in (
            run.missing_series_count,
            run.stale_block_series_count,
            run.cache_fallback_series_count,
            run.fed_h15_fallback_series_count,
        )
    )


def _source_problem_text(run: SignalRun) -> str:
    return " ".join(
        [
            run.reasoning,
            run.validator_summary,
            run.freshness_summary,
            run.market_snapshot_state,
        ]
    ).lower()


def _is_source_blocked_hold(run: SignalRun) -> bool:
    if run.action != "HOLD":
        return False
    if run.hard_blocker or run.market_snapshot_state == "blocked":
        return True
    if not _is_source_degraded(run):
        return False
    text = _source_problem_text(run)
    return any(term in text for term in _SOURCE_PROBLEM_TERMS)


def _trailing_matching(runs: list[SignalRun], predicate: Callable[[SignalRun], bool]) -> list[SignalRun]:
    matched: list[SignalRun] = []
    for run in reversed(runs):
        if not predicate(run):
            break
        matched.append(run)
    return list(reversed(matched))


def _alert_key(kind: str, latest_run: SignalRun) -> str:
    london_date = latest_run.timestamp_utc.astimezone(LONDON_TZ).strftime("%Y-%m-%d")
    return f"{kind}:{london_date}"


def _build_alerts(
    *,
    runs: list[SignalRun],
    sent_state: dict[str, Any],
    min_problem_runs: int,
) -> list[SignalAlert]:
    if not runs:
        return []
    sent_keys = set(_sent_alert_keys(sent_state))
    alerts: list[SignalAlert] = []

    stalled = _trailing_matching(runs, _is_source_blocked_hold)
    if len(stalled) >= min_problem_runs:
        key = _alert_key("stall", stalled[-1])
        if key not in sent_keys:
            alerts.append(
                SignalAlert(
                    kind="stall",
                    subject_label="signal stall",
                    count_label="Source-blocked HOLD runs",
                    runs=stalled,
                    alert_key=key,
                )
            )
        return alerts

    degraded = _trailing_matching(runs, _is_source_degraded)
    if len(degraded) >= min_problem_runs:
        key = _alert_key("degraded_sources", degraded[-1])
        if key not in sent_keys:
            alerts.append(
                SignalAlert(
                    kind="degraded_sources",
                    subject_label="signal sources degraded",
                    count_label="Degraded source runs",
                    runs=degraded,
                    alert_key=key,
                )
            )
    return alerts


def _display_run(run: SignalRun) -> str:
    timestamp = run.timestamp_utc.isoformat(timespec="seconds").replace("+00:00", "Z")
    return (
        f"- {timestamp} {run.window_label} {run.action} "
        f"state={run.market_snapshot_state or 'unknown'} "
        f"missing={run.missing_series_count} stale={run.stale_block_series_count} "
        f"archive_fallback={run.cache_fallback_series_count} h15_fallback={run.fed_h15_fallback_series_count}"
    )


def _build_message(alert: SignalAlert, recipient: str, hostname: str, sender_domain: str) -> EmailMessage:
    latest = alert.runs[-1]
    latest_timestamp = latest.timestamp_utc.isoformat(timespec="seconds").replace("+00:00", "Z")
    body = "\n".join(
        [
            f"XAUEX {alert.subject_label} detected on {hostname}.",
            "",
            f"{alert.count_label}: {len(alert.runs)}",
            f"Latest run: {latest.run_id}",
            f"Latest timestamp UTC: {latest_timestamp}",
            f"Latest window: {latest.window_label}",
            f"Latest action: {latest.action}",
            f"Latest market snapshot state: {latest.market_snapshot_state or 'unknown'}",
            f"Missing series count: {latest.missing_series_count}",
            f"Stale block series count: {latest.stale_block_series_count}",
            f"Archived fallback series count: {latest.cache_fallback_series_count}",
            f"Federal Reserve H.15 fallback series count: {latest.fed_h15_fallback_series_count}",
            "",
            f"Latest freshness summary: {latest.freshness_summary or 'n/a'}",
            f"Latest reasoning: {latest.reasoning or 'n/a'}",
            "",
            "Recent affected runs:",
            *[_display_run(run) for run in alert.runs[-8:]],
            "",
            "This alert is designed to fire before a multi-day quiet period can accumulate.",
        ]
    )
    message = EmailMessage()
    message["From"] = f"monit@{sender_domain}"
    message["To"] = recipient
    message["Subject"] = f"[Monit] XAUEX {alert.subject_label} on {hostname}"
    message.set_content(body, cte="8bit")
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
    archive_root: Path,
    sent_state_path: Path,
    sendmail_bin: Path | None,
    smtp_host: str,
    smtp_port: int,
    hostname: str | None = None,
    sender_domain: str | None = None,
    now: datetime | None = None,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
    min_problem_runs: int = DEFAULT_MIN_PROBLEM_RUNS,
) -> int:
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    sent_state = _load_json(sent_state_path)
    runs = _load_recent_signal_runs(
        archive_root=archive_root,
        now=current_time,
        lookback_hours=lookback_hours,
    )
    alerts = _build_alerts(
        runs=runs,
        sent_state=sent_state,
        min_problem_runs=max(1, min_problem_runs),
    )
    if not alerts:
        print(f"xauex-signal-stall status=ok recent_runs={len(runs)}")
        return 0

    sent_keys = _sent_alert_keys(sent_state)
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
        sent_keys.append(alert.alert_key)
        sent_keys = sent_keys[-MAX_SENT_ALERT_KEYS:]
        _save_json(
            sent_state_path,
            {
                "sent_alert_keys": sent_keys,
                "last_sent_alert_key": alert.alert_key,
                "last_sent_at_utc": _utc_now_text(current_time),
                "last_alert_kind": alert.kind,
                "last_run_id": alert.runs[-1].run_id,
            },
        )
        print(
            f"xauex-signal-stall status=sent kind={alert.kind} "
            f"runs={len(alert.runs)} latest_run={alert.runs[-1].run_id}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Email XAUEX alerts for repeated signal stalls or degraded inputs.")
    parser.add_argument("--recipient", default=DEFAULT_RECIPIENT)
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument("--sent-state-path", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--sendmail-bin", type=Path)
    parser.add_argument("--smtp-host", default=DEFAULT_SMTP_HOST)
    parser.add_argument("--smtp-port", type=int, default=DEFAULT_SMTP_PORT)
    parser.add_argument("--lookback-hours", type=int, default=DEFAULT_LOOKBACK_HOURS)
    parser.add_argument("--min-problem-runs", type=int, default=DEFAULT_MIN_PROBLEM_RUNS)
    args = parser.parse_args(argv)

    try:
        return run_once(
            recipient=args.recipient,
            archive_root=args.archive_root,
            sent_state_path=args.sent_state_path,
            sendmail_bin=args.sendmail_bin,
            smtp_host=args.smtp_host,
            smtp_port=args.smtp_port,
            lookback_hours=args.lookback_hours,
            min_problem_runs=args.min_problem_runs,
        )
    except Exception as exc:
        print(f"xauex-signal-stall status=fail root_cause={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
