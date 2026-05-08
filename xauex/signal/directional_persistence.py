"""Cross-window directional persistence for XAUEX intraday signals.

Three windows fire each London trading day (MORNING, MIDDAY, US_OPEN). Each
historically decided independently, which produced same-day flip-flops like
SELL → BUY → SELL with no edge — every flip stops out before the next
window's signal completes.

This module implements a thin policy layer that:

1. Locks the day's primary direction once a window opens with confidence
   above ``DEFAULT_LOCK_CONFIDENCE_THRESHOLD`` (0.50).
2. Allows aligned subsequent signals through unchanged.
3. Blocks counter-direction signals unless they exceed
   ``DEFAULT_FLIP_CONFIDENCE_THRESHOLD`` (0.65) AND the macro signature has
   changed direction (DXY sign flip and/or yields sign flip).
4. Resets when the London calendar day rolls over.

The policy is pure-function: the I/O layer reads/writes the state file and
the caller chooses when to apply the policy.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


DEFAULT_LOCK_CONFIDENCE_THRESHOLD = 0.50
DEFAULT_FLIP_CONFIDENCE_THRESHOLD = 0.65


@dataclass(frozen=True)
class DirectionalDecision:
    """Result of applying the persistence policy to a proposed signal."""

    action: str
    confidence: float
    policy: str
    reason: str
    next_state: Optional[dict[str, Any]]


def same_london_day(state_date: Optional[str], current_date: Optional[str]) -> bool:
    if not state_date or not current_date:
        return False
    return str(state_date) == str(current_date)


def _macro_flipped(
    *,
    state_signature: Optional[dict[str, Any]],
    proposed_signature: Optional[dict[str, Any]],
) -> bool:
    """Return True only if at least one tracked macro driver has its sign flipped
    between the locked state and the proposed signal. We treat 0/None as
    "no signal" — they don't count as a flip."""
    if not isinstance(state_signature, dict) or not isinstance(proposed_signature, dict):
        return False
    flips = 0
    for key, prior_value in state_signature.items():
        new_value = proposed_signature.get(key)
        if prior_value in (None, 0) or new_value in (None, 0):
            continue
        try:
            prior_sign = 1 if float(prior_value) > 0 else -1 if float(prior_value) < 0 else 0
            new_sign = 1 if float(new_value) > 0 else -1 if float(new_value) < 0 else 0
        except (TypeError, ValueError):
            continue
        if prior_sign != 0 and new_sign != 0 and prior_sign != new_sign:
            flips += 1
    return flips >= 1


