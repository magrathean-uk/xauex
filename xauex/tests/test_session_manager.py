# ruff: noqa: E402

import sys
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import pytest


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

from config import load_config
from config import ConfigError
from bot.risk.sizing import calculate_xauex_lot_size_from_cash_risk
from bot.levels.htf_levels import HTFLevels

build_xauex_initial_stop_distance = _MODULE.build_xauex_initial_stop_distance
build_xauex_protect_stop_price = _MODULE.build_xauex_protect_stop_price
build_xauex_assurance_profile = _MODULE.build_xauex_assurance_profile
build_xauex_take_profit_distance = _MODULE.build_xauex_take_profit_distance
build_xauex_counter_signal_candidate = _MODULE.build_xauex_counter_signal_candidate
advance_xauex_session_phase = _MODULE.advance_xauex_session_phase
confirm_xauex_session_phase_transition = _MODULE.confirm_xauex_session_phase_transition
build_xauex_confirm_decision = _MODULE.build_xauex_confirm_decision
is_xauex_confirm_timestamp_fresh = _MODULE.is_xauex_confirm_timestamp_fresh
XAUEX_CONFIRM_MAX_AGE_SECONDS_DEFAULT = _MODULE.XAUEX_CONFIRM_MAX_AGE_SECONDS_DEFAULT
calculate_xauex_remaining_daily_loss_budget = _MODULE.calculate_xauex_remaining_daily_loss_budget
BotOrchestrator = _MODULE.BotOrchestrator


