"""Cross-window directional persistence tests.

The May 2026 incident showed three direction flips in one trading day
(MORNING SELL → MIDDAY BUY → US_OPEN SELL), which produced no edge and
amplified whipsaw losses. The persistence layer locks the day's primary
direction once a window opens with reasonable conviction and refuses
counter-direction entries unless they pass a higher confidence threshold
and a freshness check on the macro flip.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from xauex.signal.directional_persistence import (
    DEFAULT_LOCK_CONFIDENCE_THRESHOLD,
    DEFAULT_FLIP_CONFIDENCE_THRESHOLD,
    apply_directional_persistence,
    load_directional_state,
    record_directional_state,
    same_london_day,
)


def test_first_window_records_state_when_confidence_above_lock_threshold():
    state = None
    decision = apply_directional_persistence(
        current_state=state,
        proposed_action="BUY",
        proposed_confidence=0.55,
        proposed_macro_signature={"dxy_sign": -1, "yields_sign": -1},
        now_utc=datetime(2026, 5, 8, 7, 0, 0, tzinfo=timezone.utc),
        london_date="2026-05-08",
        window_label="morning",
    )
    assert decision.action == "BUY"
    assert decision.confidence == 0.55
    assert decision.policy == "LOCK_DIRECTION"
    assert decision.next_state is not None
    assert decision.next_state["primary_direction"] == "BUY"
    assert decision.next_state["set_by_window"] == "morning"


def test_second_window_keeps_direction_when_aligned():
    state = {
        "date_london": "2026-05-08",
        "primary_direction": "BUY",
        "set_at_utc": "2026-05-08T07:00:06Z",
        "set_by_window": "morning",
        "confidence": 0.55,
        "macro_signature": {"dxy_sign": -1, "yields_sign": -1},
    }
    decision = apply_directional_persistence(
        current_state=state,
        proposed_action="BUY",
        proposed_confidence=0.62,
        proposed_macro_signature={"dxy_sign": -1, "yields_sign": -1},
        now_utc=datetime(2026, 5, 8, 11, 30, 0, tzinfo=timezone.utc),
        london_date="2026-05-08",
        window_label="midday",
    )
    assert decision.action == "BUY"
    assert decision.policy == "ALIGNED_WITH_LOCK"


def test_second_window_blocks_flip_when_confidence_below_threshold():
    """The MIDDAY=BUY 0.68 → US_OPEN=SELL 0.48 flip seen in the May 8 incident
    should be blocked because the new confidence is below the flip threshold."""
    state = {
        "date_london": "2026-05-08",
        "primary_direction": "BUY",
        "set_at_utc": "2026-05-08T10:30:08Z",
        "set_by_window": "midday",
        "confidence": 0.68,
        "macro_signature": {"dxy_sign": -1, "yields_sign": -1},
    }
    decision = apply_directional_persistence(
        current_state=state,
        proposed_action="SELL",
        proposed_confidence=0.48,
        proposed_macro_signature={"dxy_sign": -1, "yields_sign": -1},  # macro unchanged
        now_utc=datetime(2026, 5, 8, 12, 30, 0, tzinfo=timezone.utc),
        london_date="2026-05-08",
        window_label="us_open",
    )
    assert decision.action == "HOLD"
    assert decision.confidence == 0.0
    assert decision.policy == "FLIP_BLOCKED_LOW_CONFIDENCE"
    assert "flip" in decision.reason.lower() or "persistence" in decision.reason.lower()


def test_second_window_allows_flip_when_confidence_high_and_macro_flipped():
    """A high-conviction flip backed by a fresh macro signature change is
    legitimate — the policy must let it through."""
    state = {
        "date_london": "2026-05-08",
        "primary_direction": "BUY",
        "set_at_utc": "2026-05-08T07:00:06Z",
        "set_by_window": "morning",
        "confidence": 0.55,
        "macro_signature": {"dxy_sign": -1, "yields_sign": -1},
    }
    decision = apply_directional_persistence(
        current_state=state,
        proposed_action="SELL",
        proposed_confidence=DEFAULT_FLIP_CONFIDENCE_THRESHOLD + 0.05,
        proposed_macro_signature={"dxy_sign": +1, "yields_sign": +1},  # both flipped
        now_utc=datetime(2026, 5, 8, 12, 30, 0, tzinfo=timezone.utc),
        london_date="2026-05-08",
        window_label="us_open",
    )
    assert decision.action == "SELL"
    assert decision.policy == "FLIP_ALLOWED_MACRO_CONFIRMED"
    assert decision.next_state is not None
    assert decision.next_state["primary_direction"] == "SELL"


def test_state_resets_on_new_london_day():
    """A state from yesterday must not bind today's first window."""
    state = {
        "date_london": "2026-05-07",
        "primary_direction": "BUY",
        "set_at_utc": "2026-05-07T07:00:06Z",
        "set_by_window": "morning",
        "confidence": 0.55,
        "macro_signature": {"dxy_sign": -1, "yields_sign": -1},
    }
    decision = apply_directional_persistence(
        current_state=state,
        proposed_action="SELL",
        proposed_confidence=0.55,
        proposed_macro_signature={"dxy_sign": +1, "yields_sign": +1},
        now_utc=datetime(2026, 5, 8, 7, 0, 0, tzinfo=timezone.utc),
        london_date="2026-05-08",
        window_label="morning",
    )
    assert decision.action == "SELL"
    assert decision.policy == "LOCK_DIRECTION"
    assert decision.next_state is not None
    assert decision.next_state["date_london"] == "2026-05-08"