def apply_directional_persistence(
    *,
    current_state: Optional[dict[str, Any]],
    proposed_action: str,
    proposed_confidence: float,
    proposed_macro_signature: Optional[dict[str, Any]],
    now_utc,
    london_date: str,
    window_label: str,
    lock_confidence_threshold: float = DEFAULT_LOCK_CONFIDENCE_THRESHOLD,
    flip_confidence_threshold: float = DEFAULT_FLIP_CONFIDENCE_THRESHOLD,
) -> DirectionalDecision:
    action = (proposed_action or "HOLD").upper()
    confidence = max(0.0, min(1.0, float(proposed_confidence or 0.0)))

    # HOLD never modifies state and is always allowed through.
    if action == "HOLD":
        return DirectionalDecision(
            action="HOLD",
            confidence=confidence,
            policy="HOLD_PASS_THROUGH",
            reason="HOLD signals do not affect directional persistence.",
            next_state=None,
        )

    # State from a previous London day cannot bind today's signals.
    state_active = (
        current_state is not None
        and same_london_day(current_state.get("date_london"), london_date)
    )

    if not state_active:
        # No active lock for today. Either set the lock or pass through quietly.
        if confidence >= lock_confidence_threshold and action in {"BUY", "SELL"}:
            new_state = _build_state(
                action=action,
                confidence=confidence,
                macro_signature=proposed_macro_signature,
                now_utc=now_utc,
                london_date=london_date,
                window_label=window_label,
            )
            return DirectionalDecision(
                action=action,
                confidence=confidence,
                policy="LOCK_DIRECTION",
                reason=f"Locked {action} for {london_date} at confidence {confidence:.2f}.",
                next_state=new_state,
            )
        return DirectionalDecision(
            action=action,
            confidence=confidence,
            policy="LOW_CONFIDENCE_NO_LOCK",
            reason=f"Confidence {confidence:.2f} below lock threshold {lock_confidence_threshold:.2f}.",
            next_state=None,
        )

    # State is active for today.
    locked_direction = str((current_state or {}).get("primary_direction") or "").upper()

    if action == locked_direction:
        return DirectionalDecision(
            action=action,
            confidence=confidence,
            policy="ALIGNED_WITH_LOCK",
            reason=f"Proposed {action} aligned with locked direction.",
            next_state=None,
        )

    # Opposite direction. Need both high confidence and macro flip.
    if confidence < flip_confidence_threshold:
        return DirectionalDecision(
            action="HOLD",
            confidence=0.0,
            policy="FLIP_BLOCKED_LOW_CONFIDENCE",
            reason=(
                f"Persistence flip blocked: proposed {action} confidence {confidence:.2f} "
                f"below flip threshold {flip_confidence_threshold:.2f} (locked={locked_direction})."
            ),
            next_state=None,
        )

    if not _macro_flipped(
        state_signature=(current_state or {}).get("macro_signature"),
        proposed_signature=proposed_macro_signature,
    ):
        return DirectionalDecision(
            action="HOLD",
            confidence=0.0,
            policy="FLIP_BLOCKED_MACRO_UNCHANGED",
            reason=(
                f"Persistence flip blocked: proposed {action} confidence {confidence:.2f} ≥ "
                f"flip threshold but macro signature has not flipped from the locked direction."
            ),
            next_state=None,
        )

    new_state = _build_state(
        action=action,
        confidence=confidence,
        macro_signature=proposed_macro_signature,
        now_utc=now_utc,
        london_date=london_date,
        window_label=window_label,
    )
    return DirectionalDecision(
        action=action,
        confidence=confidence,
        policy="FLIP_ALLOWED_MACRO_CONFIRMED",
        reason=(
            f"Flip allowed: confidence {confidence:.2f} ≥ {flip_confidence_threshold:.2f} "
            f"and macro signature flipped from {locked_direction} thesis."
        ),
        next_state=new_state,
    )


def _build_state(
    *,
    action: str,
    confidence: float,
    macro_signature: Optional[dict[str, Any]],
    now_utc,
    london_date: str,
    window_label: str,
) -> dict[str, Any]:
    set_at = now_utc.astimezone(now_utc.tzinfo).strftime("%Y-%m-%dT%H:%M:%SZ") if now_utc else ""
    return {
        "date_london": london_date,
        "primary_direction": action,
        "set_at_utc": set_at,
        "set_by_window": window_label,
        "confidence": round(confidence, 4),
        "macro_signature": dict(macro_signature or {}),
    }


def load_directional_state(path: Path) -> Optional[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("[DIRECTIONAL_STATE] Failed to load %s: %s", target, exc)
        return None


def record_directional_state(path: Path, state: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_suffix(target.suffix + ".tmp")
    tmp_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(target)


def macro_signature_from_market_snapshot(market_snapshot: Optional[dict[str, Any]]) -> dict[str, int]:
    """Extract the sign of each tracked driver for persistence comparisons.

    A flip on either DXY (USD broad index) or the 10Y yield is a meaningful
    macro change for gold; we ignore micro-noise on series unrelated to the
    primary gold thesis.
    """
    series = (market_snapshot or {}).get("series") or {}
    signature: dict[str, int] = {}
    for series_key, signature_key in (
        ("usd_broad_index", "dxy_sign"),
        ("us10y_yield", "yields_sign"),
        ("us10y_real_yield", "real_yields_sign"),
    ):
        row = series.get(series_key) or {}
        change_1d = row.get("change_1d")
        try:
            change = float(change_1d) if change_1d is not None else 0.0
        except (TypeError, ValueError):
            change = 0.0
        if change > 0:
            signature[signature_key] = 1
        elif change < 0:
            signature[signature_key] = -1
        else:
            signature[signature_key] = 0
    return signature
