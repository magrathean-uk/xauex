"""Signed dashboard manual-command envelopes."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from xauex.shared.event_journal import safe_append_event, utc_timestamp

MANUAL_COMMAND_SCHEMA_VERSION = 1
DEFAULT_MANUAL_COMMAND_TTL_SECONDS = 180


@dataclass(frozen=True)
class ManualCommandConsumeResult:
    payload: dict[str, Any] | None
    rejection_reason: str | None = None
    command_id: str | None = None
    file_found: bool = False


def _canonical_bytes(envelope: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in envelope.items() if key != "signature"}
    return json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _signature(envelope: dict[str, Any], secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), _canonical_bytes(envelope), hashlib.sha256).hexdigest()
    return f"sha256:{digest}"


def create_signed_manual_command(
    payload: dict[str, Any],
    *,
    secret: str,
    command_id: str | None = None,
    now_utc: datetime | None = None,
    ttl_seconds: int = DEFAULT_MANUAL_COMMAND_TTL_SECONDS,
) -> dict[str, Any]:
    if not secret:
        raise ValueError("manual command secret is required")
    now_utc = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    envelope = {
        "schema_version": MANUAL_COMMAND_SCHEMA_VERSION,
        "command_id": command_id or str(uuid.uuid4()),
        "created_at_utc": utc_timestamp(now_utc),
        "expires_at_utc": utc_timestamp(now_utc + timedelta(seconds=int(ttl_seconds))),
        "payload": dict(payload),
    }
    envelope["signature"] = _signature(envelope, secret)
    return envelope


def verify_signed_manual_command(
    envelope: dict[str, Any],
    *,
    secret: str,
    seen_command_ids: set[str] | None = None,
    now_utc: datetime | None = None,
) -> ManualCommandConsumeResult:
    command_id = str(envelope.get("command_id") or "").strip() or None
    if not secret:
        return ManualCommandConsumeResult(None, "MANUAL_COMMAND_SECRET_UNSET", command_id, True)
    if not isinstance(envelope, dict):
        return ManualCommandConsumeResult(None, "MALFORMED_COMMAND", None, True)
    if "payload" not in envelope and "signature" not in envelope and "command" in envelope:
        return ManualCommandConsumeResult(None, "UNSIGNED_LEGACY_COMMAND", None, True)
    if envelope.get("schema_version") != MANUAL_COMMAND_SCHEMA_VERSION:
        return ManualCommandConsumeResult(None, "UNSUPPORTED_SCHEMA_VERSION", command_id, True)
    payload = envelope.get("payload")
    if not command_id or not isinstance(payload, dict) or not envelope.get("signature"):
        return ManualCommandConsumeResult(None, "MALFORMED_COMMAND", command_id, True)
    if seen_command_ids is not None and command_id in seen_command_ids:
        return ManualCommandConsumeResult(None, "DUPLICATE_COMMAND", command_id, True)
    try:
        expires_at = datetime.fromisoformat(str(envelope.get("expires_at_utc", "")).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return ManualCommandConsumeResult(None, "MALFORMED_COMMAND", command_id, True)
    now_utc = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if now_utc > expires_at:
        return ManualCommandConsumeResult(None, "EXPIRED", command_id, True)
    expected = _signature(envelope, secret)
    if not hmac.compare_digest(str(envelope.get("signature")), expected):
        return ManualCommandConsumeResult(None, "BAD_SIGNATURE", command_id, True)
    if seen_command_ids is not None:
        seen_command_ids.add(command_id)
    return ManualCommandConsumeResult(dict(payload), None, command_id, True)


def consume_manual_command_file(
    path: str | Path,
    *,
    secret: str | None = None,
    seen_command_ids: set[str] | None = None,
    now_utc: datetime | None = None,
    journal_path: str | Path | None = None,
) -> ManualCommandConsumeResult:
    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ManualCommandConsumeResult(None, None, None, False)
    except OSError:
        return ManualCommandConsumeResult(None, "READ_ERROR", None, True)

    try:
        target.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass

    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        result = ManualCommandConsumeResult(None, "MALFORMED_COMMAND", None, True)
        _journal_consume_result(result, journal_path)
        return result

    if not isinstance(envelope, dict):
        result = ManualCommandConsumeResult(None, "MALFORMED_COMMAND", None, True)
        _journal_consume_result(result, journal_path)
        return result

    result = verify_signed_manual_command(
        envelope,
        secret=secret if secret is not None else os.getenv("XAUEX_MANUAL_COMMAND_SECRET", ""),
        seen_command_ids=seen_command_ids,
        now_utc=now_utc,
    )
    _journal_consume_result(result, journal_path)
    return result


def _journal_consume_result(result: ManualCommandConsumeResult, journal_path: str | Path | None) -> None:
    if not result.file_found or journal_path is None:
        return
    if result.payload is None:
        safe_append_event(
            journal_path,
            source="bot",
            event_type="manual_command_rejected",
            correlation_id=result.command_id,
            payload={"command_id": result.command_id, "reason": result.rejection_reason},
        )
    else:
        safe_append_event(
            journal_path,
            source="bot",
            event_type="manual_command_received",
            correlation_id=result.command_id,
            payload={"command_id": result.command_id, "command": result.payload.get("command", "open")},
        )
