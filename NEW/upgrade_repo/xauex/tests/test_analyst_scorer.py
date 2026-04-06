"""Tests for analyst.setup_scorer."""
import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from analyst.setup_scorer import (
    find_new_signals,
    build_score_prompt,
    run,
)

NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

SIG_A = {
    "time_utc": "2026-03-18T09:00:00Z",
    "pattern": "PINBAR",
    "level_checked": 3300.0,
    "gate_result": "OK",
    "action": "TRADE_PLACED",
    "strategy_mode": "LEGACY_LEVELS",
}
SIG_B = {
    "time_utc": "2026-03-18T10:00:00Z",
    "pattern": "ENGULFING",
    "level_checked": 3310.0,
    "gate_result": "NEWS_BLOCK",
    "action": "SKIPPED",
    "strategy_mode": "LEGACY_LEVELS",
}

FRESH_STATE = {
    "meta": {"last_updated_utc": NOW},
    "signal_history": [SIG_B, SIG_A],  # most recent first (deque order)
    "trend": {"bias": "BULLISH"},
}


def test_find_new_signals_no_cursor():
    new = find_new_signals([SIG_A, SIG_B], cursor={"last_signal_ts": None})
    assert len(new) == 2


def test_find_new_signals_cursor_after_A():
    new = find_new_signals([SIG_A, SIG_B], cursor={"last_signal_ts": "2026-03-18T09:30:00Z"})
    assert len(new) == 1
    assert new[0]["time_utc"] == "2026-03-18T10:00:00Z"


def test_find_new_signals_all_seen():
    new = find_new_signals([SIG_A, SIG_B], cursor={"last_signal_ts": "2026-03-18T10:00:00Z"})
    assert new == []


def test_build_score_prompt_contains_rubric_dimensions():
    prompt = build_score_prompt(SIG_A, trend={"bias": "BULLISH"})
    assert "PINBAR" in prompt
    assert "3300" in prompt
    assert "score" in prompt.lower()


def test_run_scores_new_signals(tmp_path):
    state_path = str(tmp_path / "state.json")
    cursor_path = str(tmp_path / "cursor.json")
    scores_path = str(tmp_path / "setup_scores.json")
    open(state_path, "w").write(json.dumps(FRESH_STATE))

    claude_response = "Score: 7/10. Clean pinbar at weekly level, good session."
    with patch("analyst.setup_scorer.call_claude", return_value=claude_response):
        run(state_path=state_path, cursor_path=cursor_path, scores_path=scores_path)

    scores = json.loads(open(scores_path).read())
    assert len(scores) == 2
    assert scores[0]["signal"]["time_utc"] in ["2026-03-18T09:00:00Z", "2026-03-18T10:00:00Z"]


def test_run_skips_already_scored(tmp_path):
    state_path = str(tmp_path / "state.json")
    cursor_path = str(tmp_path / "cursor.json")
    scores_path = str(tmp_path / "setup_scores.json")
    open(state_path, "w").write(json.dumps(FRESH_STATE))
    open(cursor_path, "w").write(json.dumps({"last_signal_ts": "2026-03-18T10:00:00Z"}))

    with patch("analyst.setup_scorer.call_claude") as mock_call:
        run(state_path=state_path, cursor_path=cursor_path, scores_path=scores_path)
        mock_call.assert_not_called()


def test_run_stale_state_skips(tmp_path):
    state = {"meta": {"last_updated_utc": "2020-01-01T00:00:00Z"}, "signal_history": [SIG_A]}
    state_path = str(tmp_path / "state.json")
    cursor_path = str(tmp_path / "cursor.json")
    scores_path = str(tmp_path / "setup_scores.json")
    open(state_path, "w").write(json.dumps(state))

    with patch("analyst.setup_scorer.call_claude") as mock_call:
        run(state_path=state_path, cursor_path=cursor_path, scores_path=scores_path)
        mock_call.assert_not_called()
