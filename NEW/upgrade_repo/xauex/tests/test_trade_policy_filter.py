from datetime import datetime, timezone

from bot.filters.trade_policy import TradePolicyLoader, TradePolicy


UTC = timezone.utc


def test_trade_policy_gate_direction_ignores_block_mode_for_execution():
    policy = TradePolicy(
        mode="BLOCK",
        direction="SHORT_ONLY",
        aggressiveness=0.0,
        allow_reentry=False,
        pullback_zone_multiplier=1.0,
        sl_buffer_multiplier=1.2,
        tp_rr_multiplier=1.0,
        generated_at_utc=datetime(2026, 3, 24, 10, 0, tzinfo=UTC),
        expires_utc=datetime(2026, 3, 24, 18, 0, tzinfo=UTC),
        block_new_entries_until_utc=datetime(2026, 3, 24, 14, 30, tzinfo=UTC),
        summary="Event risk.",
    )

    allowed, reason = TradePolicyLoader.gate_direction(
        policy,
        direction=-1,
        now_utc=datetime(2026, 3, 24, 11, 0, tzinfo=UTC),
    )

    assert allowed is True
    assert reason is None


def test_trade_policy_gate_direction_still_blocks_wrong_side():
    policy = TradePolicy(
        mode="CAUTIOUS",
        direction="SHORT_ONLY",
        aggressiveness=0.3,
        allow_reentry=False,
        pullback_zone_multiplier=1.0,
        sl_buffer_multiplier=1.2,
        tp_rr_multiplier=1.0,
        generated_at_utc=datetime(2026, 3, 24, 10, 0, tzinfo=UTC),
        expires_utc=datetime(2026, 3, 24, 18, 0, tzinfo=UTC),
        summary="Short bias.",
    )

    allowed, reason = TradePolicyLoader.gate_direction(
        policy,
        direction=1,
        now_utc=datetime(2026, 3, 24, 11, 0, tzinfo=UTC),
    )

    assert allowed is False
    assert reason == "POLICY_DIRECTION_BLOCK"
