"""Tests for analyst.weekly_review."""
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from analyst.weekly_review import (
    get_previous_week_bounds,
    filter_to_week,
    build_review_prompt,
    run,
)

# Monday and Sunday of a known week
MON = datetime(2026, 3, 9, 0, 0, 0, tzinfo=timezone.utc)
SUN = datetime(2026, 3, 15, 23, 59, 59, tzinfo=timezone.utc)

JOURNAL_ENTRIES = [
    {"trade_id": "p1", "journalled_at_utc": "2026-03-10T11:00:00Z", "journal": "Clean trade.", "entry": {"pnl": 140.0}},
    {"trade_id": "p2", "journalled_at_utc": "2026-03-11T14:00:00Z", "journal": "Loss on news.", "entry": {"pnl": -80.0}},
    {"trade_id": "p3", "journalled_at_utc": "2026-03-17T10:00:00Z", "journal": "This week.", "entry": {"pnl": 50.0}},  # outside week
]

SCORE_ENTRIES = [
    {"signal_ts": "2026-03-10T09:00:00Z", "scored_at_utc": "2026-03-10T09:05:00Z", "breakdown": "Score: 8/10"},
    {"signal_ts": "2026-03-17T09:00:00Z", "scored_at_utc": "2026-03-17T09:05:00Z", "breakdown": "Score: 6/10"},  # outside week
]

FRESH_STATE = {
    "meta": {"last_updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
    "risk": {"weekly_pnl": 60.0, "consecutive_losses_today": 0},
    "signal_history": [],
    "shadow_signal_history": [],
}


def test_get_previous_week_bounds():
    # Run on Monday 2026-03-16: previous week is Mon 2026-03-09 to Sun 2026-03-15
    run_at = datetime(2026, 3, 16, 8, 0, 0, tzinfo=timezone.utc)
    start, end = get_previous_week_bounds(run_at)
    assert start == datetime(2026, 3, 9, 0, 0, 0, tzinfo=timezone.utc)
    assert end.date().isoformat() == "2026-03-15"


def test_filter_to_week_journal():
    filtered = filter_to_week(JOURNAL_ENTRIES, "journalled_at_utc", MON, SUN)
    assert len(filtered) == 2
    ids = [e["trade_id"] for e in filtered]
    assert "p1" in ids and "p2" in ids
    assert "p3" not in ids


def test_filter_to_week_scores():
    filtered = filter_to_week(SCORE_ENTRIES, "scored_at_utc", MON, SUN)
    assert len(filtered) == 1
    assert filtered[0]["signal_ts"] == "2026-03-10T09:00:00Z"


def test_build_review_prompt_contains_key_fields():
    journal = filter_to_week(JOURNAL_ENTRIES, "journalled_at_utc", MON, SUN)
    scores = filter_to_week(SCORE_ENTRIES, "scored_at_utc", MON, SUN)
    prompt = build_review_prompt(FRESH_STATE, journal, scores, MON, SUN)
    assert "2026-03-09" in prompt
    assert "2026-03-15" in prompt
    assert "Clean trade" in prompt
    assert "8/10" in prompt


def test_run_writes_output(tmp_path):
    state_path = str(tmp_path / "state.json")
    journal_path = str(tmp_path / "trade_journal.json")
    scores_path = str(tmp_path / "setup_scores.json")
    output_path = str(tmp_path / "weekly_review.json")
    open(state_path, "w").write(json.dumps(FRESH_STATE))
    open(journal_path, "w").write(json.dumps(JOURNAL_ENTRIES))
    open(scores_path, "w").write(json.dumps(SCORE_ENTRIES))

    run_at = datetime(2026, 3, 16, 8, 0, 0, tzinfo=timezone.utc)
    with patch("analyst.weekly_review.call_claude", return_value="Week was profitable."):
        run(
            state_path=state_path,
            journal_path=journal_path,
            scores_path=scores_path,
            output_path=output_path,
            _run_at=run_at,
        )

    result = json.loads(open(output_path).read())
    assert result["review"] == "Week was profitable."
    assert result["model"] == "claude-opus-4-6"
    assert result["week_ending"] == "2026-03-15"
