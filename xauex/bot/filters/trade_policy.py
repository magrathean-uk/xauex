"""Claude-produced trade policy for XAUUSD execution behavior."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Optional, Tuple


@dataclass
class TradePolicy:
    """Structured execution policy read from trade_policy.json."""

    mode: str
    direction: str
    aggressiveness: float
    allow_reentry: bool
    pullback_zone_multiplier: float
    sl_buffer_multiplier: float
    tp_rr_multiplier: float
    generated_at_utc: datetime
    expires_utc: Optional[datetime] = None
    block_new_entries_until_utc: Optional[datetime] = None
    summary: str = ""

    def direction_allows(self, direction: int) -> bool:
        if self.direction == "BOTH":
            return True
        if self.direction == "LONG_ONLY":
            return direction > 0
        if self.direction == "SHORT_ONLY":
            return direction < 0
        return False

    def to_state_dict(self) -> dict:
        return {
            "mode": self.mode,
            "direction": self.direction,
            "aggressiveness": round(self.aggressiveness, 2),
            "allow_reentry": self.allow_reentry,
            "pullback_zone_multiplier": round(self.pullback_zone_multiplier, 2),
            "sl_buffer_multiplier": round(self.sl_buffer_multiplier, 2),
            "tp_rr_multiplier": round(self.tp_rr_multiplier, 2),
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
            "summary": self.summary,
        }


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


class TradePolicyLoader:
    """Load and validate a fresh trade policy file."""

    def __init__(self, path: str, max_age_minutes: int):
        self.path = Path(path)
        self.max_age = timedelta(minutes=max_age_minutes)

    def load(self, now_utc: Optional[datetime] = None) -> Optional[TradePolicy]:
        now_utc = now_utc or datetime.now(timezone.utc)
        try:
            payload = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

        try:
            policy = TradePolicy(
                mode=str(payload.get("mode", "NORMAL")).upper(),
                direction=str(payload.get("direction", "BOTH")).upper(),
                aggressiveness=float(payload.get("aggressiveness", 0.5)),
                allow_reentry=bool(payload.get("allow_reentry", True)),
                pullback_zone_multiplier=float(payload.get("pullback_zone_multiplier", 1.0)),
                sl_buffer_multiplier=float(payload.get("sl_buffer_multiplier", 1.0)),
                tp_rr_multiplier=float(payload.get("tp_rr_multiplier", 1.0)),
                generated_at_utc=_parse_ts(payload["generated_at_utc"]),
                expires_utc=_parse_ts(payload.get("expires_utc")),
                block_new_entries_until_utc=_parse_ts(payload.get("block_new_entries_until_utc")),
                summary=str(payload.get("summary", "")).strip(),
            )
        except Exception:
            return None

        if policy.generated_at_utc is None:
            return None
        if now_utc - policy.generated_at_utc > self.max_age:
            return None
        if policy.expires_utc and policy.expires_utc <= now_utc:
            return None
        return policy

    @staticmethod
    def gate_direction(
        policy: Optional[TradePolicy],
        *,
        direction: int,
        now_utc: datetime,
    ) -> Tuple[bool, Optional[str]]:
        if policy is None:
            return True, None
        if not policy.direction_allows(direction):
            return False, "POLICY_DIRECTION_BLOCK"
        return True, None
