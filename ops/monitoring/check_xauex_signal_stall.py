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
DEFAULT_EVENT_JOURNAL_PATH = Path(os.getenv("XAUEX_EVENT_JOURNAL_PATH", "/var/lib/xauex/events.jsonl"))
DEFAULT_STATE_PATH = Path("/var/lib/monit/xauex-signal-stall.json")
DEFAULT_SMTP_HOST = "127.0.0.1"
DEFAULT_SMTP_PORT = 25
DEFAULT_LOOKBACK_HOURS = 96
DEFAULT_MIN_PROBLEM_RUNS = 3
DEFAULT_NO_TRADE_DAYS = 5
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
    count_value: int | None = None
    latest_trade_timestamp_utc: datetime | None = None
    affected_dates: tuple[str, ...] = ()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")


def _read_event_journal(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    events.append(payload)
    except OSError:
        return []
    return events


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


def _utc_datetime_text(value: datetime | None) -> str:
    if value is None:
        return "none found in event journal"
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


def _date_alert_key(kind: str, now: datetime) -> str:
    london_date = now.astimezone(LONDON_TZ).strftime("%Y-%m-%d")
    return f"{kind}:{london_date}"


def _latest_position_opened_at(events: list[dict[str, Any]]) -> datetime | None:
    latest: datetime | None = None
    for event in events:
        if str(event.get("event_type") or "") != "position_opened":
            continue
        timestamp = _parse_timestamp(event.get("timestamp_utc"))
        if timestamp is not None and (latest is None or timestamp > latest):
            latest = timestamp
    return latest


def _signal_runs_after_trade(
    *,
    runs: list[SignalRun],
    latest_trade_timestamp_utc: datetime | None,
    now: datetime,
) -> tuple[list[str], list[SignalRun]]:
    current_london_date = now.astimezone(LONDON_TZ).date()
    seen_dates: set[str] = set()
    affected_dates: list[str] = []
    affected_runs: list[SignalRun] = []
    for run in runs:
        if latest_trade_timestamp_utc is not None and run.timestamp_utc <= latest_trade_timestamp_utc:
            continue
        london_date = run.timestamp_utc.astimezone(LONDON_TZ).date()
        if london_date > current_london_date or london_date.weekday() >= 5:
            continue
        affected_runs.append(run)
        date_text = london_date.isoformat()
        if date_text not in seen_dates:
            seen_dates.add(date_text)
            affected_dates.append(date_text)
    return affected_dates, affected_runs


def _previous_london_trading_date(value: datetime) -> str:
    candidate = value.astimezone(LONDON_TZ).date() - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate.isoformat()


def _event_signal_run(
    *,
    event: dict[str, Any],
    payload: dict[str, Any],
    timestamp: datetime,
    reasoning: str,
    freshness_summary: str,
) -> SignalRun:
    slot = str(payload.get("slot") or "").upper()
    return SignalRun(
        run_id=str(event.get("correlation_id") or f"{timestamp:%Y%m%dT%H%M%SZ}_{slot.lower()}"),
        timestamp_utc=timestamp,
        window_label=slot.lower(),
        action=str(payload.get("signal_action") or "HOLD").upper(),
        reasoning=reasoning,
        validator_summary="",
        market_snapshot_state="",
        hard_blocker=True,
        missing_series_count=0,
        stale_block_series_count=0,
        cache_fallback_series_count=0,
        fed_h15_fallback_series_count=0,
        freshness_summary=freshness_summary,
    )


def _decision_invariant_alert(
    *,
    event_journal_events: list[dict[str, Any]],
    sent_keys: set[str],
    now: datetime,
) -> SignalAlert | None:
    cutoff = now.astimezone(timezone.utc) - timedelta(hours=DEFAULT_LOOKBACK_HOURS)
    anomalous_runs: list[SignalRun] = []
    for event in event_journal_events:
        if str(event.get("event_type") or "") != "risk_result":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        timestamp = _parse_timestamp(event.get("timestamp_utc"))
        if timestamp is None or timestamp < cutoff or timestamp > now.astimezone(timezone.utc):
            continue
        if str(payload.get("reason") or "").upper() != "HARD_BLOCKER":
            continue
        if not _coerce_bool(payload.get("terminal")):
            continue

        raw_factors = payload.get("block_factors") or payload.get("policy_factors")
        factors = [str(item).upper() for item in raw_factors] if isinstance(raw_factors, list) else []
        evidence = payload.get("pattern_evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        evidence_factor = str(evidence.get("factor") or "").upper()
        pattern = str(evidence.get("pattern") or "").upper()
        problems: list[str] = []
        if not factors or "UNEXPLAINED_HARD_BLOCKER" in factors:
            problems.append("HARD_BLOCKER without block factors")
        if (
            "PATTERN_DIRECTION_MISMATCH" in factors + [evidence_factor]
            and pattern in {"", "NONE"}
        ):
            problems.append(f"PATTERN_DIRECTION_MISMATCH with pattern={pattern or 'missing'}")
        if not problems:
            continue
        anomalous_runs.append(
            _event_signal_run(
                event=event,
                payload=payload,
                timestamp=timestamp,
                reasoning="; ".join(problems),
                freshness_summary=", ".join(factors),
            )
        )

    if not anomalous_runs:
        return None
    latest = anomalous_runs[-1]
    key = f"decision_invariant:{latest.run_id}"
    if key in sent_keys:
        return None
    return SignalAlert(
        kind="decision_invariant",
        subject_label="hard-block evidence invariant",
        count_label="Invalid hard-block events",
        runs=anomalous_runs,
        alert_key=key,
        count_value=len(anomalous_runs),
        affected_dates=tuple(
            dict.fromkeys(run.timestamp_utc.astimezone(LONDON_TZ).date().isoformat() for run in anomalous_runs)
        ),
    )


def _pattern_suppression_alert(
    *,
    event_journal_events: list[dict[str, Any]],
    sent_keys: set[str],
    now: datetime,
) -> SignalAlert | None:
    by_date: dict[str, dict[str, SignalRun]] = {}
    current_london_date = now.astimezone(LONDON_TZ).date()
    for event in event_journal_events:
        if str(event.get("event_type") or "") != "risk_result":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        timestamp = _parse_timestamp(event.get("timestamp_utc"))
        if timestamp is None:
            continue
        london_date = timestamp.astimezone(LONDON_TZ).date()
        if london_date > current_london_date or london_date.weekday() >= 5:
            continue
        slot = str(payload.get("slot") or "").upper()
        if slot not in {"MORNING", "MIDDAY", "US_OPEN"}:
            continue
        if str(payload.get("reason") or "").upper() != "HARD_BLOCKER":
            continue
        if not _coerce_bool(payload.get("terminal")):
            continue
        policy_factors = payload.get("policy_factors")
        factors = [str(item).upper() for item in policy_factors] if isinstance(policy_factors, list) else []
        evidence = payload.get("pattern_evidence")
        evidence_factor = str(evidence.get("factor") or "").upper() if isinstance(evidence, dict) else ""
        if not any(factor.startswith("PATTERN_") for factor in factors + [evidence_factor]):
            continue
        by_date.setdefault(london_date.isoformat(), {})[slot] = _event_signal_run(
            event=event,
            payload=payload,
            timestamp=timestamp,
            reasoning=str(payload.get("reason") or "HARD_BLOCKER"),
            freshness_summary=", ".join(factors),
        )

    qualifying_pair: tuple[str, str, list[SignalRun]] | None = None
    for latest_date in sorted(by_date):
        latest_datetime = datetime.fromisoformat(latest_date).replace(tzinfo=LONDON_TZ)
        previous_date = _previous_london_trading_date(latest_datetime)
        if previous_date not in by_date:
            continue
        pair_runs = [
            run
            for date_text in (previous_date, latest_date)
            for run in by_date[date_text].values()
        ]
        if len(pair_runs) >= 4:
            qualifying_pair = previous_date, latest_date, pair_runs
    if qualifying_pair is None:
        return None
    previous_date, latest_date, affected_runs = qualifying_pair

    key = f"pattern_suppression:{latest_date}"
    if key in sent_keys:
        return None
    affected_dates = (previous_date, latest_date)
    return SignalAlert(
        kind="pattern_suppression",
        subject_label="pattern suppression",
        count_label="Pattern-suppressed windows in two trading days",
        runs=affected_runs,
        alert_key=key,
        count_value=len(affected_runs),
        affected_dates=affected_dates,
    )


def _build_alerts(
    *,
    runs: list[SignalRun],
    event_journal_events: list[dict[str, Any]],
    sent_state: dict[str, Any],
    min_problem_runs: int,
    no_trade_days: int,
    now: datetime,
) -> list[SignalAlert]:
    sent_keys = set(_sent_alert_keys(sent_state))
    alerts: list[SignalAlert] = []
    invariant_alert = _decision_invariant_alert(
        event_journal_events=event_journal_events,
        sent_keys=sent_keys,
        now=now,
    )
    if invariant_alert is not None:
        alerts.append(invariant_alert)
    pattern_alert = _pattern_suppression_alert(
        event_journal_events=event_journal_events,
        sent_keys=sent_keys,
        now=now,
    )
    if pattern_alert is not None:
        alerts.append(pattern_alert)
    if not runs:
        return alerts

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
    if no_trade_days > 0:
        latest_trade_timestamp = _latest_position_opened_at(event_journal_events)
        no_trade_dates, no_trade_runs = _signal_runs_after_trade(
            runs=runs,
            latest_trade_timestamp_utc=latest_trade_timestamp,
            now=now,
        )
        if len(no_trade_dates) >= no_trade_days:
            key = _date_alert_key("no_trades", now)
            if key not in sent_keys:
                alerts.append(
                    SignalAlert(
                        kind="no_trades",
                        subject_label="no executed trades",
                        count_label="No-trade signal days",
                        runs=no_trade_runs,
                        alert_key=key,
                        count_value=len(no_trade_dates),
                        latest_trade_timestamp_utc=latest_trade_timestamp,
                        affected_dates=tuple(no_trade_dates),
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
    if alert.kind == "decision_invariant":
        has_impossible_pattern = any("PATTERN_DIRECTION_MISMATCH" in run.reasoning for run in alert.runs)
        body = "\n".join(
            [
                f"XAUEX hard-block evidence invariant failed on {hostname}.",
                "",
                (
                    "Impossible pattern evidence was recorded."
                    if has_impossible_pattern
                    else "A terminal HARD_BLOCKER was recorded without its underlying block factors."
                ),
                f"{alert.count_label}: {alert.count_value if alert.count_value is not None else len(alert.runs)}",
                f"Affected London dates: {', '.join(alert.affected_dates)}",
                "",
                "Invalid events:",
                *[
                    f"- {run.timestamp_utc.isoformat(timespec='seconds')} {run.window_label} {run.reasoning}"
                    for run in alert.runs
                ],
                "",
                "Treat this as a runtime correctness fault. Review code and event evidence before changing risk policy.",
            ]
        )
        message = EmailMessage()
        message["From"] = f"monit@{sender_domain}"
        message["To"] = recipient
        message["Subject"] = f"[Monit] XAUEX {alert.subject_label} on {hostname}"
        message.set_content(body, cte="8bit")
        return message

    if alert.kind == "pattern_suppression":
        suppressed = alert.count_value if alert.count_value is not None else len(alert.runs)
        body = "\n".join(
            [
                f"XAUEX pattern policy suppressed {suppressed} of 6 windows across two consecutive London trading days on {hostname}.",
                "",
                f"Affected London dates: {', '.join(alert.affected_dates)}",
                f"{alert.count_label}: {suppressed}",
                "",
                "At least four terminal HARD_BLOCKER events included PATTERN_ policy evidence.",
                "Review the recorded evidence before changing the weighted policy; this alert does not disable it automatically.",
                "",
                "Affected runs:",
                *[_display_run(run) for run in alert.runs],
            ]
        )
        message = EmailMessage()
        message["From"] = f"monit@{sender_domain}"
        message["To"] = recipient
        message["Subject"] = f"[Monit] XAUEX {alert.subject_label} on {hostname}"
        message.set_content(body, cte="8bit")
        return message

    if alert.kind == "no_trades":
        latest = alert.runs[-1] if alert.runs else None
        latest_timestamp = (
            latest.timestamp_utc.isoformat(timespec="seconds").replace("+00:00", "Z") if latest is not None else "n/a"
        )
        body = "\n".join(
            [
                f"XAUEX {alert.subject_label} detected on {hostname}.",
                "",
                f"{alert.count_label}: {alert.count_value if alert.count_value is not None else len(alert.runs)}",
                f"Latest opened trade UTC: {_utc_datetime_text(alert.latest_trade_timestamp_utc)}",
                f"No-trade London dates: {', '.join(alert.affected_dates) or 'n/a'}",
                f"Latest signal run: {latest.run_id if latest is not None else 'n/a'}",
                f"Latest signal timestamp UTC: {latest_timestamp}",
                f"Latest signal action: {latest.action if latest is not None else 'n/a'}",
                "",
                "Recent signal runs after the latest trade:",
                *[_display_run(run) for run in alert.runs[-8:]],
                "",
                "This alert means XAUEX is live enough to produce signals but has not opened trades for the configured threshold.",
            ]
        )
        message = EmailMessage()
        message["From"] = f"monit@{sender_domain}"
        message["To"] = recipient
        message["Subject"] = f"[Monit] XAUEX {alert.subject_label} on {hostname}"
        message.set_content(body, cte="8bit")
        return message

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
    journal_path: Path = DEFAULT_EVENT_JOURNAL_PATH,
    sent_state_path: Path,
    sendmail_bin: Path | None,
    smtp_host: str,
    smtp_port: int,
    hostname: str | None = None,
    sender_domain: str | None = None,
    now: datetime | None = None,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
    min_problem_runs: int = DEFAULT_MIN_PROBLEM_RUNS,
    no_trade_days: int = DEFAULT_NO_TRADE_DAYS,
) -> int:
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    sent_state = _load_json(sent_state_path)
    no_trade_threshold = max(0, int(no_trade_days or 0))
    effective_lookback_hours = max(lookback_hours, (no_trade_threshold + 4) * 24) if no_trade_threshold else lookback_hours
    runs = _load_recent_signal_runs(
        archive_root=archive_root,
        now=current_time,
        lookback_hours=effective_lookback_hours,
    )
    event_journal_events = _read_event_journal(journal_path)
    alerts = _build_alerts(
        runs=runs,
        event_journal_events=event_journal_events,
        sent_state=sent_state,
        min_problem_runs=max(1, min_problem_runs),
        no_trade_days=no_trade_threshold,
        now=current_time,
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
    parser.add_argument("--journal-path", type=Path, default=DEFAULT_EVENT_JOURNAL_PATH)
    parser.add_argument("--sent-state-path", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--sendmail-bin", type=Path)
    parser.add_argument("--smtp-host", default=DEFAULT_SMTP_HOST)
    parser.add_argument("--smtp-port", type=int, default=DEFAULT_SMTP_PORT)
    parser.add_argument("--lookback-hours", type=int, default=DEFAULT_LOOKBACK_HOURS)
    parser.add_argument("--min-problem-runs", type=int, default=DEFAULT_MIN_PROBLEM_RUNS)
    parser.add_argument("--no-trade-days", type=int, default=DEFAULT_NO_TRADE_DAYS)
    args = parser.parse_args(argv)

    try:
        return run_once(
            recipient=args.recipient,
            archive_root=args.archive_root,
            journal_path=args.journal_path,
            sent_state_path=args.sent_state_path,
            sendmail_bin=args.sendmail_bin,
            smtp_host=args.smtp_host,
            smtp_port=args.smtp_port,
            lookback_hours=args.lookback_hours,
            min_problem_runs=args.min_problem_runs,
            no_trade_days=args.no_trade_days,
        )
    except Exception as exc:
        print(f"xauex-signal-stall status=fail root_cause={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
