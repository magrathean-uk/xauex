from datetime import datetime, timezone

from bot.filters.macro_regime import MacroRegimeLoader, MacroRegime


UTC = timezone.utc


def test_macro_regime_gate_direction_ignores_event_block_for_execution():
    regime = MacroRegime(
        regime="XAU_BEARISH",
        confidence=0.8,
        summary="PMI risk but bearish.",
        generated_at_utc=datetime(2026, 3, 24, 10, 0, tzinfo=UTC),
        expires_utc=datetime(2026, 3, 24, 18, 0, tzinfo=UTC),
        block_new_entries_until_utc=datetime(2026, 3, 24, 14, 30, tzinfo=UTC),
    )

    allowed, reason = MacroRegimeLoader.gate_direction(
        regime,
        direction=-1,
        now_utc=datetime(2026, 3, 24, 12, 0, tzinfo=UTC),
        confidence_threshold=0.5,
    )

    assert allowed is True
    assert reason is None


def test_macro_regime_gate_direction_blocks_wrong_bias():
    regime = MacroRegime(
        regime="XAU_BEARISH",
        confidence=0.8,
        summary="Bearish.",
        generated_at_utc=datetime(2026, 3, 24, 10, 0, tzinfo=UTC),
        expires_utc=datetime(2026, 3, 24, 18, 0, tzinfo=UTC),
    )

    allowed, reason = MacroRegimeLoader.gate_direction(
        regime,
        direction=1,
        now_utc=datetime(2026, 3, 24, 12, 0, tzinfo=UTC),
        confidence_threshold=0.5,
    )

    assert allowed is False
    assert reason == "MACRO_DIRECTION_BLOCK"