def _set_required_config_env(monkeypatch):
    monkeypatch.setenv("CTRADER_CLIENT_ID", "test-client")
    monkeypatch.setenv("CTRADER_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("CTRADER_ACCOUNT_ID", "123456")


def test_load_config_includes_xauex_session_manager_settings(monkeypatch):
    monkeypatch.chdir(XAUEX_ROOT)
    _set_required_config_env(monkeypatch)
    monkeypatch.setenv("XAUEX_SESSION_PROTECT_R", "0.85")
    monkeypatch.setenv("XAUEX_SESSION_TRAIL_R", "1.35")
    monkeypatch.setenv("XAUEX_SESSION_ATR_MULTIPLIER", "1.4")
    monkeypatch.setenv("XAUEX_SESSION_STRUCTURE_BUFFER_USD", "2.5")
    monkeypatch.setenv("XAUEX_SESSION_PROTECT_LOCK_R", "0.30")
    monkeypatch.setenv("XAUEX_SESSION_LOW_CONFIDENCE_PROTECT_LOCK_R", "0.35")
    monkeypatch.setenv("XAUEX_SESSION_HIGH_CONFIDENCE_PROTECT_LOCK_R", "0.25")
    monkeypatch.setenv("XAUEX_MANUAL_COMMAND_PATH", "/tmp/manual_trade_cmd.json")
    monkeypatch.setenv("XAUEX_COUNTER_SIGNAL_ENABLED", "false")
    monkeypatch.setenv("XAUEX_COUNTER_SIGNAL_CONFIDENCE", "0.58")
    monkeypatch.setenv("XAUEX_COUNTER_SIGNAL_RISK_MULTIPLIER", "0.5")

    cfg = load_config()

    assert cfg.xauex_session_protect_r == 0.85
    assert cfg.xauex_session_trail_r == 1.35
    assert cfg.xauex_session_atr_multiplier == 1.4
    assert cfg.xauex_session_structure_buffer_usd == 2.5
    assert cfg.xauex_session_protect_lock_r == 0.30
    assert cfg.xauex_session_low_confidence_protect_lock_r == 0.35
    assert cfg.xauex_session_high_confidence_protect_lock_r == 0.25
    assert cfg.xauex_manual_command_path == "/tmp/manual_trade_cmd.json"
    assert cfg.xauex_manual_command_secret == ""
    assert cfg.xauex_manual_command_ledger_path == "/var/lib/xauex/manual_command_ids.json"
    assert cfg.xauex_event_journal_path == "/var/lib/xauex/events.jsonl"
    assert cfg.xauex_counter_signal_enabled is False
    assert cfg.xauex_counter_signal_confidence == 0.58
    assert cfg.xauex_counter_signal_risk_multiplier == 0.5


def test_load_config_defaults_health_check_host_to_loopback(monkeypatch):
    monkeypatch.chdir(XAUEX_ROOT)
    _set_required_config_env(monkeypatch)
    monkeypatch.delenv("HEALTH_CHECK_HOST", raising=False)

    cfg = load_config()

    assert cfg.health_check_host == "127.0.0.1"


def test_load_config_rejects_force_flat_before_second_window_finishes(monkeypatch):
    monkeypatch.chdir(XAUEX_ROOT)
    _set_required_config_env(monkeypatch)
    monkeypatch.setenv("XAUEX_ENTRY_SECOND_START_LONDON", "11:30")
    monkeypatch.setenv("XAUEX_ENTRY_SECOND_END_LONDON", "11:35")
    monkeypatch.setenv("XAUEX_FORCE_FLAT_LONDON", "11:30")

    with pytest.raises(ConfigError, match="XAUEX_FORCE_FLAT_LONDON must be later than XAUEX_ENTRY_SECOND_END_LONDON"):
        load_config()


def test_oracle_initial_stop_uses_widest_signal_structure_or_atr():
    stop = build_xauex_initial_stop_distance(
        signal_stop=12.0,
        atr_stop=16.5,
        structure_stop=14.0,
        min_stop=8.0,
        max_stop=30.0,
    )
    assert stop == 16.5


def test_oracle_wider_stop_reduces_lot_but_respects_cash_cap():
    lot = calculate_xauex_lot_size_from_cash_risk(
        cash_risk=50.0,
        stop_distance=20.0,
        lot_size=100.0,
        volume_step=0.01,
        volume_min=0.01,
        volume_max=100.0,
        max_lot_size=0.1,
    )
    assert lot == 0.02


def test_session_phase_moves_to_protect_then_trail():
    state = {
        "phase": "OBSERVE",
        "direction": "LONG",
        "entry_price": 100.0,
        "initial_risk_distance": 10.0,
        "confidence_bucket": "medium",
    }

    protect = advance_xauex_session_phase(
        state,
        current_price=109.0,
        protect_r=0.85,
        trail_r=1.35,
    )
    trail = advance_xauex_session_phase(
        protect,
        current_price=114.0,
        protect_r=0.85,
        trail_r=1.35,
    )

    assert protect["phase"] == "PROTECT"
    assert trail["phase"] == "TRAIL"


def test_session_phase_promotion_requires_matching_unrealised_pnl():
    state = {
        "phase": "OBSERVE",
        "direction": "SHORT",
        "entry_price": 4737.03,
        "initial_risk_distance": 25.0,
        "confidence_bucket": "high",
        "protect_r": 1.0,
        "trail_r": 1.5,
    }

    candidate = advance_xauex_session_phase(
        state,
        current_price=4710.0,
        protect_r=1.0,
        trail_r=1.5,
    )
    confirmed = confirm_xauex_session_phase_transition(
        previous_state=state,
        candidate_state=candidate,
        unrealised_pnl=0.61,
        lot_size=0.01,
        contract_size=100.0,
    )

    assert candidate["phase"] == "PROTECT"
    assert confirmed["phase"] == "OBSERVE"


def test_session_phase_promotion_allows_confirmed_profit_threshold():
    state = {
        "phase": "OBSERVE",
        "direction": "SHORT",
        "entry_price": 4737.03,
        "initial_risk_distance": 25.0,
        "confidence_bucket": "high",
        "protect_r": 1.0,
        "trail_r": 1.5,
    }

    candidate = advance_xauex_session_phase(
        state,
        current_price=4699.0,
        protect_r=1.0,
        trail_r=1.5,
    )
    confirmed = confirm_xauex_session_phase_transition(
        previous_state=state,
        candidate_state=candidate,
        unrealised_pnl=40.0,
        lot_size=0.01,
        contract_size=100.0,
    )

    assert candidate["phase"] == "TRAIL"
    assert confirmed["phase"] == "TRAIL"


def test_assurance_profile_blocks_low_confidence_validator_disagreement():
    cfg = SimpleNamespace(
        xauex_session_low_confidence_protect_r=0.7,
        xauex_session_protect_r=0.85,
        xauex_session_high_confidence_protect_r=1.0,
        xauex_session_trail_r=1.35,
        xauex_session_low_confidence_protect_lock_r=0.35,
        xauex_session_protect_lock_r=0.30,
        xauex_session_high_confidence_protect_lock_r=0.25,
    )
    signal = {
        "action": "SELL",
        "confidence": 0.42,
        "consensus_state": "disagreed",
        "validator_status": "reviewed",
        "validator_summary": "The proposed SELL signal contradicts the overall BUY bias.",
        "decision_packet": {
            "input_freshness": {
                "market_snapshot_state": "warning",
                "hard_blocker": False,
            }
        },
    }

    profile = build_xauex_assurance_profile(signal, cfg)

    assert profile.allow_trade is False
    assert profile.risk_multiplier == 0.0
    assert profile.reason == "LOW_ASSURANCE_VALIDATOR_DISAGREEMENT"


def test_assurance_profile_allows_confirmed_low_confidence_signal_at_reduced_risk():
    cfg = SimpleNamespace(
        xauex_low_confidence_lot_multiplier=0.25,
        xauex_session_low_confidence_protect_r=0.7,
        xauex_session_protect_r=0.85,
        xauex_session_high_confidence_protect_r=1.0,
        xauex_session_trail_r=1.35,
        xauex_session_low_confidence_protect_lock_r=0.35,
        xauex_session_protect_lock_r=0.30,
        xauex_session_high_confidence_protect_lock_r=0.25,
    )
    signal = {
        "action": "BUY",
        "confidence": 0.37,
        "consensus_state": "disagreed",
        "validator_status": "reviewed",
        "validator_summary": "Recent trade memory shows 2+ losses in the same direction.",
        "confirm_status": "CONFIRMED",
        "decision_packet": {
            "input_freshness": {
                "market_snapshot_state": "warning",
                "hard_blocker": False,
            }
        },
    }

    profile = build_xauex_assurance_profile(signal, cfg)

    assert profile.allow_trade is True
    assert profile.bucket == "low"
    assert profile.reason == "LOW_ASSURANCE_REDUCED_RISK"
    assert profile.risk_multiplier == 0.25
    assert profile.target_rr == 1.5
    assert profile.protect_r == 0.7
    assert profile.protect_lock_r == 0.35


def test_assurance_profile_still_blocks_very_low_confirmed_validator_disagreement():
    cfg = SimpleNamespace(
        xauex_low_confidence_lot_multiplier=0.25,
        xauex_session_low_confidence_protect_r=0.7,
        xauex_session_protect_r=0.85,
        xauex_session_high_confidence_protect_r=1.0,
        xauex_session_trail_r=1.35,
        xauex_session_low_confidence_protect_lock_r=0.35,
        xauex_session_protect_lock_r=0.30,
        xauex_session_high_confidence_protect_lock_r=0.25,
    )
    signal = {
        "action": "BUY",
        "confidence": 0.25,
        "consensus_state": "disagreed",
        "validator_status": "reviewed",
        "validator_summary": "The proposed BUY signal contradicts the dominant macro driver.",
        "confirm_status": "CONFIRMED",
        "decision_packet": {
            "input_freshness": {
                "market_snapshot_state": "fresh",
                "hard_blocker": False,
            }
        },
    }

    profile = build_xauex_assurance_profile(signal, cfg)

    assert profile.allow_trade is False
    assert profile.reason == "LOW_ASSURANCE_VALIDATOR_DISAGREEMENT"


def test_assurance_profile_allows_aligned_high_confidence_with_larger_target():
    cfg = SimpleNamespace(
        xauex_session_low_confidence_protect_r=0.7,
        xauex_session_protect_r=0.85,
        xauex_session_high_confidence_protect_r=1.0,
        xauex_session_trail_r=1.35,
        xauex_session_low_confidence_protect_lock_r=0.35,
        xauex_session_protect_lock_r=0.30,
        xauex_session_high_confidence_protect_lock_r=0.25,
    )
    signal = {
        "action": "BUY",
        "confidence": 0.74,
        "consensus_state": "aligned",
        "validator_status": "reviewed",
        "validator_summary": "The proposed signal is well-supported.",
        "decision_packet": {"input_freshness": {"market_snapshot_state": "fresh"}},
    }

    profile = build_xauex_assurance_profile(signal, cfg)

    assert profile.allow_trade is True
    assert profile.bucket == "high"
    assert profile.risk_multiplier == 1.5
    assert profile.target_rr == 2.5
    assert profile.protect_lock_r == 0.25


def test_take_profit_distance_expands_with_assurance_target():
    profile = SimpleNamespace(target_rr=2.5)

    distance = build_xauex_take_profit_distance(
        signal_take_profit=30.0,
        stop_distance=25.0,
        assurance=profile,
    )

    assert distance == 62.5


def test_protect_stop_locks_profit_in_r_not_fixed_one_dollar():
    short_stop = build_xauex_protect_stop_price(
        direction="SHORT",
        entry_price=4812.84,
        initial_risk_distance=25.0,
        lock_r=0.25,
        min_buffer_usd=1.0,
    )
    long_stop = build_xauex_protect_stop_price(
        direction="LONG",
        entry_price=4812.84,
        initial_risk_distance=25.0,
        lock_r=0.25,
        min_buffer_usd=1.0,
    )

    assert short_stop == 4806.59
    assert long_stop == 4819.09


def test_confirm_decision_confirms_tradeable_directional_signal():
    cfg = SimpleNamespace(
        xauex_signal_max_age_seconds=300,
        xauex_confirm_spread_max_dollars=1.0,
    )
    decision = build_xauex_confirm_decision(
        signal={
            "action": "BUY",
            "timestamp_utc": "2026-04-15T07:55:10Z",
            "decision_packet": {
                "input_freshness": {
                    "hard_blocker": False,
                    "market_snapshot_state": "fresh",
                }
            },
        },
        now_utc=datetime(2026, 4, 15, 7, 59, 0, tzinfo=timezone.utc),
        latest_quote={
            "bid": 4782.2,
            "ask": 4782.7,
            "updated_at_utc": "2026-04-15T07:58:58Z",
        },
        news_gate={"clear": True, "reason": ""},
        trend_snapshot={"alignment": "BULLISH"},
        shadow_signal={"action": "BUY"},
        config=cfg,
    )

    assert decision["status"] == "CONFIRMED"
    assert decision["reason"] == "CONFIRMED"


def test_confirm_decision_skips_when_microstructure_conflicts_with_signal():
    cfg = SimpleNamespace(
        xauex_signal_max_age_seconds=300,
        xauex_confirm_spread_max_dollars=1.0,
    )
    decision = build_xauex_confirm_decision(
        signal={
            "action": "BUY",
            "timestamp_utc": "2026-04-15T07:55:10Z",
            "decision_packet": {
                "input_freshness": {
                    "hard_blocker": False,
                    "market_snapshot_state": "fresh",
                }
            },
        },
        now_utc=datetime(2026, 4, 15, 7, 59, 0, tzinfo=timezone.utc),
        latest_quote={
            "bid": 4782.2,
            "ask": 4782.7,
            "updated_at_utc": "2026-04-15T07:58:58Z",
        },
        news_gate={"clear": True, "reason": ""},
        trend_snapshot={"alignment": "BEARISH"},
        shadow_signal={"action": "SELL"},
        config=cfg,
    )

    assert decision["status"] == "SKIP"
    assert decision["reason"] == "MICROSTRUCTURE_CONFLICT"


def test_confirm_decision_defers_strong_aligned_signal_for_first_microstructure_conflict():
    cfg = SimpleNamespace(
        xauex_signal_max_age_seconds=300,
        xauex_confirm_spread_max_dollars=1.0,
    )
    decision = build_xauex_confirm_decision(
        signal={
            "action": "BUY",
            "timestamp_utc": "2026-04-15T07:55:10Z",
            "confidence": 0.62,
            "consensus_state": "aligned",
            "validator_status": "reviewed",
            "decision_packet": {
                "input_freshness": {
                    "hard_blocker": False,
                    "market_snapshot_state": "fresh",
                }
            },
        },
        now_utc=datetime(2026, 4, 15, 7, 59, 0, tzinfo=timezone.utc),
        latest_quote={
            "bid": 4782.2,
            "ask": 4782.7,
            "updated_at_utc": "2026-04-15T07:58:58Z",
        },
        news_gate={"clear": True, "reason": ""},
        trend_snapshot={"alignment": "BEARISH"},
        shadow_signal={"action": "SELL"},
        config=cfg,
    )

    assert decision["status"] == "PENDING"
    assert decision["reason"] == "MICROSTRUCTURE_DEFERRED"
    assert decision["microstructure_policy"] == "defer"
    assert decision["microstructure_deferred"] is True
    assert decision["microstructure_defer_count"] == 1


def test_confirm_decision_soft_confirms_strong_aligned_signal_after_defer():
    cfg = SimpleNamespace(
        xauex_signal_max_age_seconds=300,
        xauex_confirm_spread_max_dollars=1.0,
    )
    decision = build_xauex_confirm_decision(
        signal={
            "action": "BUY",
            "timestamp_utc": "2026-04-15T07:55:10Z",
            "confidence": 0.62,
            "consensus_state": "aligned",
            "validator_status": "reviewed",
            "microstructure_deferred": True,
            "microstructure_defer_count": 1,
            "decision_packet": {
                "input_freshness": {
                    "hard_blocker": False,
                    "market_snapshot_state": "fresh",
                }
            },
        },
        now_utc=datetime(2026, 4, 15, 7, 59, 0, tzinfo=timezone.utc),
        latest_quote={
            "bid": 4782.2,
            "ask": 4782.7,
            "updated_at_utc": "2026-04-15T07:58:58Z",
        },
        news_gate={"clear": True, "reason": ""},
        trend_snapshot={"alignment": "BEARISH"},
        shadow_signal={"action": "SELL"},
        config=cfg,
    )

    assert decision["status"] == "CONFIRMED"
    assert decision["reason"] == "MICROSTRUCTURE_SOFT_CONFIRMED"
    assert decision["microstructure_soft_confirmed"] is True


def _counter_signal_config() -> SimpleNamespace:
    return SimpleNamespace(
        xauex_signal_max_age_seconds=300,
        xauex_confirm_spread_max_dollars=1.0,
        xauex_counter_signal_enabled=True,
        xauex_counter_signal_confidence=0.58,
        xauex_counter_signal_risk_multiplier=0.5,
        xauex_session_low_confidence_protect_r=0.7,
        xauex_session_protect_r=0.85,
        xauex_session_high_confidence_protect_r=1.0,
        xauex_session_trail_r=1.35,
        xauex_session_low_confidence_protect_lock_r=0.35,
        xauex_session_protect_lock_r=0.30,
        xauex_session_high_confidence_protect_lock_r=0.25,
    )


def test_counter_signal_candidate_flips_microstructure_veto_to_reduced_risk_sell():
    cfg = _counter_signal_config()
    original_signal = {
        "action": "BUY",
        "confidence": 0.4,
        "timestamp_utc": "2026-04-15T07:55:10Z",
        "consensus_state": "disagreed",
        "validator_status": "reviewed",
        "validator_summary": "Recent trade memory shows 2+ losses in the same direction.",
        "decision_packet": {
            "input_freshness": {
                "hard_blocker": False,
                "market_snapshot_state": "fresh",
            }
        },
    }

    candidate = build_xauex_counter_signal_candidate(
        signal=original_signal,
        original_confirm={"status": "SKIP", "reason": "MICROSTRUCTURE_CONFLICT"},
        now_utc=datetime(2026, 4, 15, 7, 59, 0, tzinfo=timezone.utc),
        latest_quote={"bid": 4782.2, "ask": 4782.7},
        news_gate={"clear": True, "reason": ""},
        trend_snapshot={"alignment": "BEARISH"},
        shadow_signal={"action": "SHADOW_SKIP"},
        config=cfg,
    )

    assert candidate is not None
    assert candidate["action"] == "SELL"
    assert candidate["confidence"] == 0.58
    assert candidate["consensus_state"] == "aligned"
    assert candidate["confirm_status"] == "CONFIRMED"
    assert candidate["confirm_reason"] == "COUNTER_SIGNAL_CONFIRMED"
    assert candidate["counter_signal"] is True
    assert candidate["counter_source_action"] == "BUY"
    assert candidate["counter_signal_risk_multiplier"] == 0.5

    profile = build_xauex_assurance_profile(candidate, cfg)
    assert profile.allow_trade is True
    assert profile.bucket == "medium"


def test_counter_signal_candidate_skips_strong_aligned_microstructure_veto():
    cfg = _counter_signal_config()
    original_signal = {
        "action": "BUY",
        "confidence": 0.62,
        "timestamp_utc": "2026-04-15T07:55:10Z",
        "consensus_state": "aligned",
        "validator_status": "reviewed",
        "decision_packet": {
            "input_freshness": {
                "hard_blocker": False,
                "market_snapshot_state": "fresh",
            }
        },
    }

    candidate = build_xauex_counter_signal_candidate(
        signal=original_signal,
        original_confirm={"status": "SKIP", "reason": "MICROSTRUCTURE_CONFLICT"},
        now_utc=datetime(2026, 4, 15, 7, 59, 0, tzinfo=timezone.utc),
        latest_quote={"bid": 4782.2, "ask": 4782.7},
        news_gate={"clear": True, "reason": ""},
        trend_snapshot={"alignment": "BEARISH"},
        shadow_signal={"action": "SELL"},
        config=cfg,
    )

    assert candidate is None


def test_counter_signal_candidate_ignores_non_microstructure_veto():
    cfg = _counter_signal_config()

    candidate = build_xauex_counter_signal_candidate(
        signal={"action": "BUY", "timestamp_utc": "2026-04-15T07:55:10Z"},
        original_confirm={"status": "SKIP", "reason": "STALE_SIGNAL"},
        now_utc=datetime(2026, 4, 15, 7, 59, 0, tzinfo=timezone.utc),
        latest_quote={"bid": 4782.2, "ask": 4782.7},
        news_gate={"clear": True, "reason": ""},
        trend_snapshot={"alignment": "BEARISH"},
        shadow_signal={"action": "SELL"},
        config=cfg,
    )

    assert candidate is None


def test_counter_signal_candidate_requires_inverse_confirmation():
    cfg = _counter_signal_config()

    candidate = build_xauex_counter_signal_candidate(
        signal={
            "action": "BUY",
            "timestamp_utc": "2026-04-15T07:55:10Z",
            "decision_packet": {"input_freshness": {"hard_blocker": False}},
        },
        original_confirm={"status": "SKIP", "reason": "MICROSTRUCTURE_CONFLICT"},
        now_utc=datetime(2026, 4, 15, 7, 59, 0, tzinfo=timezone.utc),
        latest_quote={"bid": 4782.2, "ask": 4782.7},
        news_gate={"clear": True, "reason": ""},
        trend_snapshot={"alignment": "BULLISH"},
        shadow_signal={"action": "BUY"},
        config=cfg,
    )

    assert candidate is None


def test_remaining_daily_loss_budget_accounts_for_realized_and_reserved_risk():
    remaining = calculate_xauex_remaining_daily_loss_budget(
        day_start_balance=10_000.0,
        daily_stop_pct=2.0,
        realized_daily_pnl=-70.0,
        open_reserved_risk=50.0,
    )

    assert remaining == 80.0


def test_structure_stop_distance_accepts_htflevels_container():
    orchestrator = _MODULE.BotOrchestrator.__new__(_MODULE.BotOrchestrator)
    orchestrator.config = SimpleNamespace(
        sl_min_dollars=10.0,
        xauex_session_structure_buffer_usd=2.5,
    )
    orchestrator._recent_h1_closes = [4700.0, 4710.0, 4720.0]
    orchestrator.level_manager = SimpleNamespace(
        _raw=HTFLevels(
            day_open=4710.0,
            day_high=4740.0,
            day_low=4690.0,
            day_close=4725.0,
            mn_open=4600.0,
            mn_high=4800.0,
            mn_low=4500.0,
            mn_close=4700.0,
            wk_open=4680.0,
            wk_high=4760.0,
            wk_low=4660.0,
            wk_close=4720.0,
            last_refresh_utc=datetime.now(timezone.utc),
            weekly_bar_open_time_utc=datetime.now(timezone.utc),
        )
    )

    distance = orchestrator._xauex_structure_stop_distance(direction=-1, current_price=4725.0)

    assert distance == 17.5


def test_xauex_confirm_timestamp_is_fresh_when_within_max_age():
    """A confirm_timestamp recorded within the freshness window is considered fresh."""
    now = datetime(2026, 5, 8, 12, 30, 0, tzinfo=timezone.utc)
    confirm_timestamp = "2026-05-08T12:25:00Z"  # 5 minutes old
    assert is_xauex_confirm_timestamp_fresh(
        confirm_timestamp_utc=confirm_timestamp,
        now_utc=now,
        max_age_seconds=600,
    ) is True


def test_xauex_confirm_timestamp_is_stale_beyond_max_age():
    """A confirm_timestamp older than max_age must be flagged stale so the
    poller will re-run the confirm pass before placing an order."""
    now = datetime(2026, 5, 8, 12, 30, 0, tzinfo=timezone.utc)
    confirm_timestamp = "2026-05-08T12:00:00Z"  # 30 minutes old
    assert is_xauex_confirm_timestamp_fresh(
        confirm_timestamp_utc=confirm_timestamp,
        now_utc=now,
        max_age_seconds=600,
    ) is False


def test_xauex_confirm_timestamp_treats_missing_or_invalid_as_stale():
    """Empty string, None, or unparseable timestamps must be treated as stale
    so the poller re-confirms instead of trusting a missing freshness check."""
    now = datetime(2026, 5, 8, 12, 30, 0, tzinfo=timezone.utc)
    assert is_xauex_confirm_timestamp_fresh("", now, 600) is False
    assert is_xauex_confirm_timestamp_fresh(None, now, 600) is False  # type: ignore[arg-type]
    assert is_xauex_confirm_timestamp_fresh("not-a-timestamp", now, 600) is False


def test_xauex_confirm_default_max_age_is_ten_minutes():
    """Document the default freshness window. If this changes the test should
    fail noisily so we update operator documentation."""
    assert XAUEX_CONFIRM_MAX_AGE_SECONDS_DEFAULT == 600


def test_xauex_pattern_check_matches_long_with_bullish_engulfing():
    """The XAUEX path historically placed every order with pattern=NONE,
    skipping all candle confirmation. With the gate active a BUY signal must
    be backed by a bullish pattern aligned to a nearby HTF level."""
    from datetime import datetime, timezone
    from bot.patterns.detector import Candle, PatternDetector, PatternType

    config = SimpleNamespace(
        pin_max_body_ratio=0.30,
        pin_min_wick_ratio=0.60,
        engulf_min_body_ratio=1.0,
        consolidation_break_buffer=0.10,
        consolidation_min_bars=3,
        candle_proximity_dollars=4.0,
    )
    detector = PatternDetector(config)

    # Strong bullish engulfing at 4720 level: prev red small, signal green large.
    prev = Candle(open=4722.0, high=4722.5, low=4719.5, close=4720.0, open_time=datetime(2026, 5, 8, 7, tzinfo=timezone.utc))
    signal = Candle(open=4719.5, high=4724.0, low=4719.0, close=4723.5, open_time=datetime(2026, 5, 8, 8, tzinfo=timezone.utc))

    pattern, level, ok, reason = _MODULE.xauex_pattern_check(
        pattern_detector=detector,
        prev_candle=prev,
        signal_candle=signal,
        candidate_levels=[4720.0, 4750.0, 4690.0],
        direction=+1,  # LONG
    )
    assert ok is True
    assert level == 4720.0
    assert pattern == PatternType.BULLISH_ENGULFING
    assert reason == "MATCH"


def test_xauex_pattern_check_blocks_when_no_pattern_aligns_to_level():
    """When the nearest level has no detectable pattern, the gate must
    reject (ok=False, reason=NO_PATTERN)."""
    from datetime import datetime, timezone
    from bot.patterns.detector import Candle, PatternDetector

    config = SimpleNamespace(
        pin_max_body_ratio=0.30,
        pin_min_wick_ratio=0.60,
        engulf_min_body_ratio=1.0,
        consolidation_break_buffer=0.10,
        consolidation_min_bars=3,
        candle_proximity_dollars=4.0,
    )
    detector = PatternDetector(config)

    # Both candles drifting up — no clear pattern.
    prev = Candle(open=4720.0, high=4721.0, low=4719.5, close=4720.8, open_time=datetime(2026, 5, 8, 7, tzinfo=timezone.utc))
    signal = Candle(open=4720.8, high=4721.5, low=4720.5, close=4721.2, open_time=datetime(2026, 5, 8, 8, tzinfo=timezone.utc))

    pattern, level, ok, reason = _MODULE.xauex_pattern_check(
        pattern_detector=detector,
        prev_candle=prev,
        signal_candle=signal,
        candidate_levels=[4750.0],
        direction=+1,
    )
    assert ok is False
    assert reason in {"NO_PATTERN", "DIRECTION_MISMATCH"}


def test_xauex_pattern_check_blocks_when_pattern_disagrees_with_direction():
    """A bearish pattern at the level cannot back a LONG signal."""
    from datetime import datetime, timezone
    from bot.patterns.detector import Candle, PatternDetector, PatternType

    config = SimpleNamespace(
        pin_max_body_ratio=0.30,
        pin_min_wick_ratio=0.60,
        engulf_min_body_ratio=1.0,
        consolidation_break_buffer=0.10,
        consolidation_min_bars=3,
        candle_proximity_dollars=4.0,
    )
    detector = PatternDetector(config)

    # Bearish engulfing at level 4720.
    prev = Candle(open=4719.0, high=4720.5, low=4718.5, close=4720.0, open_time=datetime(2026, 5, 8, 7, tzinfo=timezone.utc))
    signal = Candle(open=4720.5, high=4720.8, low=4716.0, close=4716.5, open_time=datetime(2026, 5, 8, 8, tzinfo=timezone.utc))

    pattern, level, ok, reason = _MODULE.xauex_pattern_check(
        pattern_detector=detector,
        prev_candle=prev,
        signal_candle=signal,
        candidate_levels=[4720.0],
        direction=+1,  # LONG against bearish pattern
    )
    # The detector may return BEARISH_ENGULFING or NONE depending on body ratio.
    assert ok is False
    assert pattern != PatternType.BULLISH_ENGULFING


def test_xauex_pattern_check_returns_no_levels_when_candidates_empty():
    from datetime import datetime, timezone
    from bot.patterns.detector import Candle, PatternDetector

    config = SimpleNamespace(
        pin_max_body_ratio=0.30,
        pin_min_wick_ratio=0.60,
        engulf_min_body_ratio=1.0,
        consolidation_break_buffer=0.10,
        consolidation_min_bars=3,
        candle_proximity_dollars=4.0,
    )
    detector = PatternDetector(config)
    prev = Candle(open=10.0, high=11.0, low=9.0, close=10.5, open_time=datetime.now(timezone.utc))
    signal = Candle(open=10.5, high=11.2, low=10.0, close=10.8, open_time=datetime.now(timezone.utc))

    pattern, level, ok, reason = _MODULE.xauex_pattern_check(
        pattern_detector=detector,
        prev_candle=prev,
        signal_candle=signal,
        candidate_levels=[],
        direction=+1,
    )
    assert ok is False
    assert level is None
    assert reason == "NO_LEVELS"


@pytest.mark.asyncio
async def test_xauex_mode_refreshes_active_scalp_trend_context_on_candle_close():
    orchestrator = BotOrchestrator.__new__(BotOrchestrator)
    orchestrator.config = SimpleNamespace(
        xauex_mode=True,
        execution_timeframe="M5",
        scalp_slow_ema_period=3,
        scalp_atr_period=2,
        scalp_pullback_lookback_bars=3,
    )
    orchestrator.active_strategy_mode = "SCALP_V1"
    orchestrator.execution_timeframe = "M5"
    orchestrator._strategy_data_status = {}
    orchestrator._trend_snapshot = {"alignment": "BULLISH", "reason": "STARTUP_SNAPSHOT"}
    orchestrator._recent_h1_closes = []
    orchestrator._macro_regime = None
    orchestrator._trade_policy = None
    orchestrator.scalp_strategy = SimpleNamespace(
        state_trend=lambda **_: {
            "alignment": "BEARISH",
            "reason": "REFRESHED",
            "execution_timeframe": "M5",
        }
    )

    async def fake_fetch_execution_bars(timeframe, count):
        assert timeframe == "M5"
        assert count > 0
        return (
            [
                {"close": 4710.0},
                {"close": 4705.0},
                {"close": 4700.0},
                {"close": 4695.0},
                {"close": 4690.0},
            ],
            3,
        )

    async def fake_fetch_daily_closes():
        return [4800.0 - idx for idx in range(30)]

    orchestrator._fetch_execution_bars = fake_fetch_execution_bars
    orchestrator._fetch_daily_closes = fake_fetch_daily_closes
    orchestrator._aggregate_h1_closes_from_m5 = lambda bars: [4750.0 - idx for idx in range(210)]
    orchestrator._load_macro_regime = lambda: {"state": "test"}
    orchestrator._load_trade_policy = lambda: None

    finalized = {"called": False}

    async def fake_finalize():
        finalized["called"] = True

    orchestrator._finalize_candle = fake_finalize

    await orchestrator._process_candle_close(
        price=4690.0,
        timestamp=datetime(2026, 4, 23, 12, 30, tzinfo=timezone.utc),
    )

    assert finalized["called"] is True
    assert orchestrator._trend_snapshot["alignment"] == "BEARISH"
    assert orchestrator._trend_snapshot["reason"] == "REFRESHED"
    assert orchestrator._strategy_data_status["SCALP_V1"]["data_ready"] is True
    assert orchestrator._strategy_data_status["SCALP_V1"]["reason"] == "OK"
