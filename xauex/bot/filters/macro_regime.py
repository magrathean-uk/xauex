"""Read Claude-produced macro regime hints for deterministic trade gating."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Optional, Tuple


@dataclass
class MacroRegime:
    """Structured macro regime state read from macro_regime.json."""

    regime: str
    confidence: float
    summary: str
    generated_at_utc: datetime
    expires_utc: Optional[datetime] = None
    block_new_entries_until_utc: Optional[datetime] = None

    @property
    def bias(self) -> Optional[int]:
        if self.regime == "XAU_BULLISH":
            return 1
        if self.regime == "XAU_BEARISH":
            return -1
        return None

    def to_state_dict(self) -> dict:
        return {
            "regime": self.regime,
            "confidence": round(self.confidence, 2),
            "summary": self.summary,
            "generated_at_utc": self.generated_at_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_utc": (
                self.expires_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                if self.expires_utc
                else None
            ),
            "block_new_entries_until_utc": (
                self.block_new_entries_until_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                if self.block_new_entries_until_utc
                else None
            ),
        }


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


class MacroRegimeLoader:
    """Load and validate a fresh macro regime file."""

    def __init__(self, path: str, max_age_minutes: int):
        self.path = Path(path)
        self.max_age = timedelta(minutes=max_age_minutes)

    def load(self, now_utc: Optional[datetime] = None) -> Optional[MacroRegime]:
        now_utc = now_utc or datetime.now(timezone.utc)
        try:
            payload = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

        try:
            regime = MacroRegime(
                regime=payload["regime"],
                confidence=float(payload.get("confidence", 0.0)),
                summary=str(payload.get("summary", "")).strip(),
                generated_at_utc=_parse_ts(payload["generated_at_utc"]),
                expires_utc=_parse_ts(payload.get("expires_utc")),
                block_new_entries_until_utc=_parse_ts(payload.get("block_new_entries_until_utc")),
            )
        except Exception:
            return None

        if regime.generated_at_utc is None:
            return None
        if now_utc - regime.generated_at_utc > self.max_age:
            return None
        if regime.expires_utc and regime.expires_utc <= now_utc:
            return None
        return regime

    @staticmethod
    def gate_direction(
        regime: Optional[MacroRegime],
        *,
        direction: int,
        now_utc: datetime,
        confidence_threshold: float,
    ) -> Tuple[bool, Optional[str]]:
        if regime is None:
            return True, None
        if regime.confidence < confidence_threshold:
            return True, None
        bias = regime.bias
        if bias is None:
            return True, None
        if bias != direction:
            return False, "MACRO_DIRECTION_BLOCK"
        return True, None
