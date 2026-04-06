"""Tests for analyst.post_trade_journal."""
import json
import os
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from analyst.post_trade_journal import (
    find_new_trades,
    build_trade_prompt,
    run,
)

TRADE_A = {
    "position_id": "pos001",
    "direction": "LONG",
    "entry_price": 3301.0,
    "close_price": 3315.0,
    "stop_loss": 3291.0,
    "take_profit": 3315.0,
    "lot_size": 0.01,
    "pnl": 140.0,
    "pattern": "PINBAR",
    "level": 3300.0,
    "close_time_utc": "2026-03-18T11:00:00Z",
}

TRADE_B = {
    "position_id": "pos002",
    "direction": "SHORT",
    "entry_price": 3310.0,
    "close_price": 3300.0,
    "stop_loss": 3320.0,
    "take_profit": 3296.0,
    "lot_size": 0.01,
    "pnl": 100.0,
    "pattern": "ENGULFING",
    "level": 3310.0,
    "close_time_utc": "2026-03-18T14:00:00Z",
}

FRESH_STATE = {
    "meta": {"last_updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
    "closed_trades_today": [TRADE_A, TRADE_B],
}


def test_find_new_trades_all_new():
    cursor = {"journalled_ids": []}
    new = find_new_trades([TRADE_A, TRADE_B], cursor)
    assert len(new) == 2


def test_find_new_trades_some_already_journalled():
    cursor = {"journalled_ids": ["pos001"]}
    new = find_new_trades([TRADE_A, TRADE_B], cursor)
    assert len(new) == 1
    assert new[0]["position_id"] == "pos002"


def test_find_new_trades_all_journalled():
    cursor = {"journalled_ids": ["pos001", "pos002"]}
    new = find_new_trades([TRADE_A, TRADE_B], cursor)
    assert new == []


def test_build_trade_prompt_contains_key_fields():
    prompt = build_trade_prompt(TRADE_A)
    assert "3301" in prompt      # entry price
    assert "PINBAR" in prompt
    assert "LONG" in prompt
    assert "140" in prompt       # pnl


def test_run_journals_new_trades(tmp_path):
    state_path = str(tmp_path / "state.json")
    cursor_path = str(tmp_path / "cursor.json")
    journal_path = str(tmp_path / "trade_journal.json")
    open(state_path, "w").write(json.dumps(FRESH_STATE))

    with patch("analyst.post_trade_journal.call_claude", return_value="Clean pinbar entry."):
        run(state_path=state_path, cursor_path=cursor_path, journal_path=journal_path)

    journal = json.loads(open(journal_path).read())
    assert len(journal) == 2
    assert journal[0]["trade_id"] == "pos001"
    assert journal[0]["journal"] == "Clean pinbar entry."


def test_run_skips_already_journalled(tmp_path):
    state_path = str(tmp_path / "state.json")
    cursor_path = str(tmp_path / "cursor.json")
    journal_path = str(tmp_path / "trade_journal.json")
    open(state_path, "w").write(json.dumps(FRESH_STATE))
    open(cursor_path, "w").write(json.dumps({"journalled_ids": ["pos001", "pos002"]}))

    with patch("analyst.post_trade_journal.call_claude", return_value="Should not be called") as mock_call:
        run(state_path=state_path, cursor_path=cursor_path, journal_path=journal_path)
        mock_call.assert_not_called()


def test_run_stale_state_skips(tmp_path):
    state = {
        "meta": {"last_updated_utc": "2020-01-01T00:00:00Z"},
        "closed_trades_today": [TRADE_A],
    }
    state_path = str(tmp_path / "state.json")
    cursor_path = str(tmp_path / "cursor.json")
    journal_path = str(tmp_path / "trade_journal.json")
    open(state_path, "w").write(json.dumps(state))

    with patch("analyst.post_trade_journal.call_claude") as mock_call:
        run(state_path=state_path, cursor_path=cursor_path, journal_path=journal_path)
        mock_call.assert_not_called()

    assert not os.path.exists(journal_path)


def test_run_survives_bot_restart_cursor(tmp_path):
    """Cursor uses IDs not counts — survives bot restart (closed_trades_today reset)."""
    # First run: journal pos001
    state1 = {"meta": {"last_updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}, "closed_trades_today": [TRADE_A]}
    state_path = str(tmp_path / "state.json")
    cursor_path = str(tmp_path / "cursor.json")
    journal_path = str(tmp_path / "trade_journal.json")
    open(state_path, "w").write(json.dumps(state1))
    with patch("analyst.post_trade_journal.call_claude", return_value="entry 1"):
        run(state_path=state_path, cursor_path=cursor_path, journal_path=journal_path)

    # Bot restarts — closed_trades_today reset to empty, then pos002 closes
    state2 = {"meta": {"last_updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}, "closed_trades_today": [TRADE_B]}
    open(state_path, "w").write(json.dumps(state2))
    with patch("analyst.post_trade_journal.call_claude", return_value="entry 2"):
        run(state_path=state_path, cursor_path=cursor_path, journal_path=journal_path)

    journal = json.loads(open(journal_path).read())
    assert len(journal) == 2
    ids = [j["trade_id"] for j in journal]
    assert "pos001" in ids and "pos002" in ids
