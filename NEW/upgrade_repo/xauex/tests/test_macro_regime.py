from datetime import datetime, timedelta, timezone
import json

from bot.filters.macro_regime import MacroRegimeLoader


UTC = timezone.utc


def test_loader_returns_fresh_regime(tmp_path):
    path = tmp_path / "macro_regime.json"
    path.write_text(
        json.dumps(
            {
                "generated_at_utc": "2026-03-20T10:00:00Z",
                "regime": "XAU_BEARISH",
                "confidence": 0.8,
                "summary": "Hawkish Fed.",
                "expires_utc": "2026-03-20T16:00:00Z",
            }
        )
    )
    loader = MacroRegimeLoader(str(path), max_age_minutes=180)
    regime = loader.load(datetime(2026, 3, 20, 11, 0, tzinfo=UTC))
    assert regime is not None
    assert regime.bias == -1
    assert regime.confidence == 0.8


def test_loader_rejects_stale_regime(tmp_path):
    path = tmp_path / "macro_regime.json"
    path.write_text(
        json.dumps(
            {
                "generated_at_utc": "2026-03-20T06:00:00Z",
                "regime": "XAU_BULLISH",
                "confidence": 0.8,
                "summary": "Old context.",
            }
        )
    )
    loader = MacroRegimeLoader(str(path), max_age_minutes=60)
    assert loader.load(datetime(2026, 3, 20, 8, 0, tzinfo=UTC)) is None


def test_gate_direction_blocks_opposite_high_confidence(tmp_path):
    path = tmp_path / "macro_regime.json"
    path.write_text(
        json.dumps(
            {
                "generated_at_utc": "2026-03-20T10:00:00Z",
                "regime": "XAU_BEARISH",
                "confidence": 0.9,
                "summary": "Bearish gold.",
                "expires_utc": "2026-03-20T16:00:00Z",
            }
        )
    )
    loader = MacroRegimeLoader(str(path), max_age_minutes=180)
    regime = loader.load(datetime(2026, 3, 20, 11, 0, tzinfo=UTC))
    allowed, reason = loader.gate_direction(
        regime,
        direction=1,
        now_utc=datetime(2026, 3, 20, 11, 0, tzinfo=UTC),
        confidence_threshold=0.65,
    )
    assert allowed is False
    assert reason == "MACRO_DIRECTION_BLOCK"


def test_gate_direction_blocks_during_event_window(tmp_path):
    path = tmp_path / "macro_regime.json"
    path.write_text(
        json.dumps(
            {
                "generated_at_utc": "2026-03-20T10:00:00Z",
                "regime": "NEUTRAL",
                "confidence": 0.4,
                "summary": "Event risk.",
                "block_new_entries_until_utc": "2026-03-20T11:30:00Z",
                "expires_utc": "2026-03-20T16:00:00Z",
            }
        )
    )
    loader = MacroRegimeLoader(str(path), max_age_minutes=180)
    regime = loader.load(datetime(2026, 3, 20, 11, 0, tzinfo=UTC))
    allowed, reason = loader.gate_direction(
        regime,
        direction=-1,
        now_utc=datetime(2026, 3, 20, 11, 0, tzinfo=UTC),
        confidence_threshold=0.65,
    )
    assert allowed is False
    assert reason == "MACRO_EVENT_BLOCK"
