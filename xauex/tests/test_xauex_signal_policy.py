# ruff: noqa: E402

import sys
import importlib.util
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

XAUEX_ROOT = REPO_ROOT / "xauex"
if str(XAUEX_ROOT) not in sys.path:
    sys.path.insert(0, str(XAUEX_ROOT))

_SPEC = importlib.util.spec_from_file_location("xauex_main", XAUEX_ROOT / "main.py")
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

from xauex.bot.api.models import SymbolSpec
from xauex.signal.assets import resolve_asset
from xauex.signal.signal_parser import _fallback_direction, _normalize_signal

BotOrchestrator = _MODULE.BotOrchestrator
XauexAssuranceProfile = _MODULE.XauexAssuranceProfile
calculate_xauex_assurance_cash_risk = _MODULE.calculate_xauex_assurance_cash_risk
build_xauex_assurance_profile = _MODULE.build_xauex_assurance_profile


def _assurance_config(**overrides):
    base = {
        "xauex_session_protect_r": 0.85,
        "xauex_session_high_confidence_protect_r": 1.0,
        "xauex_session_trail_r": 1.35,
        "xauex_session_protect_lock_r": 0.30,
        "xauex_session_high_confidence_protect_lock_r": 0.25,
        "xauex_session_low_confidence_protect_r": 0.95,
        "xauex_session_low_confidence_protect_lock_r": 0.20,
        "xauex_low_confidence_lot_multiplier": 0.25,
        "xauex_stale_context_min_confidence": 0.70,
        "xauex_same_direction_loss_cooldown": True,
        "xauex_counter_signal_daily_limit": 1,
        "xauex_exceptional_reentry_min_confidence": 0.85,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _signal(**overrides):
    base = {
        "action": "SELL",
        "confidence": 0.80,
        "consensus_state": "aligned",
        "validator_status": "reviewed",
        "validator_summary": "well-supported setup",
        "confirm_status": "CONFIRMED",
        "decision_packet": {
            "input_freshness": {"market_snapshot_state": "fresh"},
            "price_features": {"range_position": "MIDDLE_THIRD"},
        },
    }
    base.update(overrides)
    return base


def test_parser_fallback_converts_soft_hold_to_directional_trade():
    asset = resolve_asset("XAUUSD")
    fallback = _fallback_direction(
        actions=[
            {"agent_name": "Macro", "action_type": "POST", "content": "Bullish gold outlook on weaker dollar and lower yields."},
            {"agent_name": "Flow", "action_type": "POST", "content": "ETF inflow and safe haven demand support upside."},
        ],
        report_markdown="Gold looks bullish into London open with weaker dollar, lower yields, and strong safe haven demand.",
        reasoning="Signals are mixed but slightly constructive.",
    )
    signal = _normalize_signal(
        asset,
        {
            "action": "HOLD",
            "confidence": 0.42,
            "reasoning": "Signals are mixed but slightly constructive.",
            "stop_loss_distance": 12.0,
            "take_profit_distance": 24.0,
        },
        fallback=fallback,
    )
    assert signal["action"] == "BUY"
    assert signal["confidence"] >= 0.51
    assert signal["stop_loss_distance"] == 12.0
    assert signal["take_profit_distance"] == 24.0


def test_parser_keeps_hard_blocker_hold():
    asset = resolve_asset("XAUUSD")
    fallback = _fallback_direction(
        actions=[],
        report_markdown="Bullish backdrop but the market is closed.",
        reasoning="Market closed.",
    )
    signal = _normalize_signal(
        asset,
        {
            "action": "HOLD",
            "confidence": 0.4,
            "reasoning": "Market closed for trading.",
            "stop_loss_distance": 12.0,
            "take_profit_distance": 24.0,
        },
        fallback=fallback,
    )
    assert signal["action"] == "HOLD"
    assert signal["stop_loss_distance"] == 0.0
    assert signal["take_profit_distance"] == 0.0


def test_parser_fallback_rejects_weak_evidence_hold():
    """Weak or empty keyword evidence must not override HOLD.

    Prior behavior used a permanent long-tie bias (score>=0 -> BUY) and a 0.51
    confidence floor, so a HOLD from the LLM turned into a half-size BUY even
    without real evidence. Now the fallback must stay HOLD when evidence is
    thin.
    """
    asset = resolve_asset("XAUUSD")

    # Empty inputs: the old code returned BUY at ~0.54; now must be HOLD.
    empty_fallback = _fallback_direction(actions=[], report_markdown="", reasoning=None)
    assert empty_fallback["action"] == "HOLD"
    assert empty_fallback["confidence"] == 0.0

    # A single bullish mention is not enough evidence to take risk.
    thin_fallback = _fallback_direction(
        actions=[],
        report_markdown="Gold could drift a bit higher.",
        reasoning=None,
    )
    assert thin_fallback["action"] == "HOLD"

    # When fallback is HOLD the normalizer must preserve the HOLD decision.
    signal = _normalize_signal(
        asset,
        {
            "action": "HOLD",
            "confidence": 0.30,
            "reasoning": "Mixed signals; staying sidelined.",
            "stop_loss_distance": 12.0,
            "take_profit_distance": 24.0,
        },
        fallback=empty_fallback,
    )
    assert signal["action"] == "HOLD"
    assert signal["stop_loss_distance"] == 0.0
    assert signal["take_profit_distance"] == 0.0


def test_parser_fallback_breaks_score_tie_into_hold():
    """A score tie (equal bullish and bearish hits) must not default to BUY."""
    balanced_fallback = _fallback_direction(
        actions=[
            {"agent_name": "A", "action_type": "POST", "content": "bullish upside higher dovish inflow"},
            {"agent_name": "B", "action_type": "POST", "content": "bearish downside hawkish outflow selloff"},
        ],
        report_markdown="",
        reasoning=None,
    )
    assert balanced_fallback["action"] == "HOLD"
    assert balanced_fallback["confidence"] == 0.0


def test_loss_cooldown_multiplier_scales_with_recent_losses():
    """After 2 losses the next trade must be half-size; 3+ would be 0.3x
    (though the risk gate halts at 3 by default). Zero/one loss = full size."""
    dummy = SimpleNamespace()
    method = BotOrchestrator._xauex_loss_cooldown_multiplier

    dummy.risk_gates = None
    assert method(dummy) == 1.0

    dummy.risk_gates = SimpleNamespace(state=SimpleNamespace(consecutive_losses_today=0))
    assert method(dummy) == 1.0

    dummy.risk_gates = SimpleNamespace(state=SimpleNamespace(consecutive_losses_today=1))
    assert method(dummy) == 1.0

    dummy.risk_gates = SimpleNamespace(state=SimpleNamespace(consecutive_losses_today=2))
    assert method(dummy) == 0.5

    dummy.risk_gates = SimpleNamespace(state=SimpleNamespace(consecutive_losses_today=3))
    assert method(dummy) == 0.3


def test_confidence_scales_lot_size_without_skipping_trade():
    dummy = SimpleNamespace(
        config=SimpleNamespace(
            xauex_confidence_full_threshold=0.65,
            xauex_confidence_medium_threshold=0.55,
            xauex_medium_confidence_lot_multiplier=0.5,
            xauex_low_confidence_lot_multiplier=0.25,
            max_lot_size=1.0,
        ),
        symbol_spec=SymbolSpec(
            symbol="XAUUSD",
            lot_size=100.0,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            digits=2,
            pip_value=0.01,
        ),
    )
    dummy._xauex_lot_multiplier = lambda confidence: BotOrchestrator._xauex_lot_multiplier(dummy, confidence)

    medium = BotOrchestrator._scale_lot_to_confidence(dummy, 0.12, 0.58)
    low = BotOrchestrator._scale_lot_to_confidence(dummy, 0.12, 0.40)
    full = BotOrchestrator._scale_lot_to_confidence(dummy, 0.12, 0.72)
    floored = BotOrchestrator._scale_lot_to_confidence(dummy, 0.03, 0.40)

    assert medium == 0.06
    assert low == 0.03
    assert full == 0.12
    assert floored == 0.01


def test_assurance_cash_risk_multiplies_all_reducers_without_execution_exception():
    cash_risk = calculate_xauex_assurance_cash_risk(
        cash_risk_budget=29.41,
        assurance_risk_multiplier=1.0,
        cooldown_multiplier=1.0,
        session_slot_multiplier=0.75,
        counter_signal_risk_multiplier=1.0,
        microstructure_risk_multiplier=0.5,
    )

    assert cash_risk == 11.03


def test_assurance_cash_risk_lifts_approved_trade_to_broker_minimum_when_budget_allows():
    cash_risk = calculate_xauex_assurance_cash_risk(
        cash_risk_budget=29.41,
        assurance_risk_multiplier=1.0,
        cooldown_multiplier=1.0,
        session_slot_multiplier=0.85,
        counter_signal_risk_multiplier=1.0,
        microstructure_risk_multiplier=0.5,
        minimum_executable_risk=25.0,
    )

    assert cash_risk == 25.0


def test_assurance_cash_risk_does_not_lift_low_assurance_trade_to_broker_minimum():
    cash_risk = calculate_xauex_assurance_cash_risk(
        cash_risk_budget=29.41,
        assurance_risk_multiplier=0.25,
        cooldown_multiplier=1.0,
        session_slot_multiplier=1.0,
        counter_signal_risk_multiplier=1.0,
        microstructure_risk_multiplier=1.0,
        minimum_executable_risk=25.0,
        allow_minimum_executable_risk_lift=False,
    )

    assert cash_risk == 7.35


def test_assurance_cash_risk_does_not_lift_to_minimum_when_budget_cannot_cover_it():
    cash_risk = calculate_xauex_assurance_cash_risk(
        cash_risk_budget=20.0,
        assurance_risk_multiplier=1.0,
        cooldown_multiplier=1.0,
        session_slot_multiplier=0.85,
        counter_signal_risk_multiplier=1.0,
        microstructure_risk_multiplier=0.5,
        minimum_executable_risk=25.0,
    )

    assert cash_risk == 8.5


def test_assurance_reduces_medium_confidence_with_stale_market_snapshot():
    signal = _signal(
        confidence=0.55,
        decision_packet={
            "input_freshness": {"market_snapshot_state": "warning"},
            "price_features": {"range_position": "MIDDLE_THIRD"},
        },
    )

    assurance = build_xauex_assurance_profile(signal, _assurance_config())

    assert assurance.allow_trade is True
    assert assurance.bucket == "medium"
    assert assurance.reason == "MEDIUM_ASSURANCE_STALE_CONTEXT"
    assert assurance.risk_multiplier == 0.5


def test_assurance_reduces_stale_counter_signal_to_medium_risk():
    signal = _signal(
        confidence=0.58,
        counter_signal=True,
        decision_packet={
            "input_freshness": {"market_snapshot_state": "warning"},
            "price_features": {"range_position": "MIDDLE_THIRD"},
        },
    )

    assurance = build_xauex_assurance_profile(signal, _assurance_config())

    assert assurance.allow_trade is True
    assert assurance.bucket == "medium"
    assert assurance.reason == "MEDIUM_ASSURANCE_STALE_CONTEXT"
    assert assurance.risk_multiplier == 0.5


def test_assurance_caps_high_confidence_stale_snapshot_to_reduced_medium_risk():
    signal = _signal(
        confidence=0.82,
        decision_packet={
            "input_freshness": {"market_snapshot_state": "stale"},
            "price_features": {"range_position": "MIDDLE_THIRD"},
        },
    )

    assurance = build_xauex_assurance_profile(signal, _assurance_config())

    assert assurance.allow_trade is True
    assert assurance.bucket == "medium"
    assert assurance.reason == "MEDIUM_ASSURANCE_STALE_CONTEXT"
    assert assurance.risk_multiplier == 0.5


def test_assurance_trusts_structured_freshness_over_stale_reference_wording():
    signal = _signal(
        confidence=0.65,
        validator_summary=(
            "well-supported setup; a stale reference series follows its normal weekly publication cadence"
        ),
        decision_packet={
            "input_freshness": {
                "market_snapshot_state": "fresh",
                "daily_publishing_max_business_age_days": 2,
                "missing_series_count": 0,
                "stale_block_series_count": 0,
                "cache_fallback_series_count": 0,
                "fed_h15_fallback_series_count": 0,
            },
            "price_features": {"range_position": "MIDDLE_THIRD"},
        },
    )

    assurance = build_xauex_assurance_profile(signal, _assurance_config())

    assert assurance.allow_trade is True
    assert assurance.bucket == "high"
    assert assurance.reason == "HIGH_ASSURANCE"
    assert assurance.risk_multiplier == 1.5


def test_assurance_hard_block_explains_direct_validator_contradiction():
    signal = _signal(
        confidence=0.47,
        consensus_state="disagreed",
        validator_summary="The proposed SELL directly contradicts the price regime.",
    )

    assurance = build_xauex_assurance_profile(signal, _assurance_config())

    assert assurance.allow_trade is False
    assert assurance.reason == "HARD_BLOCKER"
    assert assurance.score == 0.21
    assert assurance.block_factors == (
        "ASSURANCE_LOW_CONFIDENCE",
        "ASSURANCE_CONSENSUS_DISAGREEMENT",
        "ASSURANCE_VALIDATOR_CONTRADICTION",
    )


def test_assurance_hard_block_explains_combined_low_score():
    signal = _signal(
        confidence=0.58,
        consensus_state="disagreed",
        validator_summary="The proposed SELL directly contradicts the price regime.",
        decision_packet={
            "input_freshness": {"market_snapshot_state": "warning"},
            "price_features": {"range_position": "MIDDLE_THIRD"},
        },
    )

    assurance = build_xauex_assurance_profile(signal, _assurance_config())

    assert assurance.allow_trade is False
    assert assurance.reason == "HARD_BLOCKER"
    assert assurance.score == 0.27
    assert assurance.block_factors == (
        "ASSURANCE_SCORE_BELOW_MINIMUM",
        "ASSURANCE_CONSENSUS_DISAGREEMENT",
        "ASSURANCE_VALIDATOR_CONTRADICTION",
        "ASSURANCE_STALE_CONTEXT",
    )


def test_counter_signal_canary_can_force_minimum_risk_without_regular_lift():
    cash_risk = calculate_xauex_assurance_cash_risk(
        cash_risk_budget=29.41,
        assurance_risk_multiplier=1.0,
        cooldown_multiplier=1.0,
        session_slot_multiplier=0.85,
        counter_signal_risk_multiplier=0.5,
        microstructure_risk_multiplier=1.0,
        minimum_executable_risk=25.0,
        allow_minimum_executable_risk_lift=False,
        force_minimum_executable_risk=True,
    )

    assert cash_risk == 25.0


def test_entry_policy_blocks_second_counter_signal_same_london_day():
    decision = _MODULE.build_xauex_entry_quality_decision(
        signal=_signal(counter_signal=True),
        assurance=XauexAssuranceProfile(
            bucket="medium",
            score=0.58,
            allow_trade=True,
            reason="MEDIUM_ASSURANCE",
            risk_multiplier=1.0,
            target_rr=2.0,
            protect_r=0.85,
            trail_r=1.35,
            protect_lock_r=0.30,
        ),
        config=_assurance_config(),
        now_utc=None,
        closed_trades_today=[],
        signal_runs_london=[
            {"reason": "ORDER_PLACED", "signal_action": "SELL", "counter_signal": True},
        ],
    )

    assert decision["allowed"] is True
    assert decision["reason"] == "ENTRY_QUALITY_WARNINGS"
    assert decision["risk_multiplier"] < 1.0
    assert "COUNTER_SIGNAL_DAILY_LIMIT" in decision["policy_factors"]


def test_entry_policy_reduces_same_direction_after_loss():
    decision = _MODULE.build_xauex_entry_quality_decision(
        signal=_signal(action="SELL", confidence=0.84),
        assurance=XauexAssuranceProfile(
            bucket="high",
            score=0.90,
            allow_trade=True,
            reason="HIGH_ASSURANCE",
            risk_multiplier=1.5,
            target_rr=2.5,
            protect_r=1.0,
            trail_r=1.5,
            protect_lock_r=0.25,
        ),
        config=_assurance_config(),
        now_utc=None,
        closed_trades_today=[
            {
                "position_id": "loss-1",
                "direction": "SHORT",
                "pnl": -19.41,
                "close_time_utc": "2026-06-19T08:23:17Z",
            }
        ],
        signal_runs_london=[],
    )

    assert decision["allowed"] is True
    assert decision["reason"] == "ENTRY_QUALITY_WARNINGS"
    assert decision["risk_multiplier"] < 1.0
    assert "SAME_DIRECTION_LOSS_COOLDOWN" in decision["policy_factors"]


def test_entry_policy_reduces_lower_third_same_direction_chase_after_prior_entry():
    signal = _signal(
        action="SELL",
        confidence=0.86,
        decision_packet={
            "input_freshness": {"market_snapshot_state": "fresh"},
            "price_features": {"range_position": "LOWER_THIRD"},
        },
    )

    decision = _MODULE.build_xauex_entry_quality_decision(
        signal=signal,
        assurance=XauexAssuranceProfile(
            bucket="high",
            score=0.94,
            allow_trade=True,
            reason="HIGH_ASSURANCE",
            risk_multiplier=1.5,
            target_rr=2.5,
            protect_r=1.0,
            trail_r=1.5,
            protect_lock_r=0.25,
        ),
        config=_assurance_config(),
        now_utc=None,
        closed_trades_today=[],
        signal_runs_london=[
            {"reason": "ORDER_PLACED", "signal_action": "SELL", "counter_signal": False},
        ],
    )

    assert decision["allowed"] is True
    assert decision["reason"] == "ENTRY_QUALITY_WARNINGS"
    assert decision["risk_multiplier"] < 1.0
    assert "LOWER_THIRD_NO_CHASE" in decision["policy_factors"]


def test_entry_policy_uses_single_hard_blocker_for_combined_soft_risks():
    signal = _signal(
        action="SELL",
        confidence=0.62,
        decision_packet={
            "input_freshness": {"market_snapshot_state": "warning"},
            "price_features": {"range_position": "LOWER_THIRD"},
        },
    )

    decision = _MODULE.build_xauex_entry_quality_decision(
        signal=signal,
        assurance=XauexAssuranceProfile(
            bucket="medium",
            score=0.57,
            allow_trade=True,
            reason="MEDIUM_ASSURANCE_STALE_CONTEXT",
            risk_multiplier=0.5,
            target_rr=1.5,
            protect_r=0.85,
            trail_r=1.35,
            protect_lock_r=0.30,
        ),
        config=_assurance_config(),
        now_utc=None,
        closed_trades_today=[
            {
                "position_id": "loss-1",
                "direction": "SHORT",
                "pnl": -19.41,
                "close_time_utc": "2026-06-19T08:23:17Z",
            }
        ],
        signal_runs_london=[
            {"reason": "ORDER_PLACED", "signal_action": "SELL", "counter_signal": False},
        ],
    )

    assert decision["allowed"] is False
    assert decision["reason"] == "HARD_BLOCKER"
    assert decision["policy_factors"] == [
        "STALE_CONTEXT_LOW_CONFIDENCE",
        "SAME_DIRECTION_LOSS_COOLDOWN",
        "LOWER_THIRD_NO_CHASE",
    ]


def test_entry_policy_missing_pattern_reduces_risk_without_blocking_alone():
    decision = _MODULE.build_xauex_entry_quality_decision(
        signal=_signal(),
        assurance=XauexAssuranceProfile(
            bucket="high",
            score=0.90,
            allow_trade=True,
            reason="HIGH_ASSURANCE",
            risk_multiplier=1.5,
            target_rr=2.5,
            protect_r=1.0,
            trail_r=1.5,
            protect_lock_r=0.25,
        ),
        config=_assurance_config(),
        now_utc=None,
        closed_trades_today=[],
        signal_runs_london=[],
        pattern_evidence={"factor": "PATTERN_MISSING", "weight": 2},
    )

    assert decision["allowed"] is True
    assert decision["reason"] == "ENTRY_QUALITY_WARNINGS"
    assert decision["risk_multiplier"] == 0.25
    assert decision["policy_factors"] == ["PATTERN_MISSING"]


def test_entry_policy_combines_missing_pattern_with_stale_context():
    decision = _MODULE.build_xauex_entry_quality_decision(
        signal=_signal(
            confidence=0.62,
            decision_packet={
                "input_freshness": {"market_snapshot_state": "warning"},
                "price_features": {"range_position": "MIDDLE_THIRD"},
            },
        ),
        assurance=XauexAssuranceProfile(
            bucket="medium",
            score=0.58,
            allow_trade=True,
            reason="MEDIUM_ASSURANCE_STALE_CONTEXT",
            risk_multiplier=0.5,
            target_rr=1.5,
            protect_r=0.85,
            trail_r=1.35,
            protect_lock_r=0.30,
        ),
        config=_assurance_config(),
        now_utc=None,
        closed_trades_today=[],
        signal_runs_london=[],
        pattern_evidence={"factor": "PATTERN_MISSING", "weight": 2},
    )

    assert decision["allowed"] is False
    assert decision["reason"] == "HARD_BLOCKER"
    assert decision["policy_factors"] == ["PATTERN_MISSING", "STALE_CONTEXT_LOW_CONFIDENCE"]


def test_entry_policy_blocks_directly_opposed_pattern():
    decision = _MODULE.build_xauex_entry_quality_decision(
        signal=_signal(),
        assurance=XauexAssuranceProfile(
            bucket="high",
            score=0.90,
            allow_trade=True,
            reason="HIGH_ASSURANCE",
            risk_multiplier=1.5,
            target_rr=2.5,
            protect_r=1.0,
            trail_r=1.5,
            protect_lock_r=0.25,
        ),
        config=_assurance_config(),
        now_utc=None,
        closed_trades_today=[],
        signal_runs_london=[],
        pattern_evidence={"factor": "PATTERN_DIRECTION_MISMATCH", "weight": 3},
    )

    assert decision["allowed"] is False
    assert decision["reason"] == "HARD_BLOCKER"
    assert decision["policy_factors"] == ["PATTERN_DIRECTION_MISMATCH"]
