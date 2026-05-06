"""Append-only JSONL scratchpad helpers for auditable signal decisions."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def append_scratchpad_event(path: Path | str | None, event: dict[str, Any]) -> bool:
    """Append one scratchpad event and never raise to callers."""
    if path is None:
        return False
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            **event,
        }
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
        return True
    except Exception as exc:  # pragma: no cover - intentionally non-fatal
        logger.warning("[SCRATCHPAD] Failed to append scratchpad event: %s", exc)
        return False


def record_stage_start(path: Path | str | None, *, stage: str, prompt: str) -> bool:
    return append_scratchpad_event(
        path,
        {
            "event": "stage_start",
            "stage": stage,
            "prompt": str(prompt),
        },
    )


def record_stage_result(
    path: Path | str | None,
    *,
    stage: str,
    parsed: dict[str, Any],
    response_mode: str = "",
    usage: dict[str, Any] | None = None,
    retries: int = 0,
) -> bool:
    return append_scratchpad_event(
        path,
        {
            "event": "stage_result",
            "stage": stage,
            "parsed": parsed,
            "response_mode": response_mode,
            "usage": usage or {},
            "retries": retries,
        },
    )


def record_stage_error(
    path: Path | str | None,
    *,
    stage: str,
    error: str,
    response_mode: str = "",
    retries: int = 0,
) -> bool:
    return append_scratchpad_event(
        path,
        {
            "event": "stage_error",
            "stage": stage,
            "error": str(error),
            "response_mode": response_mode,
            "retries": retries,
        },
    )


def record_final_decision(path: Path | str | None, *, decision: dict[str, Any]) -> bool:
    return append_scratchpad_event(
        path,
        {
            "event": "final_decision",
            "decision": decision,
        },
    )
