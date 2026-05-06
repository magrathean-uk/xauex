"""State-write coalescing helpers for the XAUEX bot."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WriteDecision:
    should_write: bool
    reason: str


class StateWriteCoalescer:
    """Decides whether a full state write is necessary."""

    def __init__(self, *, min_interval_seconds: float = 1.0):
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self._last_write_time = 0.0
        self._last_digest = ""

    @staticmethod
    def digest(payload: Any) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def decide(self, payload: Any, *, critical: bool = False, now: float | None = None) -> WriteDecision:
        now = time.monotonic() if now is None else now
        digest = self.digest(payload)
        if critical:
            self._last_write_time = now
            self._last_digest = digest
            return WriteDecision(True, "critical")
        if digest == self._last_digest:
            return WriteDecision(False, "unchanged")
        if self._last_write_time and now - self._last_write_time < self.min_interval_seconds:
            return WriteDecision(False, "coalesced")
        self._last_write_time = now
        self._last_digest = digest
        return WriteDecision(True, "changed")
