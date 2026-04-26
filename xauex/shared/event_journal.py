"""Append-only JSONL event journal helpers for XAUEX runtime/replay."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

DEFAULT_EVENT_JOURNAL_PATH = Path(os.getenv("XAUEX_EVENT_JOURNAL_PATH", "/var/lib/xauex/events.jsonl"))
EVENT_SCHEMA_VERSION = 1


def utc_timestamp(value: datetime | str | None = None) -> str:
    if value is None:
        dt = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        dt = value.astimezone(timezone.utc)
    else:
        text = str(value)
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return text
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return utc_timestamp(value)
    if hasattr(value, "name") and hasattr(value, "value"):
        return str(value.name)
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def append_event(
    path: str | Path | None,
    *,
    source: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    correlation_id: str | None = None,
    event_id: str | None = None,
    timestamp_utc: datetime | str | None = None,
) -> dict[str, Any]:
    """Append one JSON event and return the written envelope."""
    envelope = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event_id": event_id or str(uuid.uuid4()),
        "correlation_id": str(correlation_id or event_id or ""),
        "timestamp_utc": utc_timestamp(timestamp_utc),
        "source": source,
        "event_type": event_type,
        "payload": _json_safe(payload or {}),
    }
    target = Path(path) if path is not None else DEFAULT_EVENT_JOURNAL_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(envelope, sort_keys=True, separators=(",", ":")))
        handle.write("\n")
    return envelope


def safe_append_event(
    path: str | Path | None,
    *,
    source: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    correlation_id: str | None = None,
    event_id: str | None = None,
    timestamp_utc: datetime | str | None = None,
) -> dict[str, Any] | None:
    """Append without allowing journal I/O failures to interrupt trading."""
    try:
        return append_event(
            path,
            source=source,
            event_type=event_type,
            payload=payload,
            correlation_id=correlation_id,
            event_id=event_id,
            timestamp_utc=timestamp_utc,
        )
    except Exception as exc:
        logger.warning("[JOURNAL] Failed to append %s: %s", event_type, exc)
        return None


def read_events(path: str | Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        lines: Iterable[str] = Path(path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events
