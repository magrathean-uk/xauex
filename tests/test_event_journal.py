from __future__ import annotations

import json
from datetime import datetime, timezone

from xauex.shared.event_journal import append_event, read_events


def test_event_journal_appends_stable_jsonl_envelopes(tmp_path):
    path = tmp_path / "events.jsonl"

    first = append_event(
        path,
        source="test",
        event_type="manual_command_queued",
        payload={"command_id": "cmd-1"},
        correlation_id="cmd-1",
        event_id="evt-1",
        timestamp_utc=datetime(2026, 4, 7, 1, 2, 3, tzinfo=timezone.utc),
    )
    second = append_event(
        path,
        source="test",
        event_type="manual_command_rejected",
        payload={"reason": "BAD_SIGNATURE"},
        correlation_id="cmd-1",
        event_id="evt-2",
        timestamp_utc=datetime(2026, 4, 7, 1, 2, 4, tzinfo=timezone.utc),
    )

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == first
    assert json.loads(lines[1]) == second
    assert first["schema_version"] == 1
    assert first["event_id"] == "evt-1"
    assert first["correlation_id"] == "cmd-1"
    assert first["timestamp_utc"] == "2026-04-07T01:02:03Z"


def test_event_journal_read_events_skips_malformed_lines(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "schema_version": 1,
                        "event_id": "evt-1",
                        "correlation_id": "cmd-1",
                        "timestamp_utc": "2026-04-07T01:02:03Z",
                        "source": "test",
                        "event_type": "manual_command_queued",
                        "payload": {},
                    }
                ),
                "{bad-json",
            ]
        ),
        encoding="utf-8",
    )

    assert [event["event_id"] for event in read_events(path)] == ["evt-1"]
