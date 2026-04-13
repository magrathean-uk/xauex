from datetime import datetime, timezone

from xauex.signal.config import SignalConfig
from xauex.signal.fedwatch import _normalize_fedwatch_payload, fetch_fedwatch_snapshot


def test_fetch_fedwatch_snapshot_returns_unconfigured_when_api_url_missing(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()

    snapshot = fetch_fedwatch_snapshot(config=cfg)

    assert snapshot["status"] == "unconfigured"
    assert snapshot["available"] is False
    assert "not configured" in snapshot["summary"].lower()


def test_normalize_fedwatch_payload_parses_next_meeting_probabilities():
    payload = {
        "meetings": [
            {
                "meetingDate": "2026-05-06",
                "currentTargetRate": {"lower": 4.25, "upper": 4.50},
                "distribution": [
                    {"label": "cut", "change_bps": -25, "probability": 0.62},
                    {"label": "hold", "change_bps": 0, "probability": 0.38},
                ],
            }
        ]
    }

    snapshot = _normalize_fedwatch_payload(payload, fetched_at=datetime(2026, 4, 14, tzinfo=timezone.utc))

    assert snapshot["status"] == "available"
    assert snapshot["available"] is True
    assert snapshot["meeting_date"] == "2026-05-06"
    assert snapshot["cut_probability"] == 0.62
    assert snapshot["hold_probability"] == 0.38
    assert snapshot["bias"] == "BUY"
    assert snapshot["expected_change_bps"] == -15.5