def test_low_confidence_initial_signal_does_not_lock_direction():
    """Below the lock threshold, the day stays unbound so a stronger signal
    in a later window can still set direction freely."""
    state = None
    decision = apply_directional_persistence(
        current_state=state,
        proposed_action="BUY",
        proposed_confidence=DEFAULT_LOCK_CONFIDENCE_THRESHOLD - 0.05,
        proposed_macro_signature={},
        now_utc=datetime(2026, 5, 8, 7, 0, 0, tzinfo=timezone.utc),
        london_date="2026-05-08",
        window_label="morning",
    )
    assert decision.action == "BUY"
    assert decision.policy == "LOW_CONFIDENCE_NO_LOCK"
    assert decision.next_state is None  # nothing recorded


def test_hold_action_passes_through_unchanged_with_no_state_change():
    state = {
        "date_london": "2026-05-08",
        "primary_direction": "BUY",
        "set_at_utc": "2026-05-08T07:00:06Z",
        "set_by_window": "morning",
        "confidence": 0.6,
        "macro_signature": {"dxy_sign": -1, "yields_sign": -1},
    }
    decision = apply_directional_persistence(
        current_state=state,
        proposed_action="HOLD",
        proposed_confidence=0.0,
        proposed_macro_signature={"dxy_sign": -1, "yields_sign": -1},
        now_utc=datetime(2026, 5, 8, 11, 30, 0, tzinfo=timezone.utc),
        london_date="2026-05-08",
        window_label="midday",
    )
    assert decision.action == "HOLD"
    assert decision.policy == "HOLD_PASS_THROUGH"
    assert decision.next_state is None


def test_load_and_record_directional_state_round_trip(tmp_path: Path):
    target = tmp_path / "directional_state.json"
    initial = load_directional_state(target)
    assert initial is None

    new_state = {
        "date_london": "2026-05-08",
        "primary_direction": "BUY",
        "set_at_utc": "2026-05-08T07:00:06Z",
        "set_by_window": "morning",
        "confidence": 0.55,
        "macro_signature": {"dxy_sign": -1, "yields_sign": -1},
    }
    record_directional_state(target, new_state)
    loaded = load_directional_state(target)
    assert loaded == new_state


def test_load_directional_state_returns_none_for_invalid_json(tmp_path: Path):
    target = tmp_path / "directional_state.json"
    target.write_text("{not valid json")
    assert load_directional_state(target) is None


def test_same_london_day_helper_handles_string_and_missing():
    assert same_london_day("2026-05-08", "2026-05-08") is True
    assert same_london_day("2026-05-08", "2026-05-09") is False
    assert same_london_day(None, "2026-05-09") is False
    assert same_london_day("2026-05-08", "") is False
