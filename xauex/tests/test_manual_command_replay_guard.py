import json
from datetime import datetime, timezone

from xauex.shared.manual_commands import consume_manual_command_file, create_signed_manual_command
from xauex.shared.replay_guard import CommandReplayGuard


def test_manual_command_replay_guard_rejects_duplicate_across_instances(tmp_path):
    cmd_path = tmp_path / "manual_trade_cmd.json"
    ledger_path = tmp_path / "manual_command_ids.json"

    envelope = create_signed_manual_command(
        {"command": "close", "position_id": "manual-1"},
        secret="manual-secret",
        command_id="replay-1",
        now_utc=datetime(2026, 4, 7, 1, 2, tzinfo=timezone.utc),
    )
    cmd_path.write_text(json.dumps(envelope), encoding="utf-8")

    first = consume_manual_command_file(
        cmd_path,
        secret="manual-secret",
        replay_guard=CommandReplayGuard(ledger_path),
        seen_command_ids=set(),
        now_utc=datetime(2026, 4, 7, 1, 2, 5, tzinfo=timezone.utc),
    )
    assert first.payload == {"command": "close", "position_id": "manual-1"}

    cmd_path.write_text(json.dumps(envelope), encoding="utf-8")
    second = consume_manual_command_file(
        cmd_path,
        secret="manual-secret",
        replay_guard=CommandReplayGuard(ledger_path),
        seen_command_ids=set(),
        now_utc=datetime(2026, 4, 7, 1, 2, 10, tzinfo=timezone.utc),
    )
    assert second.payload is None
    assert second.rejection_reason == "DUPLICATE_COMMAND"


def test_manual_command_replay_guard_fails_closed_when_ledger_corrupt(tmp_path):
    cmd_path = tmp_path / "manual_trade_cmd.json"
    ledger_path = tmp_path / "manual_command_ids.json"
    ledger_path.write_text("not-json", encoding="utf-8")

    envelope = create_signed_manual_command(
        {"command": "close", "position_id": "manual-1"},
        secret="manual-secret",
        command_id="replay-corrupt",
        now_utc=datetime(2026, 4, 7, 1, 2, tzinfo=timezone.utc),
    )
    cmd_path.write_text(json.dumps(envelope), encoding="utf-8")

    result = consume_manual_command_file(
        cmd_path,
        secret="manual-secret",
        replay_guard=CommandReplayGuard(ledger_path),
        seen_command_ids=set(),
        now_utc=datetime(2026, 4, 7, 1, 2, 5, tzinfo=timezone.utc),
    )
    assert result.payload is None
    assert result.rejection_reason == "REPLAY_LEDGER_ERROR"
