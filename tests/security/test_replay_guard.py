from datetime import datetime, timezone

import pytest

from xauex.shared.replay_guard import CommandReplayGuard, ReplayLedgerError


def test_replay_guard_persists_ids(tmp_path):
    ledger = tmp_path / "manual_command_ids.json"
    guard = CommandReplayGuard(ledger, max_entries=3)
    guard.remember("cmd-1", now_utc=datetime(2026, 4, 7, tzinfo=timezone.utc))
    assert CommandReplayGuard(ledger).contains("cmd-1") is True
    assert CommandReplayGuard(ledger).contains("cmd-2") is False


def test_replay_guard_bounds_entries(tmp_path):
    guard = CommandReplayGuard(tmp_path / "ledger.json", max_entries=2)
    guard.remember("cmd-1")
    guard.remember("cmd-2")
    guard.remember("cmd-3")
    assert guard.contains("cmd-1") is False
    assert guard.contains("cmd-2") is True
    assert guard.contains("cmd-3") is True


def test_replay_guard_fails_closed_on_corrupt_ledger(tmp_path):
    ledger = tmp_path / "ledger.json"
    ledger.write_text("not-json", encoding="utf-8")
    with pytest.raises(ReplayLedgerError):
        CommandReplayGuard(ledger).contains("cmd-1")
