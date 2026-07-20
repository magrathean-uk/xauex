"""
XAUEX Trading Bot Main Entry Point

Starts the bot daemon with asyncio event loop.
Handles startup sequence, main loop, and graceful shutdown.

Entry point: asyncio.run(main()) drives the pure-asyncio event loop.
No Twisted dependency — transport is implemented in bot/api/proto_transport.py.
"""

import asyncio
import copy
import json
import time
import logging
import logging.handlers
import math
import os
import queue
import signal
import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, List, Dict, Tuple
from zoneinfo import ZoneInfo

from xauex.config import Config, load_config
from xauex.live_windows import active_entry_slot, all_live_windows, get_live_window, london_trade_day
from xauex.bot.api.client import ApiClient
from xauex.bot.levels.htf_levels import LevelManager
from xauex.bot.patterns.detector import PatternDetector, CandleWatcher, PatternType
from xauex.bot.filters.macro_regime import MacroRegime, MacroRegimeLoader
from xauex.bot.filters.trade_policy import TradePolicy, TradePolicyLoader
from xauex.bot.filters.session import SessionFilter
from xauex.bot.filters.news import NewsFilter
from xauex.bot.filters.trend import TrendFilter
from xauex.bot.risk.sizing import calculate_lot_size, calculate_xauex_lot_size_from_cash_risk
from xauex.bot.risk.gates import RiskGates, RiskState
from xauex.bot.execution.executor import Executor, TrackedPosition
from xauex.bot.strategies.ema_pullback_h1 import EMAPullbackH1Strategy
from xauex.bot.strategies.scalp_v1 import M5ScalpStrategy
from xauex.bot.state.writer import StateWriter
from xauex.bot.state.risk_persistence import save_risk_state, load_risk_state
from xauex.bot.watchdog import Watchdog
from xauex.bot.health import HealthCheck
from xauex.shared.event_journal import safe_append_event
from xauex.shared.manual_commands import (
    ManualCommandConsumeResult,
    consume_manual_command_file,
)
from xauex.shared.replay_guard import CommandReplayGuard

logger = logging.getLogger(__name__)

_EXECUTION_BAR_LOOKBACK = 500
_DAILY_BAR_LOOKBACK = 120
_SCALP_BAR_LOOKBACK = 2500
_EMA_PULLBACK_BAR_LOOKBACK = 260
_XAUEX_SLOT_RETRY_BACKOFF_SECONDS = 45
_XAUEX_STRONG_SIGNAL_MICROSTRUCTURE_CONFIDENCE = 0.60
_XAUEX_MICROSTRUCTURE_SOFT_RISK_MULTIPLIER = 0.5
_XAUEX_CLOSE_REQUEST_TTL_SECONDS = 180
_XAUEX_REPEATED_LOG_INTERVAL_SECONDS = 300
_XAUEX_ENTRY_HARD_BLOCK_SCORE = 3
_XAUEX_ENTRY_POLICY_FACTOR_WEIGHTS = {
    "STALE_CONTEXT_LOW_CONFIDENCE": 1,
    "PRICE_CONFLICT": 1,
    "FLIP_BLOCKED_LOW_CONFIDENCE": 1,
    "FLIP_BLOCKED_MACRO_UNCHANGED": 1,
    "COUNTER_SIGNAL_DAILY_LIMIT": 1,
    "COUNTER_SIGNAL_AFTER_LOSS": 2,
    "COUNTER_SIGNAL_STALE_CONTEXT": 1,
    "SAME_DIRECTION_LOSS_COOLDOWN": 2,
    "LOWER_THIRD_NO_CHASE": 1,
    "PATTERN_MISSING": 2,
    "PATTERN_DIRECTION_MISMATCH": 3,
    "PATTERN_DATA_UNAVAILABLE": 1,
}


@dataclass(frozen=True)
class XauexAssuranceProfile:
    bucket: str
    score: float
    allow_trade: bool
    reason: str
    risk_multiplier: float
    target_rr: float
    protect_r: float
    trail_r: float
    protect_lock_r: float


def calculate_xauex_remaining_daily_loss_budget(
    *,
    day_start_balance: float,
    daily_stop_pct: float,
    realized_daily_pnl: float,
    open_reserved_risk: float,
) -> float:
    daily_limit = max(0.0, float(day_start_balance or 0.0)) * (max(0.0, float(daily_stop_pct or 0.0)) / 100.0)
    realized_loss = max(0.0, -float(realized_daily_pnl or 0.0))
    reserved_risk = max(0.0, float(open_reserved_risk or 0.0))
    return round(max(0.0, daily_limit - realized_loss - reserved_risk), 2)


def calculate_xauex_assurance_cash_risk(
    *,
    cash_risk_budget: float,
    assurance_risk_multiplier: float,
    cooldown_multiplier: float,
    session_slot_multiplier: float,
    counter_signal_risk_multiplier: float,
    microstructure_risk_multiplier: float,
    entry_quality_risk_multiplier: float = 1.0,
    continuation_addon_risk_multiplier: float = 1.0,
    minimum_executable_risk: float = 0.0,
    allow_minimum_executable_risk_lift: bool = True,
    force_minimum_executable_risk: bool = False,
) -> float:
    budget = max(0.0, float(cash_risk_budget))
    reduced_risk = round(
        float(cash_risk_budget)
        * float(assurance_risk_multiplier)
        * float(cooldown_multiplier)
        * float(session_slot_multiplier)
        * float(counter_signal_risk_multiplier)
        * float(microstructure_risk_multiplier)
        * float(entry_quality_risk_multiplier)
        * float(continuation_addon_risk_multiplier),
        2,
    )
    min_executable = round(max(0.0, float(minimum_executable_risk or 0.0)), 2)
    if force_minimum_executable_risk:
        if 0.0 < min_executable <= budget:
            return min_executable
        return 0.0
    if allow_minimum_executable_risk_lift and 0.0 < reduced_risk < min_executable <= budget:
        return min_executable
    return reduced_risk


def _is_strong_aligned_signal(*, signal: Dict[str, object]) -> bool:
    action = str(signal.get("action", "HOLD") or "HOLD").upper()
    if action not in {"BUY", "SELL"}:
        return False

    signal_timestamp = str(signal.get("timestamp_utc", "") or "").strip()
    if not signal_timestamp:
        return False

    decision_packet = signal.get("decision_packet")
    input_freshness = {}
    if isinstance(decision_packet, dict):
        input_freshness = (
            decision_packet.get("input_freshness") if isinstance(decision_packet.get("input_freshness"), dict) else {}
        )
    if bool(input_freshness.get("hard_blocker")):
        return False

    confidence = _safe_signal_float(signal.get("confidence"), 0.0)
    if confidence < _XAUEX_STRONG_SIGNAL_MICROSTRUCTURE_CONFIDENCE:
        return False

    consensus = str(signal.get("consensus_state", "") or "").strip().lower()
    validator_status = str(signal.get("validator_status", "") or "").strip().lower()
    return consensus in {"aligned", "confirmed"} and validator_status == "reviewed"


# Default freshness window for an upstream confirm_status read from cmd.json.
# The London entry windows are 5 minutes wide and confirm passes run ~1 min
# before each window opens. A confirm older than this is almost certainly a
# stale leftover from a previous slot or an aborted run, and trusting it can
# let news-blocked or microstructure-vetoed states slip through. Operators can
# override via XAUEX_CONFIRM_MAX_AGE_SECONDS in the env file.
XAUEX_CONFIRM_MAX_AGE_SECONDS_DEFAULT = 600


_XAUEX_BULLISH_PATTERN_TYPES: frozenset = frozenset()
_XAUEX_BEARISH_PATTERN_TYPES: frozenset = frozenset()
_EXECUTION_TIMEFRAME_MINUTES = {
    "M1": 1,
    "M2": 2,
    "M3": 3,
    "M4": 4,
    "M5": 5,
    "M10": 10,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "H12": 720,
    "D1": 1440,
}


@dataclass(frozen=True)
class XauexPatternEvidence:
    """Closed-bar pattern evidence consumed by the XAUEX entry policy."""

    factor: str
    weight: int
    pattern: PatternType = PatternType.NONE
    level: Optional[float] = None
    timeframe: str = ""
    previous_open_time_utc: str = ""
    signal_open_time_utc: str = ""
    detail: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "factor": self.factor,
            "weight": self.weight,
            "pattern": self.pattern.name,
            "level": self.level,
            "timeframe": self.timeframe,
            "previous_open_time_utc": self.previous_open_time_utc,
            "signal_open_time_utc": self.signal_open_time_utc,
            "detail": self.detail,
        }


def _populate_xauex_pattern_sets() -> None:
    """Lazy import to avoid PatternType being unresolved at module import time
    if the bot package isn't fully initialised yet (tests load main.py via
    importlib spec)."""
    global _XAUEX_BULLISH_PATTERN_TYPES, _XAUEX_BEARISH_PATTERN_TYPES
    if _XAUEX_BULLISH_PATTERN_TYPES:
        return
    from bot.patterns.detector import PatternType as _PT
    _XAUEX_BULLISH_PATTERN_TYPES = frozenset({
        _PT.BULLISH_ENGULFING,
        _PT.BULLISH_PIN_BAR,
        _PT.BULLISH_CONTINUATION_CLOSE,
        _PT.BULLISH_CONSOLIDATION_BREAK,
    })
    _XAUEX_BEARISH_PATTERN_TYPES = frozenset({
        _PT.BEARISH_ENGULFING,
        _PT.BEARISH_PIN_BAR,
        _PT.BEARISH_CONTINUATION_CLOSE,
        _PT.BEARISH_CONSOLIDATION_BREAK,
    })


def xauex_pattern_check(
    *,
    pattern_detector,
    prev_candle,
    signal_candle,
    candidate_levels,
    direction: int,
):
    """Run pattern detection against the candidate HTF levels and return
    whether the strongest match aligns with the proposed direction. Inside
    bars are retained as neutral evidence; they never confirm either side.

    Returns a tuple ``(pattern, level, ok, reason)`` where ``ok`` is True only
    when a pattern fires that supports ``direction``.
    """
    _populate_xauex_pattern_sets()
    from bot.patterns.detector import PatternType as _PT

    if not candidate_levels:
        return _PT.NONE, None, False, "NO_LEVELS"

    sorted_levels = sorted(candidate_levels, key=lambda lvl: abs(signal_candle.close - float(lvl)))
    rank = {
        _PT.BULLISH_ENGULFING: 4,
        _PT.BEARISH_ENGULFING: 4,
        _PT.BULLISH_CONTINUATION_CLOSE: 3,
        _PT.BEARISH_CONTINUATION_CLOSE: 3,
        _PT.BULLISH_PIN_BAR: 2,
        _PT.BEARISH_PIN_BAR: 2,
        _PT.INSIDE_BAR: 1,
    }
    best_pattern = _PT.NONE
    best_level: Optional[float] = None
    best_rank = -1
    for lvl in sorted_levels:
        result = pattern_detector.detect(prev_candle, signal_candle, float(lvl))
        if result.pattern == _PT.NONE:
            continue
        current_rank = rank.get(result.pattern, 0)
        if current_rank > best_rank:
            best_rank = current_rank
            best_pattern = result.pattern
            best_level = float(lvl)

    if best_pattern == _PT.NONE:
        return _PT.NONE, sorted_levels[0] if sorted_levels else None, False, "NO_PATTERN"

    if best_pattern == _PT.INSIDE_BAR:
        return best_pattern, best_level, False, "NEUTRAL_PATTERN"

    if direction > 0 and best_pattern in _XAUEX_BULLISH_PATTERN_TYPES:
        return best_pattern, best_level, True, "MATCH"
    if direction < 0 and best_pattern in _XAUEX_BEARISH_PATTERN_TYPES:
        return best_pattern, best_level, True, "MATCH"
    return best_pattern, best_level, False, "DIRECTION_MISMATCH"


def select_closed_execution_pattern_bars(
    bars: List[Dict[str, object]],
    *,
    now_utc: datetime,
    timeframe: str,
) -> Optional[tuple[Dict[str, object], Dict[str, object]]]:
    """Return the latest two fully closed bars for XAUEX pattern evaluation."""
    minutes = _EXECUTION_TIMEFRAME_MINUTES.get(str(timeframe or "").upper())
    if minutes is None:
        return None

    close_after = timedelta(minutes=minutes)
    now = now_utc.astimezone(timezone.utc)
    closed: List[tuple[datetime, Dict[str, object]]] = []
    for bar in bars:
        if not isinstance(bar, dict):
            continue
        raw_open_time = bar.get("open_time")
        if isinstance(raw_open_time, datetime):
            open_time = raw_open_time
        elif isinstance(raw_open_time, str):
            try:
                open_time = datetime.fromisoformat(raw_open_time.replace("Z", "+00:00"))
            except ValueError:
                continue
        else:
            continue
        if open_time.tzinfo is None:
            open_time = open_time.replace(tzinfo=timezone.utc)
        else:
            open_time = open_time.astimezone(timezone.utc)
        if open_time + close_after <= now:
            closed.append((open_time, bar))

    if len(closed) < 2:
        return None
    closed.sort(key=lambda item: item[0])
    return closed[-2][1], closed[-1][1]


def is_xauex_confirm_timestamp_fresh(
    confirm_timestamp_utc: Optional[str],
    now_utc: datetime,
    max_age_seconds: int,
) -> bool:
    """Return True only when the provided ISO confirm timestamp is parseable
    and within max_age_seconds of now_utc. Missing or invalid timestamps are
    treated as stale (False) so the caller forces a re-confirmation."""
    if not confirm_timestamp_utc:
        return False
    text = str(confirm_timestamp_utc).strip()
    if not text:
        return False
    try:
        confirm_dt = datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return False
    age_seconds = (now_utc.astimezone(timezone.utc) - confirm_dt).total_seconds()
    if age_seconds < 0:
        return False
    return age_seconds <= max(0, int(max_age_seconds))


def build_xauex_confirm_decision(
    *,
    signal: Dict[str, object],
    now_utc: datetime,
    latest_quote: Dict[str, object],
    news_gate: Dict[str, object],
    trend_snapshot: Optional[Dict[str, object]],
    shadow_signal: Optional[Dict[str, object]],
    config: Config,
    signal_max_age_override_seconds: Optional[int] = None,
    grace_entry: bool = False,
) -> Dict[str, object]:
    action = str(signal.get("action", "HOLD") or "HOLD").upper()
    confirm_timestamp_utc = now_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = {
        "status": "SKIP",
        "reason": "NO_DIRECTIONAL_SIGNAL",
        "timestamp_utc": confirm_timestamp_utc,
        "signal_age_seconds": None,
        "spread_usd": None,
        "news_gate_clear": bool(news_gate.get("clear", True)),
    }
    if action not in {"BUY", "SELL"}:
        return result

    signal_timestamp = str(signal.get("timestamp_utc", "") or "").strip()
    try:
        signal_time = datetime.fromisoformat(signal_timestamp.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        result["reason"] = "INVALID_SIGNAL_TIMESTAMP"
        return result

    signal_age_seconds = max(0, int((now_utc.astimezone(timezone.utc) - signal_time).total_seconds()))
    result["signal_age_seconds"] = signal_age_seconds
    signal_max_age_seconds = int(getattr(config, "xauex_signal_max_age_seconds", 300) or 300)
    if signal_max_age_override_seconds is not None:
        signal_max_age_seconds = max(signal_max_age_seconds, int(signal_max_age_override_seconds))
    if signal_age_seconds > signal_max_age_seconds:
        result["reason"] = "STALE_SIGNAL"
        return result

    input_freshness = {}
    decision_packet = signal.get("decision_packet")
    if isinstance(decision_packet, dict):
        input_freshness = decision_packet.get("input_freshness") if isinstance(decision_packet.get("input_freshness"), dict) else {}
    if bool(input_freshness.get("hard_blocker")):
        result["reason"] = "INPUT_HARD_BLOCKER"
        return result

    bid = latest_quote.get("bid")
    ask = latest_quote.get("ask")
    if bid is None or ask is None:
        result["reason"] = "NO_QUOTE"
        return result
    try:
        spread_usd = round(float(ask) - float(bid), 4)
    except (TypeError, ValueError):
        result["reason"] = "NO_QUOTE"
        return result
    result["spread_usd"] = spread_usd
    if spread_usd <= 0:
        result["reason"] = "NO_QUOTE"
        return result
    spread_guard = float(getattr(config, "xauex_confirm_spread_max_dollars", 1.0) or 1.0)
    if spread_usd > spread_guard:
        result["reason"] = "SPREAD_TOO_WIDE"
        return result

    if not bool(news_gate.get("clear", True)):
        result["reason"] = str(news_gate.get("reason") or "LIVE_NEWS_HARD_BLOCK")
        return result

    trend_alignment = str((trend_snapshot or {}).get("alignment", "") or "").upper()
    shadow_action = str((shadow_signal or {}).get("action", "") or "").upper()
    strong_aligned = _is_strong_aligned_signal(signal=signal)
    was_microstructure_deferred = bool(signal.get("microstructure_deferred", False))
    microstructure_defer_count = int(signal.get("microstructure_defer_count") or 0)

    if action == "BUY":
        opposing_trend = trend_alignment in {"BEARISH", "SHORT"}
        opposing_shadow = shadow_action == "SELL"
    else:
        opposing_trend = trend_alignment in {"BULLISH", "LONG"}
        opposing_shadow = shadow_action == "BUY"
    if opposing_trend or opposing_shadow:
        if strong_aligned and (was_microstructure_deferred or microstructure_defer_count > 0):
            result["status"] = "CONFIRMED"
            result["reason"] = (
                "MICROSTRUCTURE_SOFT_CONFIRMED_GRACE" if grace_entry else "MICROSTRUCTURE_SOFT_CONFIRMED"
            )
            result["microstructure_policy"] = "soft_confirmed"
            result["microstructure_deferred"] = True
            result["microstructure_soft_confirmed"] = True
            result["microstructure_defer_count"] = microstructure_defer_count or 1
            return result
        if strong_aligned:
            result["status"] = "PENDING"
            result["reason"] = "MICROSTRUCTURE_DEFERRED"
            result["microstructure_policy"] = "defer"
            result["microstructure_deferred"] = True
            result["microstructure_defer_count"] = 1
            return result
        result["reason"] = "MICROSTRUCTURE_CONFLICT"
        return result

    result["status"] = "CONFIRMED"
    result["reason"] = "CONFIRMED"
    if strong_aligned and was_microstructure_deferred:
        result["microstructure_policy"] = "deferred_cleared"
        result["microstructure_deferred"] = True
        result["microstructure_defer_count"] = microstructure_defer_count
    return result


def build_xauex_counter_signal_candidate(
    *,
    signal: Dict[str, object],
    original_confirm: Dict[str, object],
    now_utc: datetime,
    latest_quote: Dict[str, object],
    news_gate: Dict[str, object],
    trend_snapshot: Optional[Dict[str, object]],
    shadow_signal: Optional[Dict[str, object]],
    config: Config,
) -> Optional[Dict[str, object]]:
    """Build a reduced-risk inverse candidate only when microstructure vetoed the source signal."""
    if not bool(getattr(config, "xauex_counter_signal_enabled", False)):
        return None

    source_action = str(signal.get("action", "HOLD") or "HOLD").upper()
    if source_action not in {"BUY", "SELL"}:
        return None
    if _is_strong_aligned_signal(signal=signal):
        return None

    original_status = str(original_confirm.get("status", "SKIP") or "SKIP").upper()
    original_reason = str(original_confirm.get("reason", "") or "").upper()
    if original_status == "CONFIRMED" or original_reason != "MICROSTRUCTURE_CONFLICT":
        return None

    counter_action = "SELL" if source_action == "BUY" else "BUY"
    candidate = copy.deepcopy(signal)
    candidate["action"] = counter_action
    candidate["confidence"] = round(
        max(0.0, min(1.0, float(getattr(config, "xauex_counter_signal_confidence", 0.58) or 0.58))),
        2,
    )
    candidate["consensus_state"] = "aligned"
    candidate["validator_status"] = "reviewed"
    candidate["validator_summary"] = (
        f"Counter-signal candidate: source {source_action} was vetoed by microstructure; "
        f"{counter_action} must pass live confirmation before execution."
    )
    candidate["counter_signal"] = True
    candidate["counter_source_action"] = source_action
    candidate["counter_source_confidence"] = signal.get("confidence")
    candidate["counter_source_confirm_reason"] = original_reason
    candidate["counter_signal_risk_multiplier"] = round(
        max(0.0, min(1.0, float(getattr(config, "xauex_counter_signal_risk_multiplier", 0.5) or 0.5))),
        2,
    )

    if isinstance(candidate.get("decision_packet"), dict):
        candidate["decision_packet"] = copy.deepcopy(candidate["decision_packet"])
    else:
        candidate["decision_packet"] = {}
    candidate["decision_packet"]["counter_signal"] = {
        "enabled": True,
        "source_action": source_action,
        "counter_action": counter_action,
        "source_confirm_reason": original_reason,
        "risk_multiplier": candidate["counter_signal_risk_multiplier"],
    }

    counter_confirm = build_xauex_confirm_decision(
        signal=candidate,
        now_utc=now_utc,
        latest_quote=latest_quote,
        news_gate=news_gate,
        trend_snapshot=trend_snapshot,
        shadow_signal=shadow_signal,
        config=config,
    )
    if str(counter_confirm.get("status", "SKIP") or "SKIP").upper() != "CONFIRMED":
        return None

    candidate["confirm_status"] = "CONFIRMED"
    candidate["confirm_reason"] = "COUNTER_SIGNAL_CONFIRMED"
    candidate["confirm_timestamp_utc"] = str(counter_confirm.get("timestamp_utc") or "")
    candidate["counter_confirm_reason"] = str(counter_confirm.get("reason") or "")
    return candidate


def _position_session(position: object) -> Dict[str, object]:
    if isinstance(position, dict):
        metadata = position.get("metadata") if isinstance(position.get("metadata"), dict) else {}
    else:
        metadata = getattr(position, "metadata", {}) or {}
    if not isinstance(metadata, dict):
        return {}
    session = metadata.get("session")
    return dict(session) if isinstance(session, dict) else {}


def _position_id(position: object) -> str:
    if isinstance(position, dict):
        return str(position.get("position_id", "") or "")
    return str(getattr(position, "position_id", "") or "")


def _position_direction(position: object) -> str:
    if isinstance(position, dict):
        return _normalise_direction_label(position.get("direction"))
    return _normalise_direction_label(getattr(position, "direction", ""))


def _signal_locked_direction_matches(signal: Dict[str, object], action: str) -> bool:
    persistence = signal.get("directional_persistence")
    if not isinstance(persistence, dict):
        return False
    policy = str(persistence.get("policy", "") or "").upper()
    if policy not in {"ALIGNED_WITH_LOCK", "LOCK_DIRECTION"}:
        return False
    for state_key in ("next_state", "previous_state"):
        state = persistence.get(state_key)
        if not isinstance(state, dict):
            continue
        primary = str(state.get("primary_direction", "") or "").upper()
        if primary:
            return primary == action
    return False


def build_xauex_continuation_addon_decision(
    *,
    signal: Dict[str, object],
    open_positions: List[object],
    slot: str,
    config: Config,
) -> Dict[str, object]:
    """Allow one reduced-risk continuation add-on only after the parent trade is protected."""
    result: Dict[str, object] = {
        "allowed": False,
        "reason": "XAUEX_POSITION_OPEN",
        "parent_position_id": "",
        "risk_multiplier": 1.0,
    }
    xauex_positions = [position for position in open_positions if _position_owner(position) == "xauex"]
    if not xauex_positions:
        result["reason"] = "NO_XAUEX_POSITION_OPEN"
        return result
    if not bool(getattr(config, "xauex_continuation_addon_enabled", False)):
        return result

    slot_name = str(slot or "").upper()
    if slot_name not in {"MIDDAY", "US_OPEN"}:
        result["reason"] = "CONTINUATION_SLOT_NOT_ELIGIBLE"
        return result

    action = str(signal.get("action", "HOLD") or "HOLD").upper()
    if action not in {"BUY", "SELL"}:
        result["reason"] = "CONTINUATION_NO_DIRECTION"
        return result
    direction = "LONG" if action == "BUY" else "SHORT"

    consensus = str(signal.get("consensus_state", "") or "").strip().lower()
    validator_status = str(signal.get("validator_status", "") or "").strip().lower()
    if consensus not in {"aligned", "confirmed"} or validator_status != "reviewed":
        result["reason"] = "CONTINUATION_SIGNAL_NOT_ALIGNED"
        return result
    if not _signal_locked_direction_matches(signal, action):
        result["reason"] = "CONTINUATION_NOT_LOCKED_DIRECTION"
        return result

    min_confidence = max(
        0.0,
        min(1.0, float(getattr(config, "xauex_continuation_addon_min_confidence", 0.55) or 0.55)),
    )
    confidence = _safe_signal_float(signal.get("confidence"), 0.0)
    if confidence < min_confidence:
        result["reason"] = "CONTINUATION_CONFIDENCE_TOO_LOW"
        return result

    if any(bool(_position_session(position).get("continuation_addon")) for position in xauex_positions):
        result["reason"] = "CONTINUATION_ADDON_ALREADY_OPEN"
        return result
    if len(xauex_positions) >= 2:
        result["reason"] = "CONTINUATION_POSITION_LIMIT_REACHED"
        return result
    if any(_position_direction(position) != direction for position in xauex_positions):
        result["reason"] = "CONTINUATION_DIRECTION_CONFLICT"
        return result

    protected_parent: Optional[object] = None
    for position in xauex_positions:
        session = _position_session(position)
        phase = str(session.get("phase", "OBSERVE") or "OBSERVE").upper()
        if phase in {"PROTECT", "TRAIL"}:
            protected_parent = position
            break
    if protected_parent is None:
        result["reason"] = "CONTINUATION_PARENT_NOT_PROTECTED"
        return result

    result["allowed"] = True
    result["reason"] = "CONTINUATION_ADDON_ALLOWED"
    result["parent_position_id"] = _position_id(protected_parent)
    result["risk_multiplier"] = round(
        max(0.05, min(1.0, float(getattr(config, "xauex_continuation_addon_risk_multiplier", 0.5) or 0.5))),
        2,
    )
    return result


def build_xauex_min_lot_canary_decision(
    *,
    signal: Dict[str, object],
    assurance: XauexAssuranceProfile,
    cash_risk_budget: float,
    minimum_executable_risk: float,
    canaries_used_today: int,
    config: Config,
) -> Dict[str, object]:
    """Decide whether a low-assurance infra-degraded signal may lift to min lot."""
    min_confidence = max(
        0.0,
        min(1.0, float(getattr(config, "xauex_min_lot_canary_min_confidence", 0.58) or 0.58)),
    )
    max_per_day = max(0, int(getattr(config, "xauex_min_lot_canary_max_per_day", 1) or 0))
    result: Dict[str, object] = {
        "allowed": False,
        "reason": "CANARY_NOT_EVALUATED",
        "allow_minimum_executable_risk_lift": False,
        "min_confidence": round(min_confidence, 2),
        "max_per_day": max_per_day,
        "canaries_used_today": max(0, int(canaries_used_today or 0)),
        "minimum_executable_risk": round(max(0.0, float(minimum_executable_risk or 0.0)), 2),
        "cash_risk_budget": round(max(0.0, float(cash_risk_budget or 0.0)), 2),
    }

    if not bool(getattr(config, "xauex_min_lot_canary_enabled", True)):
        result["reason"] = "CANARY_DISABLED"
        return result

    action = str(signal.get("action", "HOLD") or "HOLD").upper()
    if action not in {"BUY", "SELL"}:
        result["reason"] = "CANARY_NO_DIRECTIONAL_SIGNAL"
        return result

    confirm_status = str(signal.get("confirm_status", "") or "").upper()
    if confirm_status != "CONFIRMED":
        result["reason"] = "CANARY_NOT_CONFIRMED"
        return result

    confidence = _safe_signal_float(signal.get("confidence"), 0.0)
    if confidence < min_confidence:
        result["reason"] = "CANARY_CONFIDENCE_TOO_LOW"
        result["confidence"] = round(confidence, 2)
        return result

    validator_status = str(signal.get("validator_status", "") or "").strip().lower()
    consensus = str(signal.get("consensus_state", "") or "").strip().lower()
    packet = signal.get("decision_packet") if isinstance(signal.get("decision_packet"), dict) else {}
    freshness = packet.get("input_freshness") if isinstance(packet.get("input_freshness"), dict) else {}
    if bool(freshness.get("hard_blocker")):
        result["reason"] = "CANARY_INPUT_HARD_BLOCKER"
        return result
    snapshot_state = str(freshness.get("market_snapshot_state", "") or "").strip().lower()
    if snapshot_state == "blocked":
        result["reason"] = "CANARY_INPUT_BLOCKED"
        result["market_snapshot_state"] = snapshot_state
        return result

    if int(result["canaries_used_today"]) >= max_per_day:
        result["reason"] = "CANARY_DAILY_LIMIT_REACHED"
        return result

    if float(result["minimum_executable_risk"]) <= 0.0:
        result["reason"] = "CANARY_MIN_RISK_INVALID"
        return result
    if float(result["minimum_executable_risk"]) > float(result["cash_risk_budget"]):
        result["reason"] = "CANARY_MIN_RISK_EXCEEDS_BUDGET"
        return result

    assurance_allows = bool(getattr(assurance, "allow_trade", False))
    assurance_bucket = str(getattr(assurance, "bucket", "") or "").strip().lower()
    assurance_reason = str(getattr(assurance, "reason", "") or "").strip().upper()
    stale_context = _xauex_has_stale_or_warning_context(signal)
    if assurance_allows and assurance_bucket == "medium" and stale_context and "STALE_CONTEXT" in assurance_reason:
        result["stale_min_confidence"] = round(min_confidence, 2)
        if bool(signal.get("counter_signal")):
            result["reason"] = "CANARY_COUNTER_SIGNAL_STALE_CONTEXT"
            return result
        if validator_status != "reviewed":
            result["reason"] = "CANARY_STALE_CONTEXT_VALIDATOR_NOT_REVIEWED"
            result["validator_status"] = validator_status
            return result
        if consensus not in {"aligned", "confirmed"}:
            result["reason"] = "CANARY_STALE_CONTEXT_CONSENSUS_NOT_ALIGNED"
            result["consensus_state"] = consensus
            return result

        result["allowed"] = True
        result["reason"] = "MIN_LOT_CANARY_STALE_CONTEXT_CONFIRMED"
        result["allow_minimum_executable_risk_lift"] = True
        result["confidence"] = round(confidence, 2)
        result["validator_status"] = validator_status
        result["consensus_state"] = consensus
        result["market_snapshot_state"] = snapshot_state
        result["assurance_bucket"] = assurance_bucket
        result["assurance_reason"] = assurance_reason
        return result

    if not assurance_allows or assurance_bucket != "low":
        result["reason"] = "CANARY_NOT_LOW_ASSURANCE"
        return result

    if validator_status != "unavailable":
        result["reason"] = "CANARY_VALIDATOR_NOT_UNAVAILABLE"
        result["validator_status"] = validator_status
        return result

    if consensus not in {"unreviewed", ""}:
        result["reason"] = "CANARY_CONSENSUS_NOT_UNREVIEWED"
        result["consensus_state"] = consensus
        return result

    result["allowed"] = True
    result["reason"] = "MIN_LOT_CANARY_VALIDATOR_UNAVAILABLE"
    result["allow_minimum_executable_risk_lift"] = True
    result["confidence"] = round(confidence, 2)
    result["validator_status"] = validator_status
    result["consensus_state"] = consensus
    result["market_snapshot_state"] = snapshot_state
    return result


def _xauex_signal_input_freshness(signal: Dict[str, object]) -> Dict[str, object]:
    packet = signal.get("decision_packet") if isinstance(signal.get("decision_packet"), dict) else {}
    freshness = packet.get("input_freshness") if isinstance(packet.get("input_freshness"), dict) else {}
    return freshness


def _xauex_signal_price_features(signal: Dict[str, object]) -> Dict[str, object]:
    packet = signal.get("decision_packet") if isinstance(signal.get("decision_packet"), dict) else {}
    price_features = packet.get("price_features") if isinstance(packet.get("price_features"), dict) else {}
    return price_features


def _xauex_has_stale_or_warning_context(signal: Dict[str, object]) -> bool:
    freshness = _xauex_signal_input_freshness(signal)
    snapshot_state = str(freshness.get("market_snapshot_state", "") or "").strip().lower()
    if snapshot_state in {"warning", "stale"}:
        return True
    validator_summary = str(signal.get("validator_summary", "") or "").strip().lower()
    return "stale" in validator_summary or "freshness warning" in validator_summary


def _xauex_trade_direction_to_action(direction: object) -> str:
    text = str(direction or "").strip().upper()
    if text in {"LONG", "BUY"}:
        return "BUY"
    if text in {"SHORT", "SELL"}:
        return "SELL"
    return ""


def _xauex_event_london_date(value: object, now_utc: Optional[datetime]) -> str:
    if value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return london_trade_day(parsed.astimezone(timezone.utc))
        except ValueError:
            pass
    if now_utc is None:
        return ""
    return london_trade_day(now_utc.astimezone(timezone.utc))


def _xauex_is_same_london_day(value: object, now_utc: Optional[datetime]) -> bool:
    if now_utc is None:
        return True
    event_day = _xauex_event_london_date(value, now_utc)
    if not event_day:
        return True
    return event_day == london_trade_day(now_utc.astimezone(timezone.utc))


def _xauex_signal_run_matches_today(item: Dict[str, object], now_utc: Optional[datetime]) -> bool:
    if now_utc is None:
        return True
    london_date = london_trade_day(now_utc.astimezone(timezone.utc))
    item_date = str(item.get("date_london", "") or "")
    if item_date:
        return item_date == london_date
    return _xauex_is_same_london_day(item.get("recorded_at_utc"), now_utc)


def _xauex_prior_order_count(
    *,
    signal_runs_london: List[Dict[str, object]],
    action: str,
    now_utc: Optional[datetime],
    counter_signal: Optional[bool] = None,
) -> int:
    action = str(action or "").upper()
    count = 0
    for item in signal_runs_london:
        if not isinstance(item, dict) or not _xauex_signal_run_matches_today(item, now_utc):
            continue
        if str(item.get("reason") or item.get("action") or "").upper() != "ORDER_PLACED":
            continue
        if action and str(item.get("signal_action") or "").upper() != action:
            continue
        if counter_signal is not None and bool(item.get("counter_signal", False)) is not counter_signal:
            continue
        count += 1
    return count


def _xauex_has_same_day_loss(
    *,
    closed_trades_today: List[Dict[str, object]],
    action: str,
    now_utc: Optional[datetime],
    same_direction_only: bool,
) -> bool:
    action = str(action or "").upper()
    for trade in closed_trades_today:
        if not isinstance(trade, dict):
            continue
        if _safe_signal_float(trade.get("pnl"), 0.0) >= 0.0:
            continue
        if not _xauex_is_same_london_day(trade.get("close_time_utc"), now_utc):
            continue
        if same_direction_only and _xauex_trade_direction_to_action(trade.get("direction")) != action:
            continue
        return True
    return False


def build_xauex_entry_quality_decision(
    *,
    signal: Dict[str, object],
    assurance: XauexAssuranceProfile,
    config: Config,
    now_utc: Optional[datetime],
    closed_trades_today: List[Dict[str, object]],
    signal_runs_london: List[Dict[str, object]],
    pattern_evidence: XauexPatternEvidence | Dict[str, object] | None = None,
) -> Dict[str, object]:
    """Capital-preservation gate for patterns that caused repeated loss weeks."""
    action = str(signal.get("action", "HOLD") or "HOLD").upper()
    confidence = _safe_signal_float(signal.get("confidence"), 0.0)
    result: Dict[str, object] = {
        "allowed": True,
        "reason": "ENTRY_QUALITY_OK",
        "action": action,
        "confidence": round(confidence, 2),
        "policy_factors": [],
        "hard_block_score": 0,
        "risk_multiplier": 1.0,
    }
    if action not in {"BUY", "SELL"}:
        result["allowed"] = False
        result["reason"] = "NO_DIRECTIONAL_SIGNAL"
        return result
    if not assurance.allow_trade:
        result["allowed"] = False
        result["reason"] = assurance.reason
        return result

    factors: List[str] = []
    hard_block_score = 0

    def add_factor(name: str) -> None:
        nonlocal hard_block_score
        if name not in factors:
            factors.append(name)
            hard_block_score += int(_XAUEX_ENTRY_POLICY_FACTOR_WEIGHTS.get(name, 1))

    if isinstance(pattern_evidence, XauexPatternEvidence):
        result["pattern_evidence"] = pattern_evidence.to_dict()
        pattern_factor = pattern_evidence.factor
    elif isinstance(pattern_evidence, dict):
        result["pattern_evidence"] = dict(pattern_evidence)
        pattern_factor = str(pattern_evidence.get("factor") or "").upper()
    else:
        pattern_factor = ""
    if pattern_factor in _XAUEX_ENTRY_POLICY_FACTOR_WEIGHTS:
        add_factor(pattern_factor)

    trade_warnings = signal.get("trade_warnings")
    if isinstance(trade_warnings, list):
        for warning in trade_warnings:
            warning_name = str(warning or "").strip().upper()
            if warning_name in _XAUEX_ENTRY_POLICY_FACTOR_WEIGHTS:
                add_factor(warning_name)

    stale_min_confidence = max(
        0.0,
        min(1.0, float(getattr(config, "xauex_stale_context_min_confidence", 0.70) or 0.70)),
    )
    stale_context = _xauex_has_stale_or_warning_context(signal)
    if stale_context and confidence < stale_min_confidence:
        add_factor("STALE_CONTEXT_LOW_CONFIDENCE")
        result["stale_min_confidence"] = round(stale_min_confidence, 2)

    if bool(signal.get("counter_signal")):
        counter_limit = max(0, int(getattr(config, "xauex_counter_signal_daily_limit", 1) or 0))
        used = _xauex_prior_order_count(
            signal_runs_london=signal_runs_london,
            action="",
            now_utc=now_utc,
            counter_signal=True,
        )
        result["counter_signals_used_today"] = used
        result["counter_signal_daily_limit"] = counter_limit
        if used >= counter_limit:
            add_factor("COUNTER_SIGNAL_DAILY_LIMIT")
        if _xauex_has_same_day_loss(
            closed_trades_today=closed_trades_today,
            action=action,
            now_utc=now_utc,
            same_direction_only=False,
        ):
            add_factor("COUNTER_SIGNAL_AFTER_LOSS")
        if stale_context:
            add_factor("COUNTER_SIGNAL_STALE_CONTEXT")

    if bool(getattr(config, "xauex_same_direction_loss_cooldown", True)) and _xauex_has_same_day_loss(
        closed_trades_today=closed_trades_today,
        action=action,
        now_utc=now_utc,
        same_direction_only=True,
    ):
        add_factor("SAME_DIRECTION_LOSS_COOLDOWN")

    price_features = _xauex_signal_price_features(signal)
    range_position = str(price_features.get("range_position", "") or "").strip().upper()
    prior_same_direction_orders = _xauex_prior_order_count(
        signal_runs_london=signal_runs_london,
        action=action,
        now_utc=now_utc,
        counter_signal=None,
    )
    if action == "SELL" and range_position == "LOWER_THIRD" and prior_same_direction_orders > 0:
        add_factor("LOWER_THIRD_NO_CHASE")
        result["prior_same_direction_orders"] = prior_same_direction_orders

    result["policy_factors"] = list(factors)
    result["hard_block_score"] = hard_block_score
    if hard_block_score >= _XAUEX_ENTRY_HARD_BLOCK_SCORE:
        result["allowed"] = False
        result["reason"] = "HARD_BLOCKER"
        return result
    if hard_block_score > 0:
        result["reason"] = "ENTRY_QUALITY_WARNINGS"
        result["risk_multiplier"] = 0.5 if hard_block_score == 1 else 0.25

    return result


def build_xauex_initial_stop_distance(
    *,
    signal_stop: float,
    atr_stop: float,
    structure_stop: float,
    min_stop: float,
    max_stop: float,
) -> float:
    """Use the widest tradeable stop input and bound it into the configured range."""
    widest = max(float(signal_stop), float(atr_stop), float(structure_stop))
    bounded = max(float(min_stop), min(widest, float(max_stop)))
    return round(bounded, 2)


def build_xauex_protect_stop_price(
    *,
    direction: str,
    entry_price: float,
    initial_risk_distance: float,
    lock_r: float,
    min_buffer_usd: float,
) -> float:
    """Move protected stops to a locked-profit R level, with a breakeven buffer floor."""
    lock_distance = max(float(min_buffer_usd), float(initial_risk_distance) * max(0.0, float(lock_r)))
    if str(direction).upper() == "SHORT":
        return round(float(entry_price) - lock_distance, 2)
    return round(float(entry_price) + lock_distance, 2)


def build_xauex_take_profit_distance(
    *,
    signal_take_profit: float,
    stop_distance: float,
    assurance: XauexAssuranceProfile,
) -> float:
    """Use the stronger of the model TP and assurance-based RR target."""
    rr_target = float(stop_distance) * float(assurance.target_rr)
    return round(max(float(signal_take_profit), rr_target), 2)


def build_xauex_assurance_profile(signal: Dict[str, object], config: Config) -> XauexAssuranceProfile:
    """Convert signal confidence, validation, and freshness into live risk settings."""
    action = str(signal.get("action", "HOLD") or "HOLD").upper()
    if action not in {"BUY", "SELL"}:
        return _blocked_assurance("NO_DIRECTIONAL_SIGNAL")

    confidence = _safe_signal_float(signal.get("confidence"), 0.0)
    confidence = max(0.0, min(1.0, confidence))
    consensus = str(signal.get("consensus_state", "") or "").strip().lower()
    validator_status = str(signal.get("validator_status", "") or "").strip().lower()
    validator_summary = str(signal.get("validator_summary", "") or "").strip().lower()
    freshness = _xauex_signal_input_freshness(signal)

    if bool(freshness.get("hard_blocker")):
        return _blocked_assurance("HARD_BLOCKER")
    if bool(signal.get("validator_hard_blocker")):
        return _blocked_assurance("HARD_BLOCKER")

    stale_context = _xauex_has_stale_or_warning_context(signal)
    weak_validator = _validator_summary_is_weak(validator_summary)
    direct_validator_contradiction = _validator_summary_is_direct_contradiction(validator_summary)
    confirm_status = str(signal.get("confirm_status", "") or "").strip().upper()
    if (
        confidence < 0.55
        and consensus in {"disagreed", "conflicted", "blocked"}
        and direct_validator_contradiction
    ):
        return _blocked_assurance("HARD_BLOCKER")
    if (
        (confidence < 0.30 or confirm_status != "CONFIRMED")
        and confidence < 0.45
        and (consensus in {"disagreed", "conflicted", "blocked"} or weak_validator)
    ):
        return _blocked_assurance("HARD_BLOCKER")

    score = confidence
    if consensus in {"aligned", "confirmed"}:
        score += 0.08
    elif consensus in {"disagreed", "conflicted"}:
        score -= 0.16
    elif consensus == "blocked":
        score -= 0.35

    if validator_status in {"rejected", "blocked"}:
        score -= 0.25
    elif validator_status in {"unavailable", "skipped"}:
        score -= 0.05

    if weak_validator:
        score -= 0.10
    elif "well-supported" in validator_summary or "well supported" in validator_summary:
        score += 0.05

    if stale_context:
        score -= 0.05

    score = round(max(0.0, min(1.0, score)), 3)
    normal_protect_r = float(getattr(config, "xauex_session_protect_r", 0.85))
    high_protect_r = float(getattr(config, "xauex_session_high_confidence_protect_r", 1.00))
    normal_trail_r = float(getattr(config, "xauex_session_trail_r", 1.35))
    normal_lock_r = float(getattr(config, "xauex_session_protect_lock_r", 0.30))
    high_lock_r = float(getattr(config, "xauex_session_high_confidence_protect_lock_r", 0.25))

    loss_memory_gate = ""
    memory_summary = signal.get("memory_summary") if isinstance(signal.get("memory_summary"), dict) else {}
    net_pnl = float(memory_summary.get("net_pnl", 0.0) or 0.0)
    trade_count = int(memory_summary.get("trade_count", 0) or 0)
    memory_loss_threshold = float(getattr(config, "xauex_memory_loss_threshold_r", -2.0)) * 50.0
    if net_pnl < memory_loss_threshold and trade_count >= 4:
        loss_memory_gate = "_LOSS_MEMORY_TIGHTENED"

    if score >= 0.70 and not stale_context:
        target_rr = 2.5
        if loss_memory_gate:
            target_rr *= 0.5
        return XauexAssuranceProfile(
            bucket="high",
            score=score,
            allow_trade=True,
            reason="HIGH_ASSURANCE" + loss_memory_gate,
            risk_multiplier=1.5,
            target_rr=round(target_rr, 2),
            protect_r=round(high_protect_r, 2),
            trail_r=round(max(normal_trail_r + 0.15, high_protect_r + 0.2), 2),
            protect_lock_r=round(high_lock_r, 2),
        )
    if score >= 0.55:
        target_rr = 1.5 if stale_context else 2.0
        if loss_memory_gate:
            target_rr *= 0.5
        return XauexAssuranceProfile(
            bucket="medium",
            score=score,
            allow_trade=True,
            reason=("MEDIUM_ASSURANCE_STALE_CONTEXT" if stale_context else "MEDIUM_ASSURANCE") + loss_memory_gate,
            risk_multiplier=0.5 if stale_context else 1.0,
            target_rr=round(target_rr, 2),
            protect_r=round(normal_protect_r, 2),
            trail_r=round(max(normal_trail_r, normal_protect_r + 0.2), 2),
            protect_lock_r=round(normal_lock_r, 2),
        )
    if (
        confirm_status == "CONFIRMED"
        and score >= 0.30
        and confidence >= 0.35
        and consensus not in {"blocked"}
        and validator_status not in {"rejected", "blocked"}
    ):
        target_rr = 1.5
        if loss_memory_gate:
            target_rr *= 0.5
        low_protect_r = float(getattr(config, "xauex_session_low_confidence_protect_r", normal_protect_r))
        low_lock_r = float(getattr(config, "xauex_session_low_confidence_protect_lock_r", normal_lock_r))
        low_risk_multiplier = float(getattr(config, "xauex_low_confidence_lot_multiplier", 0.25))
        return XauexAssuranceProfile(
            bucket="low",
            score=score,
            allow_trade=True,
            reason="LOW_ASSURANCE_REDUCED_RISK" + loss_memory_gate,
            risk_multiplier=round(max(0.1, min(1.0, low_risk_multiplier)), 2),
            target_rr=round(target_rr, 2),
            protect_r=round(low_protect_r, 2),
            trail_r=round(max(normal_trail_r - 0.15, low_protect_r + 0.2), 2),
            protect_lock_r=round(low_lock_r, 2),
        )
    return _blocked_assurance("HARD_BLOCKER")


def _blocked_assurance(reason: str) -> XauexAssuranceProfile:
    return XauexAssuranceProfile(
        bucket="blocked",
        score=0.0,
        allow_trade=False,
        reason=reason,
        risk_multiplier=0.0,
        target_rr=0.0,
        protect_r=0.0,
        trail_r=0.0,
        protect_lock_r=0.0,
    )


def _safe_signal_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _validator_summary_is_weak(summary: str) -> bool:
    text = str(summary or "").lower()
    return any(
        marker in text
        for marker in (
            "not well supported",
            "not well-supported",
            "contradict",
            "weak",
            "unsupported",
        )
    )


def _validator_summary_is_direct_contradiction(summary: str) -> bool:
    text = str(summary or "").lower()
    return "contradict" in text


def advance_xauex_session_phase(
    state: Dict[str, object],
    *,
    current_price: float,
    protect_r: float,
    trail_r: float,
) -> Dict[str, object]:
    """Advance a persisted Oracle session phase based on price progress in R."""
    next_state = dict(state)
    phase = str(next_state.get("phase", "OBSERVE")).upper()
    direction = str(next_state.get("direction", "LONG")).upper()
    entry_price = float(next_state.get("entry_price", 0.0) or 0.0)
    initial_risk_distance = float(next_state.get("initial_risk_distance", 0.0) or 0.0)
    if initial_risk_distance <= 0:
        return next_state

    if direction == "SHORT":
        progress = entry_price - float(current_price)
    else:
        progress = float(current_price) - entry_price

    progress_r = progress / initial_risk_distance
    next_state["progress_r"] = round(progress_r, 4)

    if phase == "OBSERVE" and progress_r >= protect_r:
        next_state["phase"] = "PROTECT"
    if str(next_state.get("phase", phase)).upper() == "PROTECT" and progress_r >= trail_r:
        next_state["phase"] = "TRAIL"
    return next_state


def confirm_xauex_session_phase_transition(
    previous_state: Dict[str, object],
    candidate_state: Dict[str, object],
    *,
    unrealised_pnl: float,
    lot_size: float,
    contract_size: float,
) -> Dict[str, object]:
    """Require broker-side profit confirmation before tightening XAUEX stops."""
    confirmed_state = dict(candidate_state)
    previous_phase = str(previous_state.get("phase", "OBSERVE")).upper()
    candidate_phase = str(candidate_state.get("phase", previous_phase)).upper()
    if candidate_phase == previous_phase:
        return confirmed_state

    initial_risk_distance = float(
        previous_state.get(
            "initial_risk_distance",
            candidate_state.get("initial_risk_distance", 0.0),
        )
        or 0.0
    )
    if initial_risk_distance <= 0 or lot_size <= 0 or contract_size <= 0:
        confirmed_state["phase"] = previous_phase
        return confirmed_state

    protect_r = float(previous_state.get("protect_r", candidate_state.get("protect_r", 1.0)) or 1.0)
    trail_r = float(previous_state.get("trail_r", candidate_state.get("trail_r", max(protect_r + 0.2, 1.2))) or max(protect_r + 0.2, 1.2))

    if candidate_phase == "PROTECT":
        required_r = protect_r
    elif candidate_phase == "TRAIL":
        required_r = trail_r
    else:
        return confirmed_state

    required_pnl = initial_risk_distance * lot_size * contract_size * required_r
    if float(unrealised_pnl or 0.0) < required_pnl:
        confirmed_state["phase"] = previous_phase
    return confirmed_state


def should_manage_with_oracle_session_manager(position_payload: Dict[str, object]) -> bool:
    """Only XAUEX-owned positions should be managed by the XAUEX session manager."""
    return str(position_payload.get("owner", "") or "").lower() == "xauex"


def count_oracle_open_positions(positions: List[Dict[str, object]]) -> int:
    """Count only XAUEX-owned positions."""
    return sum(1 for position in positions if should_manage_with_oracle_session_manager(position))


def _position_owner(position: object) -> str:
    if isinstance(position, dict):
        return str(position.get("owner", "strategy") or "strategy").lower()
    return str(getattr(position, "owner", "strategy") or "strategy").lower()


def count_tradeable_open_positions(positions: List[object]) -> int:
    """Count only XAUEX-managed positions for XAUEX auto-entry limits."""
    return sum(1 for position in positions if _position_owner(position) == "xauex")


def manual_trade_global_block_reason(*, kill_switch_active: bool, auth_failure: bool) -> Optional[str]:
    """Manual trades remain independent from Oracle logic, but not from explicit global safety halts."""
    if kill_switch_active:
        return "KILL_SWITCH"
    if auth_failure:
        return "AUTH_FAILURE"
    return None


def _normalise_direction_label(value: object) -> str:
    text = str(value or "").upper()
    if text in {"BUY", "LONG"}:
        return "LONG"
    if text in {"SELL", "SHORT"}:
        return "SHORT"
    return text


def _float_close(left: object, right: object, tolerance: float = 0.05) -> bool:
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return False


def match_recovered_position_metadata(
    position: object,
    *,
    prior_positions: Dict[str, Dict[str, object]],
    prior_pending_market_orders: Dict[str, Dict[str, object]],
) -> Dict[str, object]:
    """Recover owner/session metadata for broker positions after a restart."""
    if isinstance(position, dict):
        position_id = str(position.get("position_id", "") or "")
        direction = _normalise_direction_label(position.get("direction"))
        volume = position.get("volume")
        stop_loss = position.get("stop_loss")
        take_profit = position.get("take_profit")
    else:
        position_id = str(getattr(position, "position_id", "") or "")
        direction = _normalise_direction_label(getattr(position, "direction", ""))
        volume = getattr(position, "volume", None)
        stop_loss = getattr(position, "stop_loss", None)
        take_profit = getattr(position, "take_profit", None)

    if position_id and position_id in prior_positions:
        prior = prior_positions[position_id]
        return {
            "owner": prior.get("owner", "strategy"),
            "metadata": dict(prior.get("metadata") or {}),
        }

    candidates: List[Dict[str, object]] = []
    for payload in prior_pending_market_orders.values():
        if _normalise_direction_label(payload.get("direction")) != direction:
            continue
        if not _float_close(payload.get("lot_size"), volume, tolerance=0.0001):
            continue
        if not _float_close(payload.get("stop_loss"), stop_loss):
            continue
        if not _float_close(payload.get("take_profit"), take_profit):
            continue
        candidates.append(payload)

    if not candidates:
        return {"owner": "strategy", "metadata": {}}

    candidates.sort(key=lambda item: str(item.get("created_at", "") or ""), reverse=True)
    winner = candidates[0]
    return {
        "owner": winner.get("owner", "strategy"),
        "metadata": dict(winner.get("metadata") or {}),
    }


def consume_manual_trade_command(
    path: Path,
    *,
    secret: str | None = None,
    seen_command_ids: set[str] | None = None,
    now_utc: datetime | None = None,
    journal_path: str | Path | None = None,
    replay_guard: CommandReplayGuard | None = None,
) -> ManualCommandConsumeResult:
    """Load, verify, and atomically consume a signed manual command envelope."""
    return consume_manual_command_file(
        path,
        secret=secret,
        seen_command_ids=seen_command_ids,
        now_utc=now_utc,
        journal_path=journal_path,
        replay_guard=replay_guard,
    )


def load_manual_trade_command(
    path: Path,
    *,
    secret: str | None = None,
    seen_command_ids: set[str] | None = None,
    now_utc: datetime | None = None,
    journal_path: str | Path | None = None,
    replay_guard: CommandReplayGuard | None = None,
) -> Optional[Dict[str, object]]:
    """Backward-compatible payload-only loader for signed manual commands."""
    return consume_manual_trade_command(
        path,
        secret=secret,
        seen_command_ids=seen_command_ids,
        now_utc=now_utc,
        journal_path=journal_path,
        replay_guard=replay_guard,
    ).payload


def validate_manual_trade_command(payload: Dict[str, object]) -> Tuple[bool, str]:
    """Validate a manual trade command payload before it reaches broker execution."""
    command = str(payload.get("command", "open") or "open").lower()
    if command == "close":
        position_id = str(payload.get("position_id", "") or "").strip()
        if not position_id:
            return False, "position_id is required for close commands"
        return True, ""

    if command != "open":
        return False, "command must be open or close"

    action = str(payload.get("action", "") or "").upper()
    if action not in {"BUY", "SELL"}:
        return False, "action must be BUY or SELL"

    try:
        lot_size = float(payload.get("lot_size", 0.0) or 0.0)
    except (TypeError, ValueError):
        return False, "lot_size must be numeric"
    if lot_size <= 0:
        return False, "lot_size must be positive"

    try:
        stop_loss = float(payload.get("stop_loss", 0.0) or 0.0)
        take_profit = float(payload.get("take_profit", 0.0) or 0.0)
    except (TypeError, ValueError):
        return False, "stop_loss and take_profit must be numeric"
    if stop_loss <= 0 or take_profit <= 0:
        return False, "stop_loss and take_profit are required"
    return True, ""


def validate_manual_trade_prices(payload: Dict[str, object], *, current_price: float) -> Tuple[bool, str]:
    """Validate that manual absolute-price SL/TP are on the correct side of market."""
    action = str(payload.get("action", "") or "").upper()
    stop_loss = float(payload.get("stop_loss", 0.0) or 0.0)
    take_profit = float(payload.get("take_profit", 0.0) or 0.0)
    if action == "BUY":
        if stop_loss >= current_price:
            return False, "BUY stop_loss must be below current price"
        if take_profit <= current_price:
            return False, "BUY take_profit must be above current price"
        return True, ""
    if action == "SELL":
        if stop_loss <= current_price:
            return False, "SELL stop_loss must be above current price"
        if take_profit >= current_price:
            return False, "SELL take_profit must be below current price"
        return True, ""
    return False, "action must be BUY or SELL"


class BotOrchestrator:
    """
    Coordinates all modules: startup, main event loop, graceful shutdown.
    The tick handler drives all logic; no internal timers or threads.
    """

    def __init__(self, config: Config):
        self.config = config
        self.running = False
        self.active_strategy_mode = config.strategy_mode
        self.shadow_strategy_mode = (
            config.shadow_strategy_mode if config.shadow_strategy_mode != "NONE" else None
        )
        self.execution_timeframe = self._strategy_timeframe(self.active_strategy_mode)

        self.api_client: Optional[ApiClient] = None
        self.level_manager: Optional[LevelManager] = None
        self._watchers: Dict[str, CandleWatcher] = {}
        self._watchers[self.execution_timeframe] = CandleWatcher(self.execution_timeframe)
        if self.shadow_strategy_mode:
            shadow_tf = self._strategy_timeframe(self.shadow_strategy_mode)
            self._watchers.setdefault(shadow_tf, CandleWatcher(shadow_tf))
        self.pattern_detector: Optional[PatternDetector] = None
        self.session_filter = SessionFilter()
        self.news_filter: Optional[NewsFilter] = None
        self.trend_filter = TrendFilter()
        self.ema_pullback_strategy = EMAPullbackH1Strategy(config, self.trend_filter)
        self.scalp_strategy = M5ScalpStrategy(config)
        self.macro_regime_loader = MacroRegimeLoader(
            config.macro_regime_path,
            config.macro_regime_max_age_minutes,
        )
        self.trade_policy_loader = TradePolicyLoader(
            config.trade_policy_path,
            config.trade_policy_max_age_minutes,
        )
        self._macro_regime: Optional[MacroRegime] = None
        self._trade_policy: Optional[TradePolicy] = None
        self.risk_state = RiskState()
        self.risk_gates: Optional[RiskGates] = None
        self.executor: Optional[Executor] = None
        self.state_writer: Optional[StateWriter] = None
        self.watchdog: Optional[Watchdog] = None
        self.health_check: Optional[HealthCheck] = None

        self.symbol_spec = None
        self.account: Dict = {}
        self.bot_status = "INITIALIZING"
        self.last_error: Optional[str] = None
        self.kill_switch_active = False
        self._last_xauex_signal_id_by_slot: dict[str, Optional[str]] = {}
        self._last_xauex_signal_seen_utc: dict[str, float] = {}
        self._last_xauex_gate_log_at: dict[str, float] = {}
        self._xauex_close_requested: dict[str, datetime] = {}
        self._manual_command_ids: set[str] = set()
        self._manual_replay_guard = CommandReplayGuard(self.config.xauex_manual_command_ledger_path)
        self._manual_trade_status: Dict[str, object] = {}
        self._latest_quote: Dict[str, object] = {}
        self._candidate_signal_history: deque = deque(maxlen=24)
        # Outcome analytics for blocked candidates live in the decision ledger
        # and gate-economics jobs (xauex/analyst/); only the raw counter stays.
        self._candidate_metrics: Dict[str, object] = {"total": 0}
        self.last_tick_time = time.monotonic()

        self._recent_h1_closes: deque = deque(maxlen=80)
        self._trade_entries_on_chart: List[Dict] = []
        self._last_signal: Optional[Dict] = None
        self._signal_history: deque = deque(maxlen=12)
        self._trend_snapshot: Optional[Dict] = None
        self._candle_index = 0
        self._recent_trade_levels: deque = deque(maxlen=20)
        self._shadow_last_signal: Optional[Dict] = None
        self._shadow_signal_history: deque = deque(maxlen=12)
        self._strategy_entries: Dict[str, deque] = {}
        self._strategy_trade_counts: Dict[str, int] = {}
        self._strategy_trade_dates: Dict[str, str] = {}
        self._strategy_data_status: Dict[str, Dict] = {}
        self._timeframe_candle_indices: Dict[str, int] = {timeframe: 0 for timeframe in self._watchers}
        for mode in filter(None, {self.active_strategy_mode, self.shadow_strategy_mode}):
            self._strategy_entries[mode] = deque(maxlen=20)
            self._strategy_trade_counts[mode] = 0
            self._strategy_trade_dates[mode] = self._today_utc()

    # ──────────────────────────────────────────────────────────────
    # Startup
    # ──────────────────────────────────────────────────────────────

    def _refresh_latest_quote_snapshot(
        self,
        *,
        mid: Optional[float] = None,
        timestamp: Optional[datetime] = None,
    ) -> None:
        bid = ask = None
        if self.api_client is not None:
            try:
                bid, ask = self.api_client.get_current_quote()
            except Exception:
                bid = ask = None
        if bid is None and ask is None and mid is None:
            return

        existing_mid = self._latest_quote.get("mid")
        if mid is None and bid is not None and ask is not None:
            mid = (float(bid) + float(ask)) / 2.0
        elif mid is None:
            mid = existing_mid if existing_mid is not None else None

        if timestamp is not None:
            updated_at_utc = timestamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            updated_at_utc = str(self._latest_quote.get("updated_at_utc") or "")
            if not updated_at_utc:
                updated_at_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._latest_quote = {
            "bid": round(bid, 2) if bid is not None else self._latest_quote.get("bid"),
            "ask": round(ask, 2) if ask is not None else self._latest_quote.get("ask"),
            "mid": round(float(mid), 2) if mid is not None else self._latest_quote.get("mid"),
            "updated_at_utc": updated_at_utc,
        }

    async def startup(self) -> None:
        """Run startup sequence. Exits process on any failure in steps 1-10."""
        logger.info("[STARTUP] XAUEX starting up.")

        # Step 1: Config already loaded and validated by caller.

        # Step 2: Token refresh check
        try:
            await self._check_token_refresh()
        except Exception as exc:
            logger.critical("[STARTUP] Token refresh failed: %s", exc)
            sys.exit(1)

        # Step 3+4: Connect and authenticate
        try:
            self.api_client = ApiClient(self.config)
            await self.api_client.connect()
        except Exception as exc:
            logger.critical("[STARTUP] API connection failed: %s", exc)
            sys.exit(1)

        # Step 5: Fetch account state
        try:
            account_data = await self.api_client.get_account()
            self.account = {
                "balance": account_data.balance,
                "equity": account_data.equity,
                "currency": account_data.currency,
                "open_pnl": account_data.open_pnl,
            }
            logger.info("[STARTUP] Account: %s %.2f", account_data.currency, account_data.balance)
        except Exception as exc:
            logger.critical("[STARTUP] Failed to fetch account: %s", exc)
            sys.exit(1)

        # Step 6: Symbol spec
        try:
            self.symbol_spec = await self.api_client.get_symbol_spec("XAUUSD")
            logger.info("[STARTUP] Symbol spec loaded: lot_size=%.0f", self.symbol_spec.lot_size)
        except Exception as exc:
            logger.critical("[STARTUP] Failed to fetch symbol spec: %s", exc)
            sys.exit(1)

        # Step 7+8: execution-timeframe bars and HTF levels
        try:
            self.level_manager = LevelManager(self.config, self.api_client)
            await self.level_manager.refresh()
            await self._prime_recent_context()
            logger.info("[STARTUP] HTF levels computed.")
        except Exception as exc:
            logger.critical("[STARTUP] Level manager init failed: %s", exc)
            sys.exit(1)

        # Restore persistent state before constructing its dependent objects.
        self.pattern_detector = PatternDetector(self.config)
        self.news_filter = NewsFilter(self.config)
        await self._restore_risk_state()

        # Initialize dependent modules.
        self.risk_gates = RiskGates(self.config, self.risk_state)
        self.state_writer = StateWriter(self.config)
        self.executor = Executor(
            config=self.config,
            api_client=self.api_client,
            level_manager=self.level_manager,
            risk_gates=self.risk_gates,
            state_writer=self.state_writer,
            event_journal_path=self.config.xauex_event_journal_path,
        )
        self.api_client.set_execution_callback(self._on_execution_event)

        wiring_error = self._risk_state_wiring_error()
        if wiring_error is not None:
            raise RuntimeError(wiring_error)

        # Step 9: Initialize risk period baselines.
        self.risk_gates.ensure_period_baselines(self.account.get("balance", 0.0))
        self._reset_xauex_trade_count_if_new_london_day(datetime.now(timezone.utc))

        # Step 10: Fetch economic calendar
        try:
            await self.news_filter.refresh_if_needed()
            if self.news_filter.feed_available:
                logger.info("[STARTUP] Economic calendar cached.")
            else:
                logger.warning("[STARTUP] News feed unavailable. Bot will stay up but block entries until refresh succeeds.")
        except Exception as exc:
            logger.warning("[STARTUP] Failed to refresh news calendar: %s", exc)

        self._macro_regime = self._load_macro_regime()
        self._trade_policy = self._load_trade_policy()

        # Step 11: Restore open positions from API
        try:
            restored_position_meta = self._load_prior_position_metadata()
            prior_pending_market_orders = self._load_prior_pending_market_orders()
            open_positions, pending_orders = await self.api_client.reconcile()
            self.executor.restore_pending_market_orders(prior_pending_market_orders, pending_orders)
            for pos in open_positions:
                prior = match_recovered_position_metadata(
                    pos,
                    prior_positions=restored_position_meta,
                    prior_pending_market_orders=prior_pending_market_orders,
                )
                owner = str(prior.get("owner", "strategy") or "strategy")
                metadata = dict(prior.get("metadata") or {})
                self.executor.position_manager.add_position(pos, owner=owner, metadata=metadata)
                logger.info("[STARTUP] Restored position %s %s owner=%s", pos.position_id, pos.direction, owner)
            self.risk_gates.set_open_position_count(
                count_tradeable_open_positions(self.executor.position_manager.get_open_positions())
            )
        except Exception as exc:
            logger.critical("[STARTUP] Failed to restore positions: %s", exc)
            sys.exit(1)

        # Write initial state
        await self.write_state()
        logger.info("[STARTUP] Initial state written.")

        # Subscribe to ticks
        await self.api_client.subscribe_ticks("XAUUSD", self.on_tick)
        logger.info("[STARTUP] Tick stream subscribed.")

        self.running = True
        self.bot_status = "RUNNING"

        # Background: poll kill switch, watchdog, health check
        asyncio.create_task(self._poll_kill_switch())

        if self.config.xauex_mode:
            asyncio.create_task(self._poll_xauex_signal())
            asyncio.create_task(self._monitor_xauex_positions())
            logger.info("[STARTUP] XAUEX signal mode: internal strategies disabled, polling for signals.")
        asyncio.create_task(self._poll_manual_trade_commands())

        self.watchdog = Watchdog(self)
        asyncio.create_task(self.watchdog.run())

        self.health_check = HealthCheck(
            self,
            host=getattr(self.config, "health_check_host", "127.0.0.1"),
            port=getattr(self.config, "health_check_port", 8051),
            watchdog=self.watchdog,
        )
        asyncio.create_task(self.health_check.run())
        asyncio.create_task(self._poll_account_snapshot())
        asyncio.create_task(self._poll_dashboard_state_refresh())
        await self.write_state()
        logger.info("[STARTUP] Bot online. Status: %s", self.bot_status)

    # ──────────────────────────────────────────────────────────────
    # Main event loop
    # ──────────────────────────────────────────────────────────────

    async def main_loop(self) -> None:
        """Keep the event loop alive — tick callbacks drive all logic."""
        while self.running:
            await asyncio.sleep(1)

    # ──────────────────────────────────────────────────────────────
    # Tick handler
    # ──────────────────────────────────────────────────────────────

    async def on_tick(self, price: float, timestamp: datetime) -> None:
        """Process an incoming price tick."""
        self.last_tick_time = time.monotonic()
        if self.health_check:
            self.health_check.record_tick()
        self._refresh_latest_quote_snapshot(mid=price, timestamp=timestamp)

        # Update trailing stops on every tick (independent of candle close)
        if self.executor and self.symbol_spec and self.executor.position_manager.count() > 0:
            await self.executor.update_trailing_stops(
                current_price=price,
                account_balance=self.account.get("balance", 0.0),
                symbol_spec=self.symbol_spec,
            )

        closed_timeframes: set[str] = set()
        for timeframe, watcher in self._watchers.items():
            if watcher.on_tick(timestamp):
                closed_timeframes.add(timeframe)
                self._timeframe_candle_indices[timeframe] += 1

        if self.execution_timeframe in closed_timeframes:
            self._candle_index = self._timeframe_candle_indices[self.execution_timeframe]
            await self._process_candle_close(price, timestamp)

        if self.shadow_strategy_mode:
            shadow_tf = self._strategy_timeframe(self.shadow_strategy_mode)
            if shadow_tf in closed_timeframes:
                await self._process_shadow_strategy_close(price, timestamp)

    async def _process_candle_close(self, price: float, timestamp: datetime) -> None:
        """Handle all logic triggered by the active strategy candle closing."""
        if self.config.xauex_mode:
            # In XAUEX signal mode, internal strategies are disabled.
            # Trades are placed by _poll_xauex_signal instead, but the active
            # strategy context still needs a fresh trend snapshot for confirm.
            await self._refresh_xauex_microstructure_context()
            await self._finalize_candle()
            return
        if self.active_strategy_mode == "EMA_PULLBACK_H1":
            await self._process_ema_pullback_candle_close(
                price=price,
                timestamp=timestamp,
                store="live",
                apply_risk_gates=True,
            )
            return
        if self.active_strategy_mode == "SCALP_V1":
            await self._process_scalp_v1_candle_close(
                price=price,
                timestamp=timestamp,
                store="live",
                apply_risk_gates=True,
            )
            return
        await self._process_legacy_candle_close(price, timestamp)

    async def _process_shadow_strategy_close(self, price: float, timestamp: datetime) -> None:
        """Evaluate the configured shadow strategy without placing live orders."""
        if not self.shadow_strategy_mode or self.shadow_strategy_mode == self.active_strategy_mode:
            return
        if self.shadow_strategy_mode == "EMA_PULLBACK_H1":
            await self._process_ema_pullback_candle_close(
                price=price,
                timestamp=timestamp,
                store="shadow",
                apply_risk_gates=False,
            )
            return
        if self.shadow_strategy_mode == "SCALP_V1":
            await self._process_scalp_v1_candle_close(
                price=price,
                timestamp=timestamp,
                store="shadow",
                apply_risk_gates=False,
            )

    async def _refresh_xauex_microstructure_context(self) -> None:
        """Refresh the active strategy trend context without enabling strategy trading."""
        mode = self.active_strategy_mode
        timeframe = self._strategy_timeframe(mode)
        bars, signal_index = await self._fetch_execution_bars(
            timeframe,
            count=self._strategy_execution_lookback(mode),
        )
        if bars is None or signal_index is None:
            self._update_strategy_data_status(
                mode,
                execution_timeframe=timeframe,
                execution_bars=bars,
                signal_index=signal_index,
            )
            return

        self._recent_h1_closes = deque([bar["close"] for bar in bars[-80:]], maxlen=80)

        if mode == "SCALP_V1":
            daily_closes = await self._fetch_daily_closes()
            h1_closes = self._aggregate_h1_closes_from_m5(bars)
            self._macro_regime = self._load_macro_regime()
            self._trade_policy = self._load_trade_policy()
            self._update_strategy_data_status(
                mode,
                execution_timeframe=timeframe,
                execution_bars=bars,
                signal_index=signal_index,
                daily_closes=daily_closes,
                h1_closes=h1_closes,
            )
            if daily_closes is not None and h1_closes is not None:
                trade_policy = (
                    self._trade_policy.to_state_dict()
                    if self._trade_policy is not None
                    else None
                )
                self._trend_snapshot = self.scalp_strategy.state_trend(
                    daily_closes=daily_closes,
                    h1_closes=h1_closes,
                    macro_regime=self._macro_regime,
                    trade_policy=trade_policy,
                )
            else:
                self._trend_snapshot = {
                    "alignment": "UNKNOWN",
                    "reason": "EMA_DATA_UNAVAILABLE",
                    "execution_timeframe": timeframe,
                }
            return

        execution_closes = [bar["close"] for bar in bars[: signal_index + 1]]
        daily_closes = await self._refresh_trend_snapshot(execution_closes, timeframe)
        self._update_strategy_data_status(
            mode,
            execution_timeframe=timeframe,
            execution_bars=bars,
            signal_index=signal_index,
            daily_closes=daily_closes,
        )

    async def _process_legacy_candle_close(self, price: float, timestamp: datetime) -> None:
        """Run the existing level-reaction strategy on the configured execution timeframe."""
        logger.debug("[TICK] %s legacy candle closed at %s", self.execution_timeframe, timestamp)
        bars, signal_index = await self._fetch_execution_bars(
            self.execution_timeframe,
            count=self._strategy_execution_lookback(self.active_strategy_mode),
        )
        if bars is None or signal_index is None:
            self._update_strategy_data_status(
                self.active_strategy_mode,
                execution_timeframe=self.execution_timeframe,
                execution_bars=bars,
                signal_index=signal_index,
            )
            await self.write_state()
            return

        self._recent_h1_closes = deque([bar["close"] for bar in bars[-80:]], maxlen=80)

        try:
            await self.level_manager.refresh_if_needed()
        except Exception as exc:
            logger.error("[TICK] Level refresh error: %s", exc)

        gate_result = await self._environment_gate(apply_risk_gates=True)
        if gate_result is not None:
            self._record_signal(None, None, gate_result, "SKIP", strategy_mode=self.active_strategy_mode)
            await self._finalize_candle()
            return

        prev_bar = bars[signal_index - 1]
        signal_bar = bars[signal_index]
        from bot.patterns.detector import Candle

        prev_candle = Candle(
            open=prev_bar["open"], high=prev_bar["high"],
            low=prev_bar["low"], close=prev_bar["close"],
            open_time=prev_bar["open_time"],
        )
        signal_candle = Candle(
            open=signal_bar["open"], high=signal_bar["high"],
            low=signal_bar["low"], close=signal_bar["close"],
            open_time=signal_bar["open_time"],
        )

        execution_closes = [bar["close"] for bar in bars[: signal_index + 1]]
        daily_closes = await self._refresh_trend_snapshot(execution_closes, self.execution_timeframe)
        self._update_strategy_data_status(
            self.active_strategy_mode,
            execution_timeframe=self.execution_timeframe,
            execution_bars=bars,
            signal_index=signal_index,
            daily_closes=daily_closes,
        )

        level, pattern_result = self._match_level_and_pattern(prev_candle, signal_candle)
        if level is None:
            self._record_signal(None, None, "NO_LEVEL", "SKIP", strategy_mode=self.active_strategy_mode)
            await self._finalize_candle()
            return

        try:
            current_spread = self.api_client.get_current_spread()
        except Exception:
            current_spread = 0.0

        if pattern_result is None or pattern_result.pattern == PatternType.NONE:
            self._record_signal(
                None,
                level,
                "NO_PATTERN",
                "SKIP",
                entry_source="HTF_LEVEL",
                strategy_mode=self.active_strategy_mode,
            )
            await self._finalize_candle()
            return

        if daily_closes is None:
            self._record_signal(
                pattern_result.pattern,
                level,
                "EMA_DATA_UNAVAILABLE",
                "SKIP",
                entry_source="HTF_LEVEL",
                strategy_mode=self.active_strategy_mode,
            )
            await self._finalize_candle()
            return

        if pattern_result.pattern == PatternType.INSIDE_BAR:
            direction, trend_reason, _ = self.trend_filter.aligned_bias(
                daily_closes=daily_closes,
                execution_closes=execution_closes,
            )
            if direction is None:
                self._record_signal(
                    pattern_result.pattern,
                    level,
                    trend_reason,
                    "SKIP",
                    entry_source="HTF_LEVEL",
                    strategy_mode=self.active_strategy_mode,
                )
                await self._finalize_candle()
                return
        else:
            direction = pattern_result.direction
            trend_ok, trend_reason, _ = self.trend_filter.evaluate(
                direction=direction,
                daily_closes=daily_closes,
                execution_closes=execution_closes,
            )
            if not trend_ok:
                self._record_signal(
                    pattern_result.pattern,
                    level,
                    trend_reason,
                    "SKIP",
                    entry_source="HTF_LEVEL",
                    strategy_mode=self.active_strategy_mode,
                )
                await self._finalize_candle()
                return

        cooldown_reason = self._same_level_cooldown_reason(level, direction)
        if cooldown_reason is not None:
            self._record_signal(
                pattern_result.pattern,
                level,
                cooldown_reason,
                "SKIP",
                entry_source="HTF_LEVEL",
                strategy_mode=self.active_strategy_mode,
            )
            await self._finalize_candle()
            return

        sl_price = level - self.config.sl_offset_dollars if direction > 0 else level + self.config.sl_offset_dollars
        lot = calculate_lot_size(
            account_balance=self.account.get("balance", 0.0),
            entry_price=level,
            stop_loss_price=sl_price,
            symbol_spec=self.symbol_spec,
            current_spread_usd=current_spread,
            config=self.config,
        )
        if lot is None:
            self._record_signal(
                pattern_result.pattern,
                level,
                "LOT_SKIP",
                "SKIP",
                entry_source="HTF_LEVEL",
                strategy_mode=self.active_strategy_mode,
            )
            await self._finalize_candle()
            return

        tp_price = self.level_manager.next_level_from(level, direction)
        if tp_price is None:
            sl_dist = abs(level - sl_price)
            tp_price = level + direction * 2 * sl_dist

        action = "SKIP"
        if pattern_result.pattern == PatternType.INSIDE_BAR:
            buy_id, sell_id = await self.executor.place_inside_bar_orders(
                mother_bar_high=pattern_result.mother_bar_high,
                mother_bar_low=pattern_result.mother_bar_low,
                lot_size=lot,
                level=level,
                current_candle_index=self._candle_index,
                allowed_direction=direction,
            )
            if buy_id or sell_id:
                action = "EXECUTED"
                self._record_trade_level(level, direction)
            else:
                action = "FAILED"
        else:
            pos_id = await self.executor.place_market_order(
                direction=direction,
                lot_size=lot,
                stop_loss_price=sl_price,
                take_profit_price=tp_price,
                pattern=pattern_result.pattern,
                level=level,
            )
            if pos_id:
                action = "EXECUTED"
                self._record_trade_level(level, direction)
            else:
                action = "FAILED"

            if pos_id and len(self._recent_h1_closes) > 0:
                self._append_trade_entry_on_chart(
                    None,
                    "LONG" if direction > 0 else "SHORT",
                    level,
                    "strategy",
                )

        self._record_signal(
            pattern_result.pattern,
            level,
            "OK",
            action,
            entry_source="HTF_LEVEL",
            strategy_mode=self.active_strategy_mode,
        )
        await self._finalize_candle()

    async def _process_ema_pullback_candle_close(
        self,
        *,
        price: float,
        timestamp: datetime,
        store: str,
        apply_risk_gates: bool,
    ) -> None:
        """Run the H1 EMA-pullback strategy in live or shadow mode."""
        timeframe = self._strategy_timeframe("EMA_PULLBACK_H1")
        logger.debug("[TICK] %s EMA pullback candle closed at %s (%s)", timeframe, timestamp, store)

        bars, signal_index = await self._fetch_execution_bars(
            timeframe,
            count=self._strategy_execution_lookback("EMA_PULLBACK_H1"),
        )
        if bars is None or signal_index is None:
            self._update_strategy_data_status(
                "EMA_PULLBACK_H1",
                execution_timeframe=timeframe,
                execution_bars=bars,
                signal_index=signal_index,
            )
            if store == "shadow":
                await self.write_state()
            else:
                await self._finalize_candle()
            return

        if store == "live":
            self._recent_h1_closes = deque([bar["close"] for bar in bars[-80:]], maxlen=80)

        gate_result = await self._environment_gate(apply_risk_gates=apply_risk_gates)
        if gate_result is not None:
            self._record_signal(
                None,
                None,
                gate_result,
                "SKIP" if store == "live" else "SHADOW_SKIP",
                strategy_mode="EMA_PULLBACK_H1",
                setup_stage="ENVIRONMENT",
                store=store,
            )
            if store == "shadow":
                await self.write_state()
            else:
                await self._finalize_candle()
            return

        daily_closes = await self._fetch_daily_closes()
        self._update_strategy_data_status(
            "EMA_PULLBACK_H1",
            execution_timeframe=timeframe,
            execution_bars=bars,
            signal_index=signal_index,
            daily_closes=daily_closes,
        )
        decision = self.ema_pullback_strategy.evaluate(
            bars=bars,
            signal_index=signal_index,
            daily_closes=daily_closes,
            next_open_price=price,
            current_spread=self._safe_current_spread(),
        )

        execution_closes = [bar["close"] for bar in bars[: signal_index + 1]]
        decision_snapshot = self._trend_state(daily_closes, execution_closes, timeframe)
        if store == "live":
            self._trend_snapshot = decision_snapshot

        action = "SKIP" if store == "live" else "SHADOW_SKIP"
        if decision.is_ready:
            strategy_reason = self._strategy_trade_limit_reason(
                "EMA_PULLBACK_H1",
                decision.reference_level,
                decision.direction,
            )
            if strategy_reason is not None:
                decision.gate_result = strategy_reason
            else:
                if store == "shadow":
                    self._record_strategy_entry("EMA_PULLBACK_H1", decision.reference_level, decision.direction)
                    action = "SHADOW_READY"
                else:
                    lot = calculate_lot_size(
                        account_balance=self.account.get("balance", 0.0),
                        entry_price=decision.entry_price,
                        stop_loss_price=decision.stop_loss_price,
                        symbol_spec=self.symbol_spec,
                        current_spread_usd=self._safe_current_spread(),
                        config=self.config,
                    )
                    if lot is None:
                        decision.gate_result = "LOT_SKIP"
                    else:
                        pos_id = await self.executor.place_market_order(
                            direction=decision.direction,
                            lot_size=lot,
                            stop_loss_price=decision.stop_loss_price,
                            take_profit_price=decision.take_profit_price,
                            pattern=decision.pattern or PatternType.NONE,
                            level=decision.reference_level or decision.entry_price,
                        )
                        if pos_id:
                            self._record_strategy_entry("EMA_PULLBACK_H1", decision.reference_level, decision.direction)
                            self._append_trade_entry_on_chart(
                                None,
                                "LONG" if decision.direction > 0 else "SHORT",
                                decision.entry_price,
                                "strategy",
                            )
                            action = "EXECUTED"
                        else:
                            action = "FAILED"

        self._record_signal(
            decision.pattern,
            decision.reference_level,
            decision.gate_result,
            action,
            entry_source=decision.entry_source,
            strategy_mode="EMA_PULLBACK_H1",
            setup_stage=decision.setup_stage,
            details=decision.metadata,
            trend_snapshot=decision_snapshot,
            store=store,
        )
        if store == "shadow":
            await self.write_state()
        else:
            await self._finalize_candle()

    async def _process_scalp_v1_candle_close(
        self,
        *,
        price: float,
        timestamp: datetime,
        store: str,
        apply_risk_gates: bool,
    ) -> None:
        """Run the aggressive M5 scalper in live or shadow mode."""
        timeframe = self._strategy_timeframe("SCALP_V1")
        logger.debug("[TICK] %s scalp candle closed at %s (%s)", timeframe, timestamp, store)

        bars, signal_index = await self._fetch_execution_bars(
            timeframe,
            count=self._strategy_execution_lookback("SCALP_V1"),
        )
        if bars is None or signal_index is None:
            self._update_strategy_data_status(
                "SCALP_V1",
                execution_timeframe=timeframe,
                execution_bars=bars,
                signal_index=signal_index,
            )
            if store == "shadow":
                await self.write_state()
            else:
                await self._finalize_candle()
            return

        if store == "live":
            self._recent_h1_closes = deque([bar["close"] for bar in bars[-80:]], maxlen=80)

        gate_result = await self._environment_gate(apply_risk_gates=apply_risk_gates)
        if gate_result is not None:
            self._record_signal(
                None,
                None,
                gate_result,
                "SKIP" if store == "live" else "SHADOW_SKIP",
                strategy_mode="SCALP_V1",
                setup_stage="ENVIRONMENT",
                store=store,
            )
            if store == "shadow":
                await self.write_state()
            else:
                await self._finalize_candle()
            return

        daily_closes = await self._fetch_daily_closes()
        h1_closes = self._aggregate_h1_closes_from_m5(bars)
        macro_regime = self._load_macro_regime()
        trade_policy = self._load_trade_policy()
        self._update_strategy_data_status(
            "SCALP_V1",
            execution_timeframe=timeframe,
            execution_bars=bars,
            signal_index=signal_index,
            daily_closes=daily_closes,
            h1_closes=h1_closes,
        )
        decision = self.scalp_strategy.evaluate(
            m5_bars=bars,
            signal_index=signal_index,
            daily_closes=daily_closes or [],
            h1_closes=h1_closes or [],
            next_open_price=price,
            current_spread=self._safe_current_spread(),
            macro_regime=macro_regime,
            trade_policy=trade_policy.to_state_dict() if trade_policy is not None else None,
        )
        if store == "live":
            self._macro_regime = macro_regime
            self._trade_policy = trade_policy

        if daily_closes is not None and h1_closes is not None:
            decision_snapshot = self.scalp_strategy.state_trend(
                daily_closes=daily_closes,
                h1_closes=h1_closes,
                macro_regime=macro_regime,
                trade_policy=trade_policy.to_state_dict() if trade_policy is not None else None,
            )
        else:
            decision_snapshot = {"alignment": "UNKNOWN", "reason": "EMA_DATA_UNAVAILABLE", "execution_timeframe": timeframe}
        if store == "live":
            self._trend_snapshot = decision_snapshot

        action = "SKIP" if store == "live" else "SHADOW_SKIP"
        if decision.is_ready:
            strategy_reason = self._strategy_trade_limit_reason(
                "SCALP_V1",
                decision.reference_level,
                decision.direction,
            )
            if strategy_reason is not None:
                decision.gate_result = strategy_reason
            else:
                macro_ok, macro_reason = self.macro_regime_loader.gate_direction(
                    macro_regime,
                    direction=decision.direction,
                    now_utc=datetime.now(timezone.utc),
                    confidence_threshold=self.config.macro_regime_confidence_threshold,
                )
                if not macro_ok:
                    decision.gate_result = macro_reason or "MACRO_DIRECTION_BLOCK"
                elif store == "shadow":
                    self._record_strategy_entry("SCALP_V1", decision.reference_level, decision.direction)
                    action = "SHADOW_READY"
                else:
                    lot = calculate_lot_size(
                        account_balance=self.account.get("balance", 0.0),
                        entry_price=decision.entry_price,
                        stop_loss_price=decision.stop_loss_price,
                        symbol_spec=self.symbol_spec,
                        current_spread_usd=self._safe_current_spread(),
                        config=self.config,
                        min_sl_distance=self.config.scalp_sl_min_dollars,
                        max_sl_distance=self.config.scalp_sl_max_dollars,
                    )
                    if lot is None:
                        decision.gate_result = "LOT_SKIP"
                    else:
                        pos_id = await self.executor.place_market_order(
                            direction=decision.direction,
                            lot_size=lot,
                            stop_loss_price=decision.stop_loss_price,
                            take_profit_price=decision.take_profit_price,
                            pattern=decision.pattern or PatternType.NONE,
                            level=decision.reference_level or decision.entry_price,
                        )
                        if pos_id:
                            self._record_strategy_entry("SCALP_V1", decision.reference_level, decision.direction)
                            self._append_trade_entry_on_chart(
                                None,
                                "LONG" if decision.direction > 0 else "SHORT",
                                decision.entry_price,
                                "strategy",
                            )
                            action = "EXECUTED"
                        else:
                            action = "FAILED"

        self._record_signal(
            decision.pattern,
            decision.reference_level,
            decision.gate_result,
            action,
            entry_source=decision.entry_source,
            strategy_mode="SCALP_V1",
            setup_stage=decision.setup_stage,
            details=decision.metadata,
            trend_snapshot=decision_snapshot,
            store=store,
        )
        if store == "shadow":
            await self.write_state()
        else:
            await self._finalize_candle()

    async def _finalize_candle(self) -> None:
        """Called at end of every execution-timeframe close cycle."""
        self.risk_gates.set_open_position_count(
            count_tradeable_open_positions(self.executor.position_manager.get_open_positions())
        )
        await self.executor.check_pending_inside_bar_orders(self._candle_index)
        await self.write_state()

    def _make_signal_record(
        self,
        pattern,
        level,
        gate_result,
        action,
        entry_source: Optional[str] = None,
        strategy_mode: Optional[str] = None,
        setup_stage: Optional[str] = None,
        details: Optional[Dict] = None,
        trend_snapshot: Optional[Dict] = None,
    ) -> Dict:
        record = {
            "time_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pattern": pattern.name if pattern else None,
            "level_checked": level,
            "gate_result": gate_result,
            "action": action,
        }
        if entry_source is not None:
            record["entry_source"] = entry_source
        if strategy_mode is not None:
            record["strategy_mode"] = strategy_mode
        if setup_stage is not None:
            record["setup_stage"] = setup_stage
        if details:
            record["details"] = details
        if trend_snapshot is not None:
            record["trend"] = trend_snapshot
        elif self._trend_snapshot is not None:
            record["trend"] = self._trend_snapshot
        return record

    def _record_signal(
        self,
        pattern,
        level,
        gate_result,
        action,
        entry_source: Optional[str] = None,
        strategy_mode: Optional[str] = None,
        setup_stage: Optional[str] = None,
        details: Optional[Dict] = None,
        trend_snapshot: Optional[Dict] = None,
        store: str = "live",
    ) -> None:
        record = self._make_signal_record(
            pattern,
            level,
            gate_result,
            action,
            entry_source=entry_source,
            strategy_mode=strategy_mode,
            setup_stage=setup_stage,
            details=details,
            trend_snapshot=trend_snapshot,
        )
        if store == "shadow":
            self._shadow_last_signal = record
            self._shadow_signal_history.appendleft(record)
        else:
            self._last_signal = record
            self._signal_history.appendleft(record)
        logger.info(
            "[SIGNAL][%s] %s %s | pattern=%s level=%s trend=%s",
            store.upper(),
            action,
            gate_result,
            record["pattern"] or "NONE",
            f"{level:.2f}" if isinstance(level, (int, float)) else "NONE",
            record.get("trend", {}).get("alignment", "UNKNOWN"),
        )

    async def _prime_recent_context(self) -> None:
        """Seed chart/indicator context from recent execution-timeframe history at startup."""
        bar_count = self._strategy_execution_lookback(self.active_strategy_mode)
        try:
            bars = await self.api_client.get_trendbar(self.execution_timeframe, bar_count)
        except Exception as exc:
            logger.warning("[STARTUP] Failed to seed %s context: %s", self.execution_timeframe, exc)
            self._update_strategy_data_status(
                self.active_strategy_mode,
                execution_timeframe=self.execution_timeframe,
                execution_bars=None,
                signal_index=None,
            )
            return

        self._recent_h1_closes = deque(
            [bar["close"] for bar in bars[-80:]],
            maxlen=80,
        )
        if self.active_strategy_mode == "SCALP_V1":
            daily_closes = await self._fetch_daily_closes()
            h1_closes = self._aggregate_h1_closes_from_m5(bars)
            self._macro_regime = self._load_macro_regime()
            self._trade_policy = self._load_trade_policy()
            self._update_strategy_data_status(
                self.active_strategy_mode,
                execution_timeframe=self.execution_timeframe,
                execution_bars=bars,
                signal_index=len(bars) - 2 if len(bars) >= 2 else None,
                daily_closes=daily_closes,
                h1_closes=h1_closes,
            )
            if daily_closes is not None and h1_closes is not None:
                self._trend_snapshot = self.scalp_strategy.state_trend(
                    daily_closes=daily_closes,
                    h1_closes=h1_closes,
                    macro_regime=self._macro_regime,
                )
            else:
                self._trend_snapshot = {
                    "alignment": "UNKNOWN",
                    "reason": "EMA_DATA_UNAVAILABLE",
                    "execution_timeframe": self.execution_timeframe,
                }
            return

        execution_closes = [bar["close"] for bar in bars]
        daily_closes = await self._refresh_trend_snapshot(execution_closes, self.execution_timeframe)
        self._update_strategy_data_status(
            self.active_strategy_mode,
            execution_timeframe=self.execution_timeframe,
            execution_bars=bars,
            signal_index=len(bars) - 2 if len(bars) >= 2 else None,
            daily_closes=daily_closes,
        )

    async def _fetch_daily_closes(self) -> Optional[List[float]]:
        """Fetch closed D1 bars for bias calculation."""
        try:
            daily_bars = await self.api_client.get_trendbar("D1", _DAILY_BAR_LOOKBACK)
        except Exception as exc:
            logger.error("[TREND] Failed to fetch D1 bars: %s", exc)
            return None
        return [bar["close"] for bar in daily_bars]

    async def _fetch_h1_closes(self) -> Optional[List[float]]:
        """Fetch closed H1 bars for higher-timeframe confirmation."""
        last_exc = None
        for attempt in range(2):
            try:
                h1_bars = await self.api_client.get_trendbar("H1", 260)
                return [bar["close"] for bar in h1_bars]
            except Exception as exc:
                last_exc = exc
                if attempt == 0:
                    await asyncio.sleep(0.5)
                    continue
        logger.error("[TREND] Failed to fetch H1 bars: %s", last_exc)
        return None

    async def _xauex_pattern_evidence(
        self,
        *,
        direction: int,
        now_utc: datetime,
    ) -> XauexPatternEvidence:
        """Evaluate active-timeframe closed bars as weighted entry evidence."""
        from bot.patterns.detector import Candle

        timeframe = str(self.execution_timeframe or "").upper()

        def unavailable(detail: str) -> XauexPatternEvidence:
            return XauexPatternEvidence(
                factor="PATTERN_DATA_UNAVAILABLE",
                weight=_XAUEX_ENTRY_POLICY_FACTOR_WEIGHTS["PATTERN_DATA_UNAVAILABLE"],
                timeframe=timeframe,
                detail=detail,
            )

        if self.pattern_detector is None or self.level_manager is None:
            return unavailable("DETECTOR_OR_LEVELS_UNAVAILABLE")

        bars = await self._fetch_recent_execution_ohlc_bars(timeframe=timeframe, count=4)
        if not bars:
            return unavailable("EXECUTION_BARS_UNAVAILABLE")
        selected = select_closed_execution_pattern_bars(
            bars,
            now_utc=now_utc,
            timeframe=timeframe,
        )
        if selected is None:
            return unavailable("CLOSED_EXECUTION_BARS_UNAVAILABLE")
        prev_bar, signal_bar = selected
        try:
            prev_open_time = self._bar_open_time_utc(prev_bar)
            signal_open_time = self._bar_open_time_utc(signal_bar)
            if prev_open_time is None or signal_open_time is None:
                return unavailable("EXECUTION_BAR_TIME_MALFORMED")
            prev_candle = Candle(
                open=float(prev_bar["open"]),
                high=float(prev_bar["high"]),
                low=float(prev_bar["low"]),
                close=float(prev_bar["close"]),
                open_time=prev_open_time,
            )
            signal_candle = Candle(
                open=float(signal_bar["open"]),
                high=float(signal_bar["high"]),
                low=float(signal_bar["low"]),
                close=float(signal_bar["close"]),
                open_time=signal_open_time,
            )
        except (KeyError, TypeError, ValueError):
            return unavailable("EXECUTION_BAR_MALFORMED")

        range_low = min(prev_candle.low, signal_candle.low)
        range_high = max(prev_candle.high, signal_candle.high)
        candidates = self.level_manager.levels_near_range(range_low, range_high)
        if not candidates:
            candidates = self.level_manager.all_levels()
        if not candidates:
            return unavailable("NO_HTF_LEVELS")

        pattern, level, matched, reason = xauex_pattern_check(
            pattern_detector=self.pattern_detector,
            prev_candle=prev_candle,
            signal_candle=signal_candle,
            candidate_levels=candidates,
            direction=direction,
        )
        common = {
            "pattern": pattern,
            "level": level,
            "timeframe": timeframe,
            "previous_open_time_utc": prev_open_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "signal_open_time_utc": signal_open_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "detail": reason,
        }
        if matched:
            return XauexPatternEvidence(factor="PATTERN_MATCH", weight=0, **common)
        if reason == "DIRECTION_MISMATCH":
            return XauexPatternEvidence(
                factor="PATTERN_DIRECTION_MISMATCH",
                weight=_XAUEX_ENTRY_POLICY_FACTOR_WEIGHTS["PATTERN_DIRECTION_MISMATCH"],
                **common,
            )
        return XauexPatternEvidence(
            factor="PATTERN_MISSING",
            weight=_XAUEX_ENTRY_POLICY_FACTOR_WEIGHTS["PATTERN_MISSING"],
            **common,
        )

    @staticmethod
    def _bar_open_time_utc(bar: Dict[str, object]) -> Optional[datetime]:
        raw_open_time = bar.get("open_time")
        if isinstance(raw_open_time, datetime):
            open_time = raw_open_time
        elif isinstance(raw_open_time, str):
            try:
                open_time = datetime.fromisoformat(raw_open_time.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None
        if open_time.tzinfo is None:
            return open_time.replace(tzinfo=timezone.utc)
        return open_time.astimezone(timezone.utc)

    async def _fetch_recent_execution_ohlc_bars(
        self,
        *,
        timeframe: str,
        count: int = 4,
    ) -> Optional[List[Dict[str, object]]]:
        """Fetch raw active-timeframe OHLC bars for closed-bar pattern evidence."""
        last_exc = None
        for attempt in range(2):
            try:
                bars = await self.api_client.get_trendbar(timeframe, max(2, int(count)))
                return list(bars or [])
            except Exception as exc:
                last_exc = exc
                if attempt == 0:
                    await asyncio.sleep(0.5)
                    continue
        logger.error("[XAUEX] Failed to fetch %s OHLC bars: %s", timeframe, last_exc)
        return None

    def _load_macro_regime(self) -> Optional[MacroRegime]:
        return self.macro_regime_loader.load(datetime.now(timezone.utc))

    def _load_trade_policy(self) -> Optional[TradePolicy]:
        return self.trade_policy_loader.load(datetime.now(timezone.utc))

    async def _refresh_trend_snapshot(
        self,
        execution_closes: List[float],
        execution_timeframe: str,
    ) -> Optional[List[float]]:
        """Fetch D1 closes and refresh the active trend snapshot."""
        daily_closes = await self._fetch_daily_closes()
        if daily_closes is None:
            self._trend_snapshot = {
                "alignment": "UNKNOWN",
                "reason": "EMA_DATA_UNAVAILABLE",
                "execution_timeframe": execution_timeframe,
            }
            return None
        self._trend_snapshot = self._trend_state(daily_closes, execution_closes, execution_timeframe)
        return daily_closes

    async def _fetch_execution_bars(
        self,
        timeframe: str,
        count: int = _EXECUTION_BAR_LOOKBACK,
    ) -> tuple[Optional[List[Dict]], Optional[int]]:
        """Fetch closed bars and choose the latest confirmed signal index for a timeframe."""
        try:
            bars = await self.api_client.get_trendbar(timeframe, count)
        except Exception as exc:
            logger.error("[TICK] Failed to fetch %s bars: %s", timeframe, exc)
            return None, None

        if len(bars) < 3:
            logger.warning("[TICK] Not enough %s bars (%d).", timeframe, len(bars))
            return None, None

        signal_index = self._select_signal_index(bars, self._watchers[timeframe])
        if signal_index is None:
            logger.warning("[TICK] Could not determine signal bar for %s close.", timeframe)
            return None, None
        return bars, signal_index

    @staticmethod
    def _aggregate_h1_closes_from_m5(bars: List[Dict]) -> Optional[List[float]]:
        """Roll up M5 bars into H1 closes for the H1 50/200 confirmation."""
        if not bars:
            return None
        closes: List[float] = []
        current_bucket = None
        last_close = None
        for bar in bars:
            bucket = bar["open_time"].replace(minute=0, second=0, microsecond=0)
            if current_bucket is None:
                current_bucket = bucket
            elif bucket != current_bucket:
                if last_close is not None:
                    closes.append(last_close)
                current_bucket = bucket
            last_close = bar["close"]
        if last_close is not None:
            closes.append(last_close)
        return closes if len(closes) >= 200 else None

    def _match_level_and_pattern(self, prev_candle, signal_candle):
        """
        Find the best HTF level touched by the recent price action and evaluate patterns there.

        Candidate levels are drawn from the combined range of the previous and
        signal candles, expanded by the configured proximity, so wick/body
        reactions are not discarded just because the close moved away.
        """
        if self.level_manager is None or self.pattern_detector is None:
            return None, None

        range_low = min(prev_candle.low, signal_candle.low)
        range_high = max(prev_candle.high, signal_candle.high)
        candidates = self.level_manager.levels_near_range(range_low, range_high)
        candidates = sorted(candidates, key=lambda lvl: abs(signal_candle.close - lvl))
        if not candidates:
            return None, None

        rank = {
            PatternType.BULLISH_ENGULFING: 3,
            PatternType.BEARISH_ENGULFING: 3,
            PatternType.BULLISH_PIN_BAR: 2,
            PatternType.BEARISH_PIN_BAR: 2,
            PatternType.INSIDE_BAR: 1,
            PatternType.NONE: 0,
        }

        best_level = candidates[0]
        best_result = None
        best_rank = -1
        best_distance = float("inf")

        for level in candidates:
            result = self.pattern_detector.detect(prev_candle, signal_candle, level)
            if result.pattern == PatternType.NONE:
                continue
            current_rank = rank[result.pattern]
            distance = abs(signal_candle.close - level)
            if current_rank > best_rank or (current_rank == best_rank and distance < best_distance):
                best_rank = current_rank
                best_distance = distance
                best_level = level
                best_result = result

        return best_level, best_result

    def _select_signal_index(self, bars: List[Dict], watcher: CandleWatcher) -> Optional[int]:
        """Choose the latest fully closed signal bar using returned open_time values."""
        if len(bars) < 3:
            return None

        latest = bars[-1]
        if watcher.current_candle_open and latest["open_time"] == watcher.current_candle_open:
            return len(bars) - 2
        return len(bars) - 1

    def _record_trade_level(self, level: float, direction: int) -> None:
        self._recent_trade_levels.appendleft(
            {"level": level, "direction": direction, "candle_index": self._candle_index}
        )

    def _same_level_cooldown_reason(self, level: float, direction: int) -> Optional[str]:
        cooldown = getattr(self.config, "same_level_cooldown_candles", 0)
        if cooldown <= 0:
            return None
        for item in self._recent_trade_levels:
            if item["direction"] != direction:
                continue
            if abs(item["level"] - level) > self.config.level_proximity_dollars:
                continue
            if self._candle_index - item["candle_index"] <= cooldown:
                return "SAME_LEVEL_COOLDOWN"
        return None

    def _record_strategy_entry(self, mode: str, level: Optional[float], direction: Optional[int]) -> None:
        self._reset_strategy_day(mode)
        candle_index = self._timeframe_candle_indices.get(self._strategy_timeframe(mode), 0)
        self._strategy_trade_counts[mode] += 1
        self._strategy_entries[mode].appendleft(
            {
                "level": level or 0.0,
                "direction": direction or 0,
                "candle_index": candle_index,
            }
        )

    def _strategy_trade_limit_reason(
        self,
        mode: str,
        level: Optional[float],
        direction: Optional[int],
    ) -> Optional[str]:
        self._reset_strategy_day(mode)
        trade_cap = (
            self.config.scalp_max_trades_per_day
            if mode == "SCALP_V1"
            else self.config.strategy_max_trades_per_day
        )
        if self._strategy_trade_counts[mode] >= trade_cap:
            return "STRATEGY_DAILY_CAP"
        if level is None or direction is None:
            return None
        cooldown = (
            self.config.scalp_reentry_cooldown_bars
            if mode == "SCALP_V1"
            else self.config.strategy_reentry_cooldown_candles
        )
        level_proximity = (
            self.config.scalp_touch_proximity_dollars
            if mode == "SCALP_V1"
            else self.config.ema_pullback_proximity_dollars
        )
        candle_index = self._timeframe_candle_indices.get(self._strategy_timeframe(mode), 0)
        for item in self._strategy_entries[mode]:
            if item["direction"] != direction:
                continue
            if abs(item["level"] - level) > level_proximity:
                continue
            if candle_index - item["candle_index"] <= cooldown:
                return "SCALP_COOLDOWN" if mode == "SCALP_V1" else "EMA_PULLBACK_COOLDOWN"
        return None

    def _reset_strategy_day(self, mode: str) -> None:
        today = self._today_utc()
        if self._strategy_trade_dates.get(mode) != today:
            self._strategy_trade_dates[mode] = today
            self._strategy_trade_counts[mode] = 0

    def _strategy_timeframe(self, mode: str) -> str:
        if mode == "EMA_PULLBACK_H1":
            return "H1"
        if mode == "SCALP_V1":
            return "M5"
        return self.config.execution_timeframe

    def _strategy_execution_lookback(self, mode: str) -> int:
        if mode == "SCALP_V1":
            return _SCALP_BAR_LOOKBACK
        if mode == "EMA_PULLBACK_H1":
            return _EMA_PULLBACK_BAR_LOOKBACK
        return _EXECUTION_BAR_LOOKBACK

    def _strategy_data_requirements(self, mode: str) -> Dict[str, int]:
        requirements = {
            "daily_closes_min": 21,
            "signal_index_min": 1,
        }
        if mode == "SCALP_V1":
            requirements.update(
                {
                    "execution_bars_min": max(
                        self.config.scalp_slow_ema_period,
                        self.config.scalp_atr_period + 1,
                        self.config.scalp_pullback_lookback_bars,
                    ),
                    "h1_closes_min": 200,
                    "execution_fetch_bars": self._strategy_execution_lookback(mode),
                }
            )
            return requirements
        requirements.update(
            {
                "execution_bars_min": 200,
                "h1_closes_min": 200,
                "execution_fetch_bars": self._strategy_execution_lookback(mode),
            }
        )
        return requirements

    def _update_strategy_data_status(
        self,
        mode: str,
        *,
        execution_timeframe: str,
        execution_bars: Optional[List[Dict]],
        signal_index: Optional[int],
        daily_closes: Optional[List[float]] = None,
        h1_closes: Optional[List[float]] = None,
    ) -> None:
        requirements = self._strategy_data_requirements(mode)
        execution_count = len(execution_bars) if execution_bars else 0
        daily_count = len(daily_closes) if daily_closes else 0
        h1_count = len(h1_closes) if h1_closes else 0
        if mode != "SCALP_V1":
            h1_count = execution_count

        reason = "OK"
        data_ready = True
        if execution_count < requirements["execution_bars_min"]:
            reason = "EXECUTION_BARS_UNAVAILABLE"
            data_ready = False
        elif signal_index is None or signal_index < requirements["signal_index_min"]:
            reason = "SIGNAL_BAR_UNAVAILABLE"
            data_ready = False
        elif daily_closes is None or daily_count < requirements["daily_closes_min"]:
            reason = "DAILY_BARS_UNAVAILABLE"
            data_ready = False
        elif mode == "SCALP_V1" and (h1_closes is None or h1_count < requirements["h1_closes_min"]):
            reason = "H1_CONFIRMATION_UNAVAILABLE"
            data_ready = False

        self._strategy_data_status[mode] = {
            "mode": mode,
            "execution_timeframe": execution_timeframe,
            "data_ready": data_ready,
            "reason": reason,
            "execution_bars": execution_count,
            "execution_bars_min": requirements["execution_bars_min"],
            "execution_fetch_bars": requirements["execution_fetch_bars"],
            "signal_index": signal_index,
            "daily_closes": daily_count,
            "daily_closes_min": requirements["daily_closes_min"],
            "h1_closes": h1_count,
            "h1_closes_min": requirements["h1_closes_min"],
            "updated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def current_strategy_health(self, now_utc: Optional[datetime] = None) -> Tuple[bool, Optional[str]]:
        del now_utc
        diagnostics = self._strategy_data_status.get(self.active_strategy_mode)
        if diagnostics is None:
            return False, "NO_STRATEGY_DIAGNOSTICS"
        return bool(diagnostics.get("data_ready")), diagnostics.get("reason")

    def _safe_current_spread(self) -> float:
        try:
            return self.api_client.get_current_spread()
        except Exception:
            return 0.0

    async def _environment_gate(self, apply_risk_gates: bool) -> Optional[str]:
        """Return the reason code for blocking a setup, or None when environment is tradeable."""
        now_utc = datetime.now(timezone.utc)

        tradeable, reason = self.session_filter.is_tradeable(now_utc)
        if not tradeable:
            return reason

        if apply_risk_gates:
            wiring_error = self._risk_state_wiring_error()
            if wiring_error is not None:
                logger.critical("[RISK] %s; automatic entries are disabled.", wiring_error)
                self.bot_status = f"HALTED_{wiring_error}"
                return wiring_error
            can_trade, reason = self.risk_gates.can_trade(self.account.get("balance", 0.0))
            if not can_trade:
                self.bot_status = f"HALTED_{reason}"
                return reason

        try:
            await self.news_filter.refresh_if_needed()
        except Exception as exc:
            logger.warning("[NEWS] Refresh attempt failed during candle processing: %s", exc)

        news_clear, reason = self.news_filter.is_clear(now_utc)
        if not news_clear:
            return reason

        if self.kill_switch_active:
            return "KILL_SWITCH"
        return None

    def _trend_state(self, daily_closes: List[float], execution_closes: List[float], execution_timeframe: str) -> Dict:
        bias, reason, snapshot = self.trend_filter.aligned_bias(
            daily_closes=daily_closes,
            execution_closes=execution_closes,
        )
        state = {
            "alignment": (
                "BULLISH" if bias == 1 else
                "BEARISH" if bias == -1 else
                "UNKNOWN" if snapshot is None else
                "MIXED"
            ),
            "reason": reason,
            "execution_timeframe": execution_timeframe,
        }
        if snapshot is not None:
            state.update({
                "daily_ema_8": round(snapshot.daily_ema_8, 2),
                "daily_ema_21": round(snapshot.daily_ema_21, 2),
                "exec_ema_50": round(snapshot.exec_ema_50, 2),
                "exec_ema_200": round(snapshot.exec_ema_200, 2),
            })
        return state

    @staticmethod
    def _today_utc() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _xauex_timezone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.config.xauex_entry_timezone)
        except Exception:
            logger.warning(
                "[XAUEX] Invalid timezone %s; using Europe/London instead.",
                self.config.xauex_entry_timezone,
            )
            return ZoneInfo("Europe/London")

    @staticmethod
    def _parse_hhmm(value: str) -> tuple[int, int]:
        hour_text, minute_text = value.split(":", 1)
        return max(0, min(23, int(hour_text))), max(0, min(59, int(minute_text)))

    def _xauex_entry_slot(self, now_utc: datetime) -> Optional[str]:
        return active_entry_slot(now_utc.astimezone(timezone.utc))

    def _xauex_defer_grace_minutes(self) -> int:
        try:
            return max(0, int(getattr(self.config, "xauex_defer_grace_minutes", 5) or 0))
        except (TypeError, ValueError):
            return 5

    def _xauex_grace_entry_slot(self, now_utc: datetime, sig: Dict[str, Any]) -> Optional[str]:
        """Slot for a deferred signal inside the post-window grace period.

        A strong signal that conflicts with microstructure is deferred for a
        later confirm pass; without grace, a retry landing after the entry
        window closes silently dies with ENTRY_WINDOW_CLOSED.
        """
        was_deferred = bool(sig.get("microstructure_deferred")) or int(
            sig.get("microstructure_defer_count") or 0
        ) > 0
        if not was_deferred:
            return None
        grace_minutes = self._xauex_defer_grace_minutes()
        if grace_minutes <= 0:
            return None
        now = now_utc.astimezone(timezone.utc)
        if now.astimezone(ZoneInfo("Europe/London")).weekday() >= 5:
            return None
        for window in all_live_windows():
            entry_end = window.entry_end_dt_utc(now)
            if entry_end <= now < entry_end + timedelta(minutes=grace_minutes):
                return window.slot
        return None

    def _xauex_signal_age_limit_seconds(self, *, slot: str, grace_entry: bool, now_utc: datetime) -> int:
        base = int(getattr(self.config, "xauex_signal_max_age_seconds", 300) or 300)
        if not grace_entry:
            return base
        window = get_live_window(slot=slot)
        if window is None:
            return base
        now = now_utc.astimezone(timezone.utc)
        budget_end = window.entry_end_dt_utc(now) + timedelta(minutes=self._xauex_defer_grace_minutes())
        budget = (budget_end - window.signal_dt_utc(now)).total_seconds()
        return max(base, int(budget))

    def _today_london(self, now_utc: Optional[datetime] = None) -> str:
        now_utc = now_utc or datetime.now(timezone.utc)
        return london_trade_day(now_utc.astimezone(timezone.utc))

    def _clear_xauex_runs_for_new_day(self, now_utc: Optional[datetime] = None) -> None:
        london_date = self._today_london(now_utc)
        if self.risk_state.xauex_trade_date_london != london_date:
            self.risk_state.xauex_trade_date_london = london_date
            self.risk_state.xauex_trades_taken_london = 0
            self.risk_state.xauex_signal_runs_london = []
            self._last_xauex_signal_id_by_slot = {}
            self._last_xauex_signal_seen_utc = {}
            self._xauex_close_requested = {}
        existing = self.risk_state.xauex_signal_runs_london
        existing_runs = [item for item in existing if str(item.get("date_london", "")) == london_date]
        if len(existing_runs) != len(existing):
            self.risk_state.xauex_signal_runs_london = existing_runs

    def _reset_xauex_trade_count_if_new_london_day(self, now_utc: Optional[datetime] = None) -> None:
        now_utc = now_utc or datetime.now(timezone.utc)
        self._clear_xauex_runs_for_new_day(now_utc)

    def _xauex_trades_taken_today(self, now_utc: Optional[datetime] = None) -> int:
        self._reset_xauex_trade_count_if_new_london_day(now_utc)
        return self.risk_state.xauex_trades_taken_london

    def _xauex_signal_runs_taken_today(self, now_utc: Optional[datetime] = None) -> int:
        self._reset_xauex_trade_count_if_new_london_day(now_utc)
        return sum(
            1
            for item in self.risk_state.xauex_signal_runs_london
            if str(item.get("date_london", "")) == self._today_london(now_utc)
            and bool(item.get("terminal", True))
        )

    def _xauex_pattern_policy_summary(self, now_utc: Optional[datetime] = None) -> Dict[str, object]:
        """Summarize current-day pattern-policy evidence for runtime diagnostics."""
        self._reset_xauex_trade_count_if_new_london_day(now_utc)
        london_date = self._today_london(now_utc)
        factor_counts: Dict[str, int] = {}
        evaluated_runs = 0
        terminal_pattern_blocks = 0
        for item in self.risk_state.xauex_signal_runs_london:
            if str(item.get("date_london", "")) != london_date:
                continue
            evidence = item.get("pattern_evidence")
            if not isinstance(evidence, dict):
                continue
            factor = str(evidence.get("factor") or "").upper()
            if not factor:
                continue
            evaluated_runs += 1
            factor_counts[factor] = factor_counts.get(factor, 0) + 1
            if bool(item.get("terminal", True)) and str(item.get("reason") or "") == "HARD_BLOCKER":
                if factor.startswith("PATTERN_"):
                    terminal_pattern_blocks += 1
        return {
            "evaluated_runs": evaluated_runs,
            "factor_counts": factor_counts,
            "terminal_pattern_blocks": terminal_pattern_blocks,
        }

    def _xauex_min_lot_canaries_today(self, now_utc: Optional[datetime] = None) -> int:
        self._reset_xauex_trade_count_if_new_london_day(now_utc)
        return sum(
            1
            for item in self.risk_state.xauex_signal_runs_london
            if str(item.get("date_london", "")) == self._today_london(now_utc)
            and bool(item.get("minimum_lot_canary", False))
        )

    def _slot_terminal_for_today(self, slot: str, *, now_utc: Optional[datetime] = None) -> bool:
        self._reset_xauex_trade_count_if_new_london_day(now_utc)
        now_utc = now_utc or datetime.now(timezone.utc)
        return any(
            str(item.get("slot", "")) == slot
            and str(item.get("date_london", "")) == self._today_london(now_utc)
            and bool(item.get("terminal", True))
            for item in self.risk_state.xauex_signal_runs_london
        )

    def _should_retry_signal_in_slot(self, slot: str, signal_id: str, now_utc: datetime) -> bool:
        last_signal = self._last_xauex_signal_id_by_slot.get(slot)
        if signal_id == "" or signal_id != last_signal:
            return True
        last_seen = self._last_xauex_signal_seen_utc.get(slot)
        if last_seen is None:
            return True
        age = now_utc.timestamp() - float(last_seen)
        return age >= _XAUEX_SLOT_RETRY_BACKOFF_SECONDS

    def _has_run_slot_been_used_today(self, slot: str, *, now_utc: Optional[datetime] = None) -> bool:
        return self._slot_terminal_for_today(slot, now_utc=now_utc)

    def _record_xauex_signal_run(
        self,
        *,
        slot: str,
        signal_id: str,
        action: str,
        signal_time: datetime,
        signal_reason: Optional[str] = None,
        signal_confidence: Optional[float] = None,
        signal_action: Optional[str] = None,
        window_label: Optional[str] = None,
        confirm_status: Optional[str] = None,
        confirm_reason: Optional[str] = None,
        confirm_timestamp_utc: Optional[str] = None,
        terminal: bool = True,
        minimum_lot_canary: bool = False,
        counter_signal: bool = False,
        policy_factors: Optional[List[str]] = None,
        hard_block_score: Optional[int] = None,
        pattern_evidence: Optional[Dict[str, object]] = None,
    ) -> None:
        self._reset_xauex_trade_count_if_new_london_day(signal_time)
        normalized_policy_factors = [str(item) for item in policy_factors or [] if str(item)]
        normalized_pattern_evidence = dict(pattern_evidence or {})
        self.risk_state.xauex_signal_runs_london.append(
            {
                "date_london": self._today_london(signal_time),
                "slot": slot,
                "signal_id": signal_id,
                "action": action,
                "signal_action": signal_action,
                "confidence": signal_confidence,
                "reason": signal_reason,
                "window_label": window_label,
                "confirm_status": confirm_status,
                "confirm_reason": confirm_reason,
                "confirm_timestamp_utc": confirm_timestamp_utc,
                "terminal": terminal,
                "minimum_lot_canary": bool(minimum_lot_canary),
                "counter_signal": bool(counter_signal),
                "policy_factors": normalized_policy_factors,
                "hard_block_score": max(0, int(hard_block_score or 0)),
                "pattern_evidence": normalized_pattern_evidence,
                "recorded_at_utc": signal_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
        self._journal_event(
            "risk_result",
            {
                "slot": slot,
                "reason": signal_reason or action,
                "action": action,
                "signal_action": signal_action,
                "confidence": signal_confidence,
                "terminal": terminal,
                "counter_signal": bool(counter_signal),
                "policy_factors": normalized_policy_factors,
                "hard_block_score": max(0, int(hard_block_score or 0)),
                "pattern_evidence": normalized_pattern_evidence,
            },
            correlation_id=signal_id,
        )

    def _mark_slot_used(
        self,
        *,
        slot: str,
        signal_id: str,
        reason: str,
        signal_time: datetime,
        signal_action: Optional[str] = None,
        signal_confidence: Optional[float] = None,
        window_label: Optional[str] = None,
        confirm_status: Optional[str] = None,
        confirm_reason: Optional[str] = None,
        confirm_timestamp_utc: Optional[str] = None,
        terminal: bool = True,
        minimum_lot_canary: bool = False,
        counter_signal: bool = False,
        blocked_trade: Optional[Dict[str, object]] = None,
        policy_factors: Optional[List[str]] = None,
        hard_block_score: Optional[int] = None,
        pattern_evidence: Optional[Dict[str, object]] = None,
    ) -> None:
        self._record_xauex_signal_run(
            slot=slot,
            signal_id=signal_id,
            action=reason,
            signal_time=signal_time,
            signal_reason=reason,
            signal_confidence=signal_confidence,
            signal_action=signal_action,
            window_label=window_label,
            confirm_status=confirm_status,
            confirm_reason=confirm_reason,
            confirm_timestamp_utc=confirm_timestamp_utc,
            terminal=terminal,
            minimum_lot_canary=minimum_lot_canary,
            counter_signal=counter_signal,
            policy_factors=policy_factors,
            hard_block_score=hard_block_score,
            pattern_evidence=pattern_evidence,
        )
        if terminal and reason != "ORDER_PLACED" and str(signal_action or "").upper() in {"BUY", "SELL"}:
            payload: Dict[str, object] = {
                "slot": slot,
                "window_label": window_label,
                "reason": reason,
                "signal_action": str(signal_action or "").upper(),
                "signal_confidence": signal_confidence,
                "confirm_status": confirm_status,
                "confirm_reason": confirm_reason,
                "confirm_timestamp_utc": confirm_timestamp_utc,
                "counter_signal": bool(counter_signal),
                "policy_factors": [str(item) for item in policy_factors or [] if str(item)],
                "hard_block_score": max(0, int(hard_block_score or 0)),
                "pattern_evidence": dict(pattern_evidence or {}),
            }
            if blocked_trade:
                payload["blocked_trade"] = dict(blocked_trade)
            self._journal_event("blocked_trade_candidate", payload, correlation_id=signal_id)
        self._last_xauex_signal_id_by_slot[slot] = signal_id

    def _refresh_signal_window_tracking(self, *, slot: str, signal_id: str, signal_time: datetime) -> None:
        self._last_xauex_signal_id_by_slot[slot] = signal_id
        self._last_xauex_signal_seen_utc[slot] = signal_time.timestamp()

    def _current_news_gate_snapshot(self) -> Dict[str, object]:
        now_utc = datetime.now(timezone.utc)
        clear = False
        reason = "NEWS_FEED_UNAVAILABLE"
        if self.news_filter is not None:
            try:
                clear, reason_value = self.news_filter.is_clear(now_utc)
                reason = "" if clear else str(reason_value or "LIVE_NEWS_HARD_BLOCK")
            except Exception:
                clear = False
                reason = "NEWS_FEED_UNAVAILABLE"
        return {
            "clear": clear,
            "reason": reason,
            "updated_at_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def _xauex_has_open_position(self) -> bool:
        if self.executor is None:
            return False
        return count_tradeable_open_positions(self.executor.position_manager.get_open_positions()) > 0

    def _xauex_open_reserved_risk(self) -> float:
        if self.executor is None:
            return 0.0
        total = 0.0
        for position in self.executor.position_manager.get_open_positions():
            if _position_owner(position) != "xauex":
                continue
            metadata = {}
            if isinstance(position, dict):
                metadata = dict(position.get("metadata") or {})
            else:
                metadata = dict(getattr(position, "metadata", {}) or {})
            session = metadata.get("session") if isinstance(metadata.get("session"), dict) else {}
            reserved = _safe_signal_float(session.get("actual_cash_risk"), 0.0)
            if reserved > 0:
                total += reserved
                continue
            try:
                entry_price = float(position.get("entry_price") if isinstance(position, dict) else getattr(position, "entry_price"))
                stop_loss = float(position.get("stop_loss") if isinstance(position, dict) else getattr(position, "stop_loss"))
                lot_size = float(position.get("lot_size") if isinstance(position, dict) else getattr(position, "lot_size"))
            except (AttributeError, TypeError, ValueError):
                continue
            contract_size = float(getattr(self.symbol_spec, "lot_size", 0.0) or 0.0)
            if contract_size <= 0:
                continue
            total += abs(entry_price - stop_loss) * lot_size * contract_size
        return round(total, 2)

    def _xauex_remaining_daily_loss_budget(self) -> float:
        return calculate_xauex_remaining_daily_loss_budget(
            day_start_balance=float(self.risk_state.day_start_balance or self.account.get("balance", 0.0) or 0.0),
            daily_stop_pct=float(self.config.daily_stop_pct or 0.0),
            realized_daily_pnl=float(self.risk_state.daily_pnl or 0.0),
            open_reserved_risk=self._xauex_open_reserved_risk(),
        )

    def _record_candidate_signal(
        self,
        *,
        slot: str,
        signal_id: str,
        signal: Dict[str, object],
        assurance: XauexAssuranceProfile,
        now_utc: datetime,
    ) -> None:
        entry = {
            "recorded_at_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "slot": slot,
            "window_label": str(signal.get("window_label") or "").lower() or slot.lower(),
            "signal_id": signal_id,
            "action": str(signal.get("action") or "HOLD").upper(),
            "confidence": _safe_signal_float(signal.get("confidence"), 0.0),
            "assurance_reason": assurance.reason,
        }
        self._candidate_signal_history.appendleft(entry)
        self._candidate_metrics["total"] = int(self._candidate_metrics.get("total", 0) or 0) + 1

    def _persist_inline_confirm_result(
        self,
        *,
        command_payload: Dict[str, object],
        signal: Dict[str, object],
        confirm: Dict[str, object],
    ) -> None:
        updated_signal = dict(signal)
        updated_signal["confirm_status"] = confirm["status"]
        updated_signal["confirm_reason"] = confirm["reason"]
        updated_signal["confirm_timestamp_utc"] = confirm["timestamp_utc"]
        for key in (
            "microstructure_policy",
            "microstructure_defer_count",
            "microstructure_deferred",
            "microstructure_soft_confirmed",
        ):
            if key in confirm:
                updated_signal[key] = confirm[key]
        command_payload["xauex_signal"] = updated_signal
        try:
            with open(self.config.xauex_signal_path, "w", encoding="utf-8") as handle:
                json.dump(command_payload, handle, indent=2)
        except Exception as exc:
            logger.warning("[XAUEX] Failed to persist inline confirm result: %s", exc)

    def _clear_stale_close_requests(self, *, open_position_ids: set[str], now_utc: datetime) -> None:
        stale_cutoff = now_utc - timedelta(seconds=_XAUEX_CLOSE_REQUEST_TTL_SECONDS)
        self._xauex_close_requested = {
            position_id: requested_at
            for position_id, requested_at in self._xauex_close_requested.items()
            if position_id in open_position_ids and requested_at >= stale_cutoff
        }

    def _should_emit_repeated_xauex_log(
        self,
        key: str,
        *,
        min_interval_seconds: int = _XAUEX_REPEATED_LOG_INTERVAL_SECONDS,
    ) -> bool:
        now = time.monotonic()
        last_emitted = self._last_xauex_gate_log_at.get(key)
        if last_emitted is None or (now - last_emitted) >= min_interval_seconds:
            self._last_xauex_gate_log_at[key] = now
            return True
        return False

    def _record_xauex_trade(self, now_utc: Optional[datetime] = None) -> None:
        self._reset_xauex_trade_count_if_new_london_day(now_utc)
        self.risk_state.xauex_trades_taken_london += 1

    def _append_trade_entry_on_chart(self, entry_id: Optional[str], direction: str, price: float, owner: str) -> None:
        if len(self._recent_h1_closes) <= 0:
            return
        entry_key = str(entry_id).strip() if entry_id is not None else ""
        if entry_key:
            for item in self._trade_entries_on_chart:
                if str(item.get("id", "")) == entry_key:
                    return
        payload = {
            "bar_index": len(self._recent_h1_closes) - 1,
            "direction": direction,
            "price": round(price, 2),
            "owner": owner,
        }
        if entry_key:
            payload["id"] = entry_key
        self._trade_entries_on_chart.append(payload)
        max_entries = max(1, len(self._recent_h1_closes))
        if len(self._trade_entries_on_chart) > max_entries:
            self._trade_entries_on_chart = self._trade_entries_on_chart[-max_entries:]

    def _xauex_entry_window_gate(self, now_utc: datetime) -> Optional[str]:
        london_now = now_utc.astimezone(ZoneInfo("Europe/London"))
        if london_now.weekday() >= 5:
            return "WEEKEND"

        slot = self._xauex_entry_slot(now_utc)
        if slot is not None:
            return None

        future_windows = [
            window
            for window in all_live_windows()
            if window.entry_start_dt_utc(now_utc.astimezone(timezone.utc)) > now_utc.astimezone(timezone.utc)
        ]
        if future_windows:
            return "TOO_EARLY"
        return "ENTRY_WINDOW_CLOSED"

    def _xauex_force_flat_due(self, now_utc: datetime) -> bool:
        london = now_utc.astimezone(self._xauex_timezone())
        if london.weekday() >= 5:
            return False
        flat_hour, flat_minute = self._parse_hhmm(self.config.xauex_force_flat_london)
        minute_of_day = london.hour * 60 + london.minute
        flat_minute_of_day = flat_hour * 60 + flat_minute
        return minute_of_day >= flat_minute_of_day

    def _xauex_slot_start_utc(self, slot: str, *, now_utc: datetime) -> datetime:
        window = get_live_window(slot=slot)
        if window is None:
            return now_utc.astimezone(timezone.utc)
        return window.entry_start_dt_utc(now_utc.astimezone(timezone.utc))

    def _stale_signal_should_consume_slot(
        self,
        *,
        slot: str,
        signal_time: datetime,
        now_utc: datetime,
    ) -> bool:
        slot_start_utc = self._xauex_slot_start_utc(slot, now_utc=now_utc)
        return signal_time >= slot_start_utc

    def _xauex_lot_multiplier(self, confidence: float) -> float:
        if confidence >= self.config.xauex_confidence_full_threshold:
            return 1.0
        if confidence >= self.config.xauex_confidence_medium_threshold:
            return self.config.xauex_medium_confidence_lot_multiplier
        return self.config.xauex_low_confidence_lot_multiplier

    def _scale_lot_to_confidence(self, lot: float, confidence: float) -> float | None:
        if self.symbol_spec is None:
            return lot
        multiplier = self._xauex_lot_multiplier(confidence)
        step = float(self.symbol_spec.volume_step)
        minimum = float(self.symbol_spec.volume_min)
        scaled = math.floor((lot * multiplier) / step) * step
        if scaled < minimum:
            if lot >= minimum:
                scaled = minimum
            else:
                return None
        scaled = min(scaled, float(self.symbol_spec.volume_max))
        scaled = min(scaled, float(getattr(self.config, "max_lot_size", scaled)))
        return round(scaled, 5)

    def _xauex_confidence_bucket(self, confidence: float) -> str:
        if confidence >= self.config.xauex_confidence_full_threshold:
            return "high"
        if confidence >= self.config.xauex_confidence_medium_threshold:
            return "medium"
        return "low"

    def _xauex_cash_risk_budget(self) -> float:
        balance = float(self.account.get("balance", 0.0) or 0.0)
        percent_cap = balance * (self.config.xauex_risk_cap_percent / 100.0)
        return round(min(self.config.xauex_cash_stop_loss_gbp, percent_cap), 2)

    def _xauex_loss_cooldown_multiplier(self) -> float:
        """Size-down after consecutive losses.

        RiskGates already hard-halts the day at `max_consecutive_losses` (3 by
        default). Before that halt triggers we still take trades 2 and 3 at
        full size, which is exactly how a losing streak compounds. After 2
        losses, cut the next trade to 50% risk so a 3rd loss hurts half as
        much, and a 4th loss (if the gate ever raised the limit) hurts even
        less. A winning trade resets the counter via record_trade_closed, so
        the multiplier returns to 1.0 automatically.
        """
        gates = getattr(self, "risk_gates", None)
        if gates is None:
            return 1.0
        losses = int(getattr(gates.state, "consecutive_losses_today", 0) or 0)
        if losses >= 3:
            return 0.3
        if losses >= 2:
            return 0.5
        return 1.0

    def _xauex_session_slot_multiplier(self, slot: Optional[str]) -> float:
        """Per-slot risk scaling. US_OPEN tends to whipsaw around data prints,
        so apply a small downsize there even when assurance is high. London
        morning trends remain full size; midday continuation stays full size.
        """
        if not slot:
            return 1.0
        slot_key = str(slot).upper()
        if slot_key == "US_OPEN":
            return float(getattr(self.config, "xauex_us_open_risk_multiplier", 0.85))
        if slot_key == "MIDDAY":
            return float(getattr(self.config, "xauex_midday_risk_multiplier", 1.0))
        return float(getattr(self.config, "xauex_london_open_risk_multiplier", 1.0))

    def _xauex_recent_atr_distance(self) -> float:
        closes = [float(value) for value in self._recent_h1_closes if value is not None]
        if len(closes) < 2:
            return float(self.config.sl_min_dollars)
        ranges = [abs(curr - prev) for prev, curr in zip(closes[:-1], closes[1:])]
        if not ranges:
            return float(self.config.sl_min_dollars)
        avg_range = sum(ranges) / len(ranges)
        return max(
            float(self.config.sl_min_dollars),
            round(avg_range * self.config.xauex_session_atr_multiplier, 2),
        )

    def _xauex_structure_stop_distance(self, direction: int, current_price: float) -> float:
        daily_levels = None
        if self.level_manager:
            raw_levels = getattr(self.level_manager, "_raw", None)
            if isinstance(raw_levels, dict):
                daily_levels = raw_levels.get("daily")
            else:
                daily_levels = raw_levels
        closes = [float(value) for value in self._recent_h1_closes if value is not None]
        buffer_usd = float(self.config.xauex_session_structure_buffer_usd)
        if direction > 0:
            floor = None
            if isinstance(daily_levels, dict):
                floor = daily_levels.get("low")
            elif daily_levels is not None:
                floor = getattr(daily_levels, "day_low", None)
            if floor is None and closes:
                floor = min(closes)
            if floor is None:
                return float(self.config.sl_min_dollars)
            return max(float(self.config.sl_min_dollars), round(current_price - float(floor) + buffer_usd, 2))
        ceiling = None
        if isinstance(daily_levels, dict):
            ceiling = daily_levels.get("high")
        elif daily_levels is not None:
            ceiling = getattr(daily_levels, "day_high", None)
        if ceiling is None and closes:
            ceiling = max(closes)
        if ceiling is None:
            return float(self.config.sl_min_dollars)
        return max(float(self.config.sl_min_dollars), round(float(ceiling) - current_price + buffer_usd, 2))

    def _xauex_session_thresholds(self, confidence: float) -> tuple[str, float, float]:
        bucket = self._xauex_confidence_bucket(confidence)
        if bucket == "low":
            protect_r = float(self.config.xauex_session_low_confidence_protect_r)
            trail_r = max(protect_r + 0.3, float(self.config.xauex_session_trail_r) - 0.15)
        elif bucket == "high":
            protect_r = float(self.config.xauex_session_high_confidence_protect_r)
            trail_r = float(self.config.xauex_session_trail_r) + 0.15
        else:
            protect_r = float(self.config.xauex_session_protect_r)
            trail_r = float(self.config.xauex_session_trail_r)
        return bucket, round(protect_r, 2), round(max(trail_r, protect_r + 0.2), 2)

    def _xauex_protect_stop_price(
        self,
        *,
        direction: str,
        entry_price: float,
        initial_risk_distance: float,
        lock_r: float,
    ) -> float:
        buffer_usd = max(float(self.config.xauex_session_protect_buffer_usd), self._safe_current_spread() * 1.5)
        return build_xauex_protect_stop_price(
            direction=direction,
            entry_price=entry_price,
            initial_risk_distance=initial_risk_distance,
            lock_r=lock_r,
            min_buffer_usd=buffer_usd,
        )

    def _xauex_cash_take_profit_threshold(self, session: Dict[str, object]) -> float:
        target_cash_reward = _safe_signal_float(session.get("target_cash_reward"), 0.0)
        return round(max(float(self.config.xauex_cash_take_profit_gbp), target_cash_reward), 2)

    def _xauex_trailing_stop_price(
        self,
        *,
        direction: str,
        current_price: float,
        confidence_bucket: str,
    ) -> float:
        closes = [float(value) for value in self._recent_h1_closes if value is not None]
        if not closes:
            closes = [current_price]
        atr_distance = self._xauex_recent_atr_distance()
        if confidence_bucket == "low":
            atr_distance *= 0.9
        elif confidence_bucket == "high":
            atr_distance *= 1.1
        buffer_usd = float(self.config.xauex_session_structure_buffer_usd)
        window = closes[-5:] or closes
        if direction == "SHORT":
            structure = max(window) + buffer_usd
            atr_based = current_price + atr_distance
            return round(min(structure, atr_based), 2)
        structure = min(window) - buffer_usd
        atr_based = current_price - atr_distance
        return round(max(structure, atr_based), 2)

    def _load_prior_position_metadata(self) -> Dict[str, Dict[str, object]]:
        try:
            with open(self.config.state_file_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return {}
        positions = payload.get("open_positions", []) if isinstance(payload, dict) else []
        result: Dict[str, Dict[str, object]] = {}
        for item in positions:
            if not isinstance(item, dict):
                continue
            position_id = str(item.get("position_id", "") or "")
            if not position_id:
                continue
            result[position_id] = {
                "owner": item.get("owner", "strategy"),
                "metadata": item.get("metadata", {}) or {},
            }
        return result

    def _load_prior_pending_market_orders(self) -> Dict[str, Dict[str, object]]:
        try:
            with open(self.config.state_file_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return {}
        runtime = payload.get("runtime", {}) if isinstance(payload, dict) else {}
        pending = runtime.get("pending_market_orders", {}) if isinstance(runtime, dict) else {}
        return dict(pending) if isinstance(pending, dict) else {}

    # ──────────────────────────────────────────────────────────────
    # Shutdown
    # ──────────────────────────────────────────────────────────────

    async def shutdown(self) -> None:
        """Graceful shutdown: write SHUTDOWN status, close API, exit."""
        logger.info("[SHUTDOWN] Shutting down at %s UTC", datetime.now(timezone.utc).isoformat())
        self.running = False
        self.bot_status = "SHUTDOWN"
        try:
            await self.write_state()
        except Exception:
            pass
        if self.news_filter:
            try:
                await self.news_filter.close()
            except Exception:
                pass
        if self.api_client:
            try:
                await self.api_client.disconnect()
            except Exception:
                pass
        logger.info("[SHUTDOWN] Complete.")

    # ──────────────────────────────────────────────────────────────
    # State writing
    # ──────────────────────────────────────────────────────────────

    def set_status(self, status: str) -> None:
        self.bot_status = status

    def _journal_event(self, event_type: str, payload: Dict[str, object], *, correlation_id: Optional[str] = None) -> None:
        journal_path = getattr(self.config, "xauex_event_journal_path", None)
        if not journal_path:
            return
        safe_append_event(
            journal_path,
            source="bot",
            event_type=event_type,
            payload=payload,
            correlation_id=correlation_id,
        )

    async def write_state(self) -> None:
        if self.state_writer is None:
            return
        levels = self.level_manager._raw if self.level_manager else None
        open_positions = self.executor.position_manager.get_open_positions() if self.executor else []
        closed_trades = self.executor.get_closed_trades_today() if self.executor else []
        risk_wiring_error = self._risk_state_wiring_error()
        await self.state_writer.write(
            bot_status=self.bot_status,
            account=self.account,
            risk_state=self.risk_state,
            levels=levels,
            open_positions=open_positions,
            closed_trades=closed_trades,
            recent_h1_closes=list(self._recent_h1_closes),
            trade_entries_on_chart=self._trade_entries_on_chart,
            last_signal=self._last_signal,
            signal_history=list(self._signal_history),
            strategy={
                "active_mode": self.active_strategy_mode,
                "shadow_mode": self.shadow_strategy_mode,
            },
            shadow_last_signal=self._shadow_last_signal,
            shadow_signal_history=list(self._shadow_signal_history),
            macro_regime=(
                self._macro_regime.to_state_dict()
                if self._macro_regime is not None
                else None
            ),
            trade_policy=(
                self._trade_policy.to_state_dict()
                if self._trade_policy is not None
                else None
            ),
            trend=self._trend_snapshot,
            runtime={
                "strategy_mode": self.active_strategy_mode,
                "shadow_strategy_mode": self.shadow_strategy_mode,
                "execution_timeframe": self.execution_timeframe,
                "reconnect_count": self.watchdog.reconnect_count if self.watchdog else 0,
                "news_feed_available": self.news_filter.feed_available if self.news_filter else None,
                "news_gate": self._current_news_gate_snapshot(),
                "kill_switch_active": self.kill_switch_active,
                "candle_index": self._candle_index,
                "pending_inside_bar_pairs": len(self.executor._pending_pairs) if self.executor else 0,
                "recent_trade_levels": len(self._recent_trade_levels),
                "strategy_trades_today": self._strategy_trade_counts.get(self.active_strategy_mode, 0),
                "shadow_strategy_trades_today": (
                    self._strategy_trade_counts.get(self.shadow_strategy_mode, 0)
                    if self.shadow_strategy_mode else 0
                ),
                "manual_trade_status": self._manual_trade_status,
                "pending_market_orders": (
                    self.executor.serialize_pending_market_orders()
                    if self.executor else {}
                ),
                "latest_quote": dict(self._latest_quote),
                "xauex_trade_date_london": self.risk_state.xauex_trade_date_london,
                "xauex_trades_taken_london": self.risk_state.xauex_trades_taken_london,
                "xauex_signal_runs_taken_london": len(self.risk_state.xauex_signal_runs_london),
                "xauex_signal_runs_london": list(self.risk_state.xauex_signal_runs_london),
                "xauex_pattern_policy": self._xauex_pattern_policy_summary(),
                "xauex_max_trades_per_day": self.config.xauex_max_trades_per_day,
                "risk_state_wiring": {
                    "valid": risk_wiring_error is None,
                    "error": risk_wiring_error or "",
                },
                "candidate_metrics": dict(self._candidate_metrics),
                "candidate_signal_history": list(self._candidate_signal_history),
                "strategy_data_status": self._strategy_data_status.get(self.active_strategy_mode),
                "shadow_strategy_data_status": (
                    self._strategy_data_status.get(self.shadow_strategy_mode)
                    if self.shadow_strategy_mode else None
                ),
            },
            last_error=self.last_error,
        )
        # Persist risk state to its own file on every write
        await save_risk_state(self.risk_state, self.config.state_file_path)

    # ──────────────────────────────────────────────────────────────
    # Background tasks
    # ──────────────────────────────────────────────────────────────

    async def _poll_kill_switch(self) -> None:
        while self.running:
            await asyncio.sleep(10)
            try:
                with open(self.config.cmd_file_path, "r") as f:
                    cmd = json.load(f)
                if cmd.get("kill_switch") is True and not self.kill_switch_active:
                    logger.warning("[KILL SWITCH] Activated. Halting new trade entry.")
                    self.kill_switch_active = True
                    self.set_status("HALTED_KILL_SWITCH")
                    await self.write_state()
                elif cmd.get("kill_switch") is False and self.kill_switch_active:
                    logger.info("[KILL SWITCH] Deactivated. Resuming trading.")
                    self.kill_switch_active = False
                    self.set_status("RUNNING")
                    await self.write_state()
            except FileNotFoundError:
                pass
            except Exception as exc:
                logger.warning("[KILL SWITCH] cmd.json read error: %s", exc)

    async def _poll_xauex_signal(self) -> None:
        """Poll cmd.json for XAUEX trading signals (runs only in XAUEX_MODE)."""
        _DIRECTION_MAP = {"BUY": 1, "SELL": -1}
        while self.running:
            await asyncio.sleep(10)
            if self.kill_switch_active:
                continue
            try:
                with open(self.config.xauex_signal_path, "r") as f:
                    cmd = json.load(f)
            except FileNotFoundError:
                continue
            except Exception as exc:
                logger.warning("[XAUEX] Signal file read error: %s", exc)
                continue

            sig = cmd.get("xauex_signal") or {}
            if not sig:
                continue

            signal_id = str(sig.get("timestamp_utc", "") or "").strip()
            window_label = str(sig.get("window_label") or "").lower() or "current"
            now_utc = datetime.now(timezone.utc)
            slot = self._xauex_entry_slot(now_utc)
            grace_entry = False
            if slot is None:
                grace_slot = self._xauex_grace_entry_slot(now_utc, sig)
                if grace_slot is not None:
                    slot = grace_slot
                    grace_entry = True
                    if self._should_emit_repeated_xauex_log(f"grace_entry:{slot}"):
                        logger.info(
                            "[XAUEX] Deferred signal %s re-entering %s within the post-window grace period.",
                            signal_id,
                            slot,
                        )
            if slot is None:
                entry_gate = self._xauex_entry_window_gate(now_utc)
                if entry_gate == "TOO_EARLY":
                    if self._should_emit_repeated_xauex_log("entry_gate:TOO_EARLY"):
                        logger.info(
                            "[XAUEX] Signal ready but the London entry window has not opened yet."
                        )
                elif entry_gate is not None:
                    if self._should_emit_repeated_xauex_log(f"entry_gate:{entry_gate}"):
                        logger.info("[XAUEX] Blocked by entry window: %s", entry_gate)
                continue

            if self._has_run_slot_been_used_today(slot, now_utc=now_utc):
                if self._should_emit_repeated_xauex_log(f"slot_used:{slot}:{signal_id}", min_interval_seconds=900):
                    logger.info("[XAUEX] Slot %s already used today. Ignoring signal %s.", slot, signal_id)
                continue

            if not self._should_retry_signal_in_slot(slot, signal_id, now_utc):
                continue

            self._refresh_signal_window_tracking(slot=slot, signal_id=signal_id, signal_time=now_utc)

            action = str(sig.get("action", "HOLD")).upper()
            direction = _DIRECTION_MAP.get(action)
            if direction is None:
                logger.info("[XAUEX] Signal action=%s - treated as HOLD / no trade", action)
                self._mark_slot_used(
                    slot=slot,
                    signal_id=signal_id,
                    reason="HOLD",
                    signal_time=now_utc,
                    signal_action=action,
                    window_label=window_label,
                    confirm_status=str(sig.get("confirm_status") or "SKIP"),
                    confirm_reason=str(sig.get("confirm_reason") or "NO_DIRECTIONAL_SIGNAL"),
                    confirm_timestamp_utc=str(sig.get("confirm_timestamp_utc") or ""),
                    terminal=True,
                )
                await self.write_state()
                continue

            try:
                confidence = float(sig.get("confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            confirm_status = str(sig.get("confirm_status") or "PENDING").upper()
            confirm_reason = str(sig.get("confirm_reason") or "WAITING_FOR_CONFIRM")
            confirm_timestamp_utc = str(sig.get("confirm_timestamp_utc") or "")
            # Defense in depth: a CONFIRMED/SKIP status from cmd.json that is
            # older than the configured freshness window can no longer be
            # trusted (news events may have started, microstructure may have
            # shifted). Force a re-confirmation by demoting the status to
            # PENDING so the existing confirm-pass branch below runs.
            confirm_max_age_seconds = int(
                getattr(self.config, "xauex_confirm_max_age_seconds", XAUEX_CONFIRM_MAX_AGE_SECONDS_DEFAULT)
                or XAUEX_CONFIRM_MAX_AGE_SECONDS_DEFAULT
            )
            if confirm_status in {"CONFIRMED", "SKIP"} and not is_xauex_confirm_timestamp_fresh(
                confirm_timestamp_utc=confirm_timestamp_utc,
                now_utc=now_utc,
                max_age_seconds=confirm_max_age_seconds,
            ):
                logger.warning(
                    "[XAUEX] confirm timestamp %s is older than %ds for %s/%s — forcing re-confirmation.",
                    confirm_timestamp_utc or "<missing>",
                    confirm_max_age_seconds,
                    slot,
                    signal_id,
                )
                confirm_status = "PENDING"
                confirm_reason = "CONFIRM_TIMESTAMP_STALE"
            self._journal_event(
                "signal_decision",
                {
                    "slot": slot,
                    "window_label": window_label,
                    "action": action,
                    "confidence": confidence,
                    "confirm_status": confirm_status,
                    "confirm_reason": confirm_reason,
                    "microstructure_policy": str(sig.get("microstructure_policy") or ""),
                    "microstructure_defer_count": sig.get("microstructure_defer_count"),
                    "trend_alignment": str((self._trend_snapshot or {}).get("alignment", "") or ""),
                    "shadow_action": str((self._shadow_last_signal or {}).get("action", "") or ""),
                },
                correlation_id=signal_id,
            )

            try:
                ts = datetime.fromisoformat(signal_id.replace("Z", "+00:00"))
                age = (now_utc - ts).total_seconds()
                signal_age_limit = self._xauex_signal_age_limit_seconds(
                    slot=slot,
                    grace_entry=grace_entry,
                    now_utc=now_utc,
                )
                if age > signal_age_limit:
                    terminal_stale = self._stale_signal_should_consume_slot(
                        slot=slot,
                        signal_time=ts.astimezone(timezone.utc),
                        now_utc=now_utc,
                    )
                    logger.info(
                        "[XAUEX] Signal is %.0fs old (max %ds) - stale, skipping",
                        age,
                        signal_age_limit,
                    )
                    self._mark_slot_used(
                        slot=slot,
                        signal_id=signal_id,
                        reason="STALE",
                        signal_time=now_utc,
                        signal_action=action,
                        signal_confidence=confidence,
                        window_label=window_label,
                        confirm_status="SKIP",
                        confirm_reason="STALE_SIGNAL",
                        confirm_timestamp_utc=now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        terminal=terminal_stale,
                    )
                    await self.write_state()
                    continue
            except (ValueError, TypeError):
                logger.warning("[XAUEX] Cannot parse signal timestamp '%s' - skipping", signal_id)
                self._mark_slot_used(
                    slot=slot,
                    signal_id=signal_id,
                    reason="INVALID_SIGNAL_TIMESTAMP",
                    signal_time=now_utc,
                    signal_action=action,
                    signal_confidence=confidence,
                    window_label=window_label,
                    confirm_status="SKIP",
                    confirm_reason="INVALID_SIGNAL_TIMESTAMP",
                    confirm_timestamp_utc=now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    terminal=True,
                )
                await self.write_state()
                continue

            try:
                self._reset_xauex_trade_count_if_new_london_day(now_utc)

                if self._xauex_trades_taken_today(now_utc) >= self.config.xauex_max_trades_per_day:
                    logger.info(
                        "[XAUEX] Daily London trade cap reached (%d/%d).",
                        self.risk_state.xauex_trades_taken_london,
                        self.config.xauex_max_trades_per_day,
                    )
                    self._mark_slot_used(
                        slot=slot,
                        signal_id=signal_id,
                        reason="CAP_REACHED",
                        signal_time=now_utc,
                        signal_action=action,
                        signal_confidence=confidence,
                        window_label=window_label,
                        confirm_status=confirm_status,
                        confirm_reason=confirm_reason,
                        confirm_timestamp_utc=confirm_timestamp_utc,
                        terminal=True,
                    )
                    await self.write_state()
                    continue

                if self.symbol_spec is None:
                    logger.warning("[XAUEX] Symbol spec not loaded yet - skipping")
                    self._mark_slot_used(
                        slot=slot,
                        signal_id=signal_id,
                        reason="SYMBOL_SPEC_MISSING",
                        signal_time=now_utc,
                        signal_action=action,
                        signal_confidence=confidence,
                        window_label=window_label,
                        confirm_status=confirm_status,
                        confirm_reason=confirm_reason,
                        confirm_timestamp_utc=confirm_timestamp_utc,
                        terminal=False,
                    )
                    await self.write_state()
                    continue

                signal_symbol = str(sig.get("symbol") or "").upper()
                live_symbol = str(getattr(self.symbol_spec, "symbol", "") or "").upper()
                if signal_symbol and live_symbol and signal_symbol != live_symbol:
                    logger.info(
                        "[XAUEX] Signal symbol %s does not match configured symbol %s - skipping",
                        signal_symbol,
                        live_symbol,
                    )
                    self._mark_slot_used(
                        slot=slot,
                        signal_id=signal_id,
                        reason="SYMBOL_MISMATCH",
                        signal_time=now_utc,
                        signal_action=action,
                        signal_confidence=confidence,
                        window_label=window_label,
                        confirm_status=confirm_status,
                        confirm_reason=confirm_reason,
                        confirm_timestamp_utc=confirm_timestamp_utc,
                        terminal=True,
                    )
                    await self.write_state()
                    continue

                distance_unit = str(sig.get("distance_unit", "usd") or "usd").lower()
                if distance_unit not in ("usd", "dollars", "price"):
                    logger.info(
                        "[XAUEX] Signal distance_unit=%s is not executable by the current XAUEX runtime - skipping",
                        distance_unit,
                    )
                    self._mark_slot_used(
                        slot=slot,
                        signal_id=signal_id,
                        reason="INVALID_DISTANCE_UNIT",
                        signal_time=now_utc,
                        signal_action=action,
                        signal_confidence=confidence,
                        window_label=window_label,
                        confirm_status=confirm_status,
                        confirm_reason=confirm_reason,
                        confirm_timestamp_utc=confirm_timestamp_utc,
                        terminal=True,
                    )
                    await self.write_state()
                    continue

                if confirm_status not in {"CONFIRMED", "SKIP"}:
                    confirm = build_xauex_confirm_decision(
                        signal=sig,
                        now_utc=now_utc,
                        latest_quote=dict(self._latest_quote),
                        news_gate=self._current_news_gate_snapshot(),
                        trend_snapshot=self._trend_snapshot,
                        shadow_signal=self._shadow_last_signal,
                        config=self.config,
                        signal_max_age_override_seconds=(
                            self._xauex_signal_age_limit_seconds(
                                slot=slot, grace_entry=True, now_utc=now_utc
                            )
                            if grace_entry
                            else None
                        ),
                        grace_entry=grace_entry,
                    )
                    confirm_status = str(confirm["status"] or "SKIP").upper()
                    confirm_reason = str(confirm["reason"] or "UNKNOWN")
                    confirm_timestamp_utc = str(confirm["timestamp_utc"] or "")
                    sig["confirm_status"] = confirm_status
                    sig["confirm_reason"] = confirm_reason
                    sig["confirm_timestamp_utc"] = confirm_timestamp_utc
                    self._persist_inline_confirm_result(
                        command_payload=cmd,
                        signal=sig,
                        confirm=confirm,
                    )

                if confirm_status == "PENDING" and confirm_reason == "MICROSTRUCTURE_DEFERRED":
                    logger.info(
                        "[XAUEX] Microstructure conflict deferred for %s; retrying in %ds.",
                        signal_id,
                        _XAUEX_SLOT_RETRY_BACKOFF_SECONDS,
                    )
                    self._mark_slot_used(
                        slot=slot,
                        signal_id=signal_id,
                        reason=confirm_reason,
                        signal_time=now_utc,
                        signal_action=action,
                        signal_confidence=confidence,
                        window_label=window_label,
                        confirm_status=confirm_status,
                        confirm_reason=confirm_reason,
                        confirm_timestamp_utc=confirm_timestamp_utc,
                        terminal=False,
                    )
                    await self.write_state()
                    continue

                if confirm_status != "CONFIRMED":
                    counter_candidate = build_xauex_counter_signal_candidate(
                        signal=sig,
                        original_confirm={
                            "status": confirm_status,
                            "reason": confirm_reason,
                            "timestamp_utc": confirm_timestamp_utc,
                        },
                        now_utc=now_utc,
                        latest_quote=dict(self._latest_quote),
                        news_gate=self._current_news_gate_snapshot(),
                        trend_snapshot=self._trend_snapshot,
                        shadow_signal=self._shadow_last_signal,
                        config=self.config,
                    )
                    if counter_candidate is not None:
                        original_action = action
                        original_confidence = confidence
                        sig = counter_candidate
                        action = str(sig.get("action", "HOLD") or "HOLD").upper()
                        direction = _DIRECTION_MAP.get(action)
                        confidence = _safe_signal_float(sig.get("confidence"), 0.0)
                        confirm_status = str(sig.get("confirm_status") or "CONFIRMED").upper()
                        confirm_reason = str(sig.get("confirm_reason") or "COUNTER_SIGNAL_CONFIRMED")
                        confirm_timestamp_utc = str(sig.get("confirm_timestamp_utc") or "")
                        self._journal_event(
                            "counter_signal_candidate",
                            {
                                "slot": slot,
                                "window_label": window_label,
                                "source_action": original_action,
                                "source_confidence": original_confidence,
                                "source_confirm_reason": str(sig.get("counter_source_confirm_reason") or ""),
                                "counter_action": action,
                                "counter_confidence": confidence,
                                "counter_confirm_reason": str(sig.get("counter_confirm_reason") or ""),
                                "trend_alignment": str((self._trend_snapshot or {}).get("alignment", "") or ""),
                                "shadow_action": str((self._shadow_last_signal or {}).get("action", "") or ""),
                                "risk_multiplier": sig.get("counter_signal_risk_multiplier"),
                            },
                            correlation_id=signal_id,
                        )
                        logger.info(
                            "[XAUEX] Counter-signal activated: %s vetoed by %s -> %s at %.2fx risk",
                            original_action,
                            str(sig.get("counter_source_confirm_reason") or confirm_reason),
                            action,
                            _safe_signal_float(sig.get("counter_signal_risk_multiplier"), 0.0),
                        )
                    else:
                        logger.warning(
                            "[XAUEX] Confirm veto blocked trade: status=%s reason=%s slot=%s signal=%s action=%s confidence=%.2f",
                            confirm_status,
                            confirm_reason,
                            slot,
                            signal_id,
                            action,
                            confidence,
                        )
                        self._journal_event(
                            "confirm_veto",
                            {
                                "slot": slot,
                                "window_label": window_label,
                                "confirm_status": confirm_status,
                                "confirm_reason": confirm_reason,
                                "signal_action": action,
                                "signal_confidence": confidence,
                                "confirm_timestamp_utc": confirm_timestamp_utc,
                            },
                            correlation_id=signal_id,
                        )
                        self._mark_slot_used(
                            slot=slot,
                            signal_id=signal_id,
                            reason=confirm_reason,
                            signal_time=now_utc,
                            signal_action=action,
                            signal_confidence=confidence,
                            window_label=window_label,
                            confirm_status=confirm_status,
                            confirm_reason=confirm_reason,
                            confirm_timestamp_utc=confirm_timestamp_utc,
                            terminal=True,
                        )
                        await self.write_state()
                        continue

                if confirm_status != "CONFIRMED" or direction is None:
                    logger.warning(
                        "[XAUEX] Confirm veto blocked trade (post-counter): status=%s reason=%s slot=%s signal=%s direction=%s",
                        confirm_status,
                        confirm_reason,
                        slot,
                        signal_id,
                        direction,
                    )
                    self._mark_slot_used(
                        slot=slot,
                        signal_id=signal_id,
                        reason=confirm_reason,
                        signal_time=now_utc,
                        signal_action=action,
                        signal_confidence=confidence,
                        window_label=window_label,
                        confirm_status=confirm_status,
                        confirm_reason=confirm_reason,
                        confirm_timestamp_utc=confirm_timestamp_utc,
                        terminal=True,
                    )
                    await self.write_state()
                    continue

                assurance = build_xauex_assurance_profile(sig, self.config)
                if not assurance.allow_trade:
                    logger.info(
                        "[XAUEX] Blocked by assurance profile: %s score=%.2f confidence=%.2f consensus=%s validator=%s",
                        assurance.reason,
                        assurance.score,
                        confidence,
                        sig.get("consensus_state", ""),
                        sig.get("validator_status", ""),
                    )
                    if assurance.reason in {"ASSURANCE_TOO_LOW", "LOW_ASSURANCE_VALIDATOR_DISAGREEMENT"}:
                        self._record_candidate_signal(
                            slot=slot,
                            signal_id=signal_id,
                            signal=sig,
                            assurance=assurance,
                            now_utc=now_utc,
                        )
                    self._mark_slot_used(
                        slot=slot,
                        signal_id=signal_id,
                        reason=assurance.reason,
                        signal_time=now_utc,
                        signal_action=action,
                        signal_confidence=confidence,
                        window_label=window_label,
                        confirm_status=confirm_status,
                        confirm_reason=confirm_reason,
                        confirm_timestamp_utc=confirm_timestamp_utc,
                        terminal=True,
                    )
                    await self.write_state()
                    continue

                pattern_evidence = await self._xauex_pattern_evidence(
                    direction=direction,
                    now_utc=now_utc,
                )
                sig["pattern_evidence"] = pattern_evidence.to_dict()
                entry_quality = build_xauex_entry_quality_decision(
                    signal=sig,
                    assurance=assurance,
                    config=self.config,
                    now_utc=now_utc,
                    closed_trades_today=(self.executor.get_closed_trades_today(now_utc) if self.executor else []),
                    signal_runs_london=list(self.risk_state.xauex_signal_runs_london),
                    pattern_evidence=pattern_evidence,
                )
                if not bool(entry_quality.get("allowed")):
                    reason = str(entry_quality.get("reason") or "ENTRY_QUALITY_BLOCKED")
                    logger.info(
                        "[XAUEX] Blocked by entry-quality gate: %s action=%s confidence=%.2f",
                        reason,
                        action,
                        confidence,
                    )
                    self._mark_slot_used(
                        slot=slot,
                        signal_id=signal_id,
                        reason=reason,
                        signal_time=now_utc,
                        signal_action=action,
                        signal_confidence=confidence,
                        window_label=window_label,
                        confirm_status=confirm_status,
                        confirm_reason=confirm_reason,
                        confirm_timestamp_utc=confirm_timestamp_utc,
                        terminal=True,
                        counter_signal=bool(sig.get("counter_signal")),
                        policy_factors=list(entry_quality.get("policy_factors", [])),
                        hard_block_score=int(entry_quality.get("hard_block_score", 0) or 0),
                        pattern_evidence=pattern_evidence.to_dict(),
                    )
                    await self.write_state()
                    continue
                sig["entry_quality_policy"] = dict(entry_quality)
                entry_quality_risk_multiplier = round(
                    max(0.05, min(1.0, _safe_signal_float(entry_quality.get("risk_multiplier"), 1.0))),
                    2,
                )
                if entry_quality_risk_multiplier < 1.0:
                    logger.info(
                        "[XAUEX] Entry-quality warnings %s reduce risk by %.2fx.",
                        ",".join(str(item) for item in entry_quality.get("policy_factors", [])),
                        entry_quality_risk_multiplier,
                    )

                continuation_addon_decision: Dict[str, object] = {
                    "allowed": False,
                    "reason": "NO_XAUEX_POSITION_OPEN",
                    "parent_position_id": "",
                    "risk_multiplier": 1.0,
                }
                if self._xauex_has_open_position():
                    continuation_addon_decision = build_xauex_continuation_addon_decision(
                        signal=sig,
                        open_positions=self.executor.position_manager.get_open_positions(),
                        slot=slot,
                        config=self.config,
                    )
                    if not bool(continuation_addon_decision.get("allowed")):
                        reason = str(continuation_addon_decision.get("reason") or "XAUEX_POSITION_OPEN")
                        logger.info("[XAUEX] Existing XAUEX position still open - skipping new slot: %s", reason)
                        self._mark_slot_used(
                            slot=slot,
                            signal_id=signal_id,
                            reason=reason,
                            signal_time=now_utc,
                            signal_action=action,
                            signal_confidence=confidence,
                            window_label=window_label,
                            confirm_status=confirm_status,
                            confirm_reason=confirm_reason,
                            confirm_timestamp_utc=confirm_timestamp_utc,
                            terminal=True,
                        )
                        await self.write_state()
                        continue
                    sig["continuation_addon"] = True
                    sig["continuation_parent_position_id"] = continuation_addon_decision.get("parent_position_id")
                    sig["continuation_addon_risk_multiplier"] = continuation_addon_decision.get("risk_multiplier")
                    self._journal_event(
                        "continuation_addon_candidate",
                        {
                            "slot": slot,
                            "window_label": window_label,
                            "parent_position_id": continuation_addon_decision.get("parent_position_id"),
                            "action": action,
                            "confidence": confidence,
                            "risk_multiplier": continuation_addon_decision.get("risk_multiplier"),
                        },
                        correlation_id=signal_id,
                    )
                    logger.info(
                        "[XAUEX] Continuation add-on allowed for parent=%s risk_multiplier=%.2f.",
                        str(continuation_addon_decision.get("parent_position_id") or ""),
                        _safe_signal_float(continuation_addon_decision.get("risk_multiplier"), 1.0),
                    )

                gate_result = await self._environment_gate(apply_risk_gates=True)
                if gate_result is not None:
                    if self._should_emit_repeated_xauex_log(f"environment_gate:{gate_result}"):
                        logger.info("[XAUEX] Blocked by gate: %s", gate_result)
                    self._mark_slot_used(
                        slot=slot,
                        signal_id=signal_id,
                        reason=gate_result,
                        signal_time=now_utc,
                        signal_action=action,
                        signal_confidence=confidence,
                        window_label=window_label,
                        confirm_status=confirm_status,
                        confirm_reason=confirm_reason,
                        confirm_timestamp_utc=confirm_timestamp_utc,
                        terminal=False,
                    )
                    await self.write_state()
                    continue

                bid = self.api_client._last_bid
                ask = self.api_client._last_ask
                if bid is None or ask is None:
                    logger.warning("[XAUEX] No bid/ask available yet - skipping")
                    self._mark_slot_used(
                        slot=slot,
                        signal_id=signal_id,
                        reason="NO_QUOTE",
                        signal_time=now_utc,
                        signal_action=action,
                        signal_confidence=confidence,
                        window_label=window_label,
                        confirm_status=confirm_status,
                        confirm_reason=confirm_reason,
                        confirm_timestamp_utc=confirm_timestamp_utc,
                        terminal=False,
                    )
                    await self.write_state()
                    continue

                try:
                    signal_stop_distance = float(sig.get("stop_loss_usd", sig.get("stop_loss_distance", 12.0)))
                    signal_tp_distance = float(sig.get("take_profit_usd", sig.get("take_profit_distance", 24.0)))
                except (TypeError, ValueError):
                    logger.warning("[XAUEX] Invalid stop or take-profit values in signal - skipping")
                    self._mark_slot_used(
                        slot=slot,
                        signal_id=signal_id,
                        reason="INVALID_DISTANCE_VALUES",
                        signal_time=now_utc,
                        signal_action=action,
                        signal_confidence=confidence,
                        window_label=window_label,
                        confirm_status=confirm_status,
                        confirm_reason=confirm_reason,
                        confirm_timestamp_utc=confirm_timestamp_utc,
                        terminal=True,
                    )
                    await self.write_state()
                    continue

                if signal_stop_distance <= 0 or signal_tp_distance <= 0:
                    logger.info("[XAUEX] Non-positive stop or take-profit distance - skipping")
                    self._mark_slot_used(
                        slot=slot,
                        signal_id=signal_id,
                        reason="NONPOSITIVE_DISTANCE",
                        signal_time=now_utc,
                        signal_action=action,
                        signal_confidence=confidence,
                        window_label=window_label,
                        confirm_status=confirm_status,
                        confirm_reason=confirm_reason,
                        confirm_timestamp_utc=confirm_timestamp_utc,
                        terminal=True,
                    )
                    await self.write_state()
                    continue

                atr_stop_distance = self._xauex_recent_atr_distance()
                current_price = ask if direction == 1 else bid
                structure_stop_distance = self._xauex_structure_stop_distance(direction, current_price)
                max_stop_distance = max(signal_stop_distance * 2.0, float(self.config.sl_max_dollars), 25.0)
                sl_distance = build_xauex_initial_stop_distance(
                    signal_stop=signal_stop_distance,
                    atr_stop=atr_stop_distance,
                    structure_stop=structure_stop_distance,
                    min_stop=float(self.config.sl_min_dollars),
                    max_stop=max_stop_distance,
                )
                wide_spread_threshold = float(getattr(self.config, "xauex_wide_spread_usd", 0.80))
                wide_spread_multiplier = float(getattr(self.config, "xauex_wide_spread_sl_multiplier", 1.20))
                spread_for_widen = self._safe_current_spread()
                if spread_for_widen >= wide_spread_threshold:
                    widened = round(min(sl_distance * wide_spread_multiplier, max_stop_distance), 2)
                    if widened > sl_distance:
                        logger.info(
                            "[XAUEX] Wide spread %.2f >= %.2f - expanding SL %.2f -> %.2f to absorb whipsaw",
                            spread_for_widen,
                            wide_spread_threshold,
                            sl_distance,
                            widened,
                        )
                        sl_distance = widened
                tp_distance = build_xauex_take_profit_distance(
                    signal_take_profit=signal_tp_distance,
                    stop_distance=sl_distance,
                    assurance=assurance,
                )

                if direction == 1:
                    stop_loss_price = current_price - sl_distance
                    take_profit_price = current_price + tp_distance
                else:
                    stop_loss_price = current_price + sl_distance
                    take_profit_price = current_price - tp_distance

                # Reject trades when the stop would sit inside or within reach of
                # the current bid/ask. bot/risk/sizing.calculate_lot_size enforces
                # the same 3x-spread rule, but the XAUEX cash-risk sizer does not,
                # so we guard the XAUEX path here to match.
                current_spread = self._safe_current_spread()
                if current_spread > 0 and sl_distance < current_spread * 3.0:
                    logger.info(
                        "[XAUEX] Stop distance %.2f is below 3x current spread %.2f - skipping",
                        sl_distance,
                        current_spread,
                    )
                    self._mark_slot_used(
                        slot=slot,
                        signal_id=signal_id,
                        reason="SPREAD_TOO_WIDE",
                        signal_time=now_utc,
                        signal_action=action,
                        signal_confidence=confidence,
                        window_label=window_label,
                        confirm_status=confirm_status,
                        confirm_reason=confirm_reason,
                        confirm_timestamp_utc=confirm_timestamp_utc,
                        terminal=False,
                    )
                    await self.write_state()
                    continue

                cash_risk_budget = self._xauex_cash_risk_budget()
                if cash_risk_budget <= 0:
                    logger.info("[XAUEX] Cash risk budget is non-positive - skipping")
                    self._mark_slot_used(
                        slot=slot,
                        signal_id=signal_id,
                        reason="NO_RISK_BUDGET",
                        signal_time=now_utc,
                        signal_action=action,
                        signal_confidence=confidence,
                        window_label=window_label,
                        confirm_status=confirm_status,
                        confirm_reason=confirm_reason,
                        confirm_timestamp_utc=confirm_timestamp_utc,
                        terminal=False,
                    )
                    await self.write_state()
                    continue

                cooldown = self._xauex_loss_cooldown_multiplier()
                if cooldown < 1.0:
                    logger.info(
                        "[XAUEX] Post-loss cooldown active: risk scaled by %.2fx (consecutive losses=%d)",
                        cooldown,
                        int(getattr(self.risk_gates.state, "consecutive_losses_today", 0) or 0),
                    )
                session_slot_multiplier = self._xauex_session_slot_multiplier(slot)
                if session_slot_multiplier < 1.0:
                    logger.info(
                        "[XAUEX] Session slot multiplier %.2fx applied for slot=%s",
                        session_slot_multiplier,
                        slot,
                    )
                counter_signal_risk_multiplier = 1.0
                if bool(sig.get("counter_signal")):
                    counter_signal_risk_multiplier = round(
                        max(
                            0.0,
                            min(
                                1.0,
                                _safe_signal_float(
                                    sig.get(
                                        "counter_signal_risk_multiplier",
                                        getattr(self.config, "xauex_counter_signal_risk_multiplier", 0.5),
                                    ),
                                    0.5,
                                ),
                            ),
                        ),
                        2,
                    )
                    logger.info(
                        "[XAUEX] Counter-signal risk multiplier %.2fx applied.",
                        counter_signal_risk_multiplier,
                    )
                microstructure_soft_confirmed = str(sig.get("confirm_reason", "")).upper().startswith(
                    "MICROSTRUCTURE_SOFT_CONFIRMED"
                )
                microstructure_risk_multiplier = 1.0
                if microstructure_soft_confirmed:
                    microstructure_risk_multiplier = _XAUEX_MICROSTRUCTURE_SOFT_RISK_MULTIPLIER
                    logger.info(
                        "[XAUEX] Microstructure soft-confirm risk multiplier %.2fx applied.",
                        microstructure_risk_multiplier,
                    )
                continuation_addon_risk_multiplier = 1.0
                if bool(sig.get("continuation_addon")):
                    continuation_addon_risk_multiplier = round(
                        max(
                            0.05,
                            min(
                                1.0,
                                _safe_signal_float(
                                    sig.get(
                                        "continuation_addon_risk_multiplier",
                                        getattr(self.config, "xauex_continuation_addon_risk_multiplier", 0.5),
                                    ),
                                    0.5,
                                ),
                            ),
                        ),
                        2,
                    )
                    logger.info(
                        "[XAUEX] Continuation add-on risk multiplier %.2fx applied.",
                        continuation_addon_risk_multiplier,
                    )
                minimum_executable_risk = round(
                    float(self.symbol_spec.volume_min) * float(self.symbol_spec.lot_size) * sl_distance,
                    2,
                )
                canary_decision = build_xauex_min_lot_canary_decision(
                    signal=sig,
                    assurance=assurance,
                    cash_risk_budget=cash_risk_budget,
                    minimum_executable_risk=minimum_executable_risk,
                    canaries_used_today=self._xauex_min_lot_canaries_today(now_utc),
                    config=self.config,
                )
                counter_signal_canary_only = bool(sig.get("counter_signal")) and bool(
                    getattr(self.config, "xauex_counter_signal_canary_only", True)
                )
                stale_or_warning_context = _xauex_has_stale_or_warning_context(sig)
                allow_minimum_executable_risk_lift = (
                    assurance.risk_multiplier >= 1.0
                    and not counter_signal_canary_only
                    and not stale_or_warning_context
                    or bool(canary_decision.get("allow_minimum_executable_risk_lift"))
                )
                assurance_cash_risk = calculate_xauex_assurance_cash_risk(
                    cash_risk_budget=cash_risk_budget,
                    assurance_risk_multiplier=assurance.risk_multiplier,
                    cooldown_multiplier=cooldown,
                    session_slot_multiplier=session_slot_multiplier,
                    counter_signal_risk_multiplier=counter_signal_risk_multiplier,
                    microstructure_risk_multiplier=microstructure_risk_multiplier,
                    entry_quality_risk_multiplier=entry_quality_risk_multiplier,
                    continuation_addon_risk_multiplier=continuation_addon_risk_multiplier,
                    minimum_executable_risk=minimum_executable_risk,
                    allow_minimum_executable_risk_lift=allow_minimum_executable_risk_lift,
                    force_minimum_executable_risk=counter_signal_canary_only,
                )
                minimum_lot_canary = (
                    (bool(canary_decision.get("allowed")) or counter_signal_canary_only)
                    and minimum_executable_risk > 0
                    and assurance_cash_risk == minimum_executable_risk
                )
                if minimum_lot_canary:
                    logger.info(
                        "[XAUEX] Minimum-lot canary enabled for %s: min_risk=%.2f budget=%.2f used=%s/%s",
                        signal_id,
                        minimum_executable_risk,
                        cash_risk_budget,
                        canary_decision.get("canaries_used_today"),
                        canary_decision.get("max_per_day"),
                    )
                lot = calculate_xauex_lot_size_from_cash_risk(
                    cash_risk=assurance_cash_risk,
                    stop_distance=sl_distance,
                    lot_size=float(self.symbol_spec.lot_size),
                    volume_step=float(self.symbol_spec.volume_step),
                    volume_min=float(self.symbol_spec.volume_min),
                    volume_max=float(self.symbol_spec.volume_max),
                    max_lot_size=float(getattr(self.config, "max_lot_size", self.symbol_spec.volume_max)),
                )
                if lot is None:
                    logger.info(
                        "[XAUEX] Assurance risk budget %.2f below broker minimum for stop %.2f - skipping",
                        assurance_cash_risk,
                        sl_distance,
                    )
                    self._mark_slot_used(
                        slot=slot,
                        signal_id=signal_id,
                        reason="ASSURANCE_RISK_BELOW_MIN_LOT",
                        signal_time=now_utc,
                        signal_action=action,
                        signal_confidence=confidence,
                        window_label=window_label,
                        confirm_status=confirm_status,
                        confirm_reason=confirm_reason,
                        confirm_timestamp_utc=confirm_timestamp_utc,
                        terminal=True,
                        blocked_trade={
                            "entry_price": round(current_price, 2),
                            "stop_loss": round(stop_loss_price, 2),
                            "take_profit": round(take_profit_price, 2),
                            "sl_distance": round(sl_distance, 2),
                            "tp_distance": round(tp_distance, 2),
                            "cash_risk_budget": cash_risk_budget,
                            "assurance_cash_risk": assurance_cash_risk,
                            "minimum_executable_risk": minimum_executable_risk,
                            "assurance_bucket": assurance.bucket,
                            "assurance_score": assurance.score,
                            "assurance_reason": assurance.reason,
                            "canary_decision": canary_decision,
                        },
                    )
                    await self.write_state()
                    continue

                reasoning = sig.get("reasoning", "XAUEX signal")
                dir_label = "LONG" if direction == 1 else "SHORT"
                actual_cash_risk = round(lot * float(self.symbol_spec.lot_size) * sl_distance, 2)
                remaining_daily_risk = self._xauex_remaining_daily_loss_budget()
                if actual_cash_risk > remaining_daily_risk:
                    logger.info(
                        "[XAUEX] Remaining daily loss budget %.2f is below proposed risk %.2f - skipping",
                        remaining_daily_risk,
                        actual_cash_risk,
                    )
                    self._mark_slot_used(
                        slot=slot,
                        signal_id=signal_id,
                        reason="REMAINING_DAILY_RISK_EXCEEDED",
                        signal_time=now_utc,
                        signal_action=action,
                        signal_confidence=confidence,
                        window_label=window_label,
                        confirm_status=confirm_status,
                        confirm_reason=confirm_reason,
                        confirm_timestamp_utc=confirm_timestamp_utc,
                        terminal=True,
                        blocked_trade={
                            "entry_price": round(current_price, 2),
                            "stop_loss": round(stop_loss_price, 2),
                            "take_profit": round(take_profit_price, 2),
                            "lot": lot,
                            "sl_distance": round(sl_distance, 2),
                            "tp_distance": round(tp_distance, 2),
                            "actual_cash_risk": actual_cash_risk,
                            "remaining_daily_risk": remaining_daily_risk,
                            "cash_risk_budget": cash_risk_budget,
                            "minimum_executable_risk": minimum_executable_risk,
                            "minimum_lot_canary": minimum_lot_canary,
                            "canary_decision": canary_decision,
                        },
                    )
                    await self.write_state()
                    continue
                target_cash_reward = round(lot * float(self.symbol_spec.lot_size) * tp_distance, 2)
                # Capture the signal context that produced this trade so the
                # post-trade journal and weekly review can attribute outcomes
                # to confidence, validator opinion, regime filter, and macro
                # snapshot freshness rather than treating every closed trade
                # as a context-free event.
                decision_packet_for_meta = sig.get("decision_packet") or {}
                price_features_for_meta = (decision_packet_for_meta.get("price_features") or {}) if isinstance(decision_packet_for_meta, dict) else {}
                input_freshness_for_meta = (decision_packet_for_meta.get("input_freshness") or {}) if isinstance(decision_packet_for_meta, dict) else {}
                signal_context = {
                    "signal_action": str(sig.get("action") or "").upper(),
                    "signal_confidence": round(float(sig.get("confidence") or 0.0), 4),
                    "validator_status": str(sig.get("validator_status") or ""),
                    "validator_summary": str(sig.get("validator_summary") or "")[:280],
                    "consensus_state": str(sig.get("consensus_state") or ""),
                    "decision_mode": str(sig.get("decision_mode") or ""),
                    "daily_trend_bias": price_features_for_meta.get("daily_trend_bias"),
                    "range_position": price_features_for_meta.get("range_position"),
                    "regime_filter": price_features_for_meta.get("regime_filter"),
                    "market_snapshot_age_seconds": input_freshness_for_meta.get("market_snapshot_age_seconds"),
                    "market_snapshot_state": input_freshness_for_meta.get("market_snapshot_state"),
                }
                session_metadata = {
                    "session": {
                        "phase": "OBSERVE",
                        "direction": dir_label,
                        "entry_price": round(current_price, 2),
                        "initial_risk_distance": round(sl_distance, 2),
                        "confidence": round(confidence, 2),
                        "confidence_bucket": assurance.bucket,
                        "assurance_score": assurance.score,
                        "assurance_reason": assurance.reason,
                        "risk_multiplier": assurance.risk_multiplier,
                        "cooldown_multiplier": cooldown,
                        "session_slot_multiplier": session_slot_multiplier,
                        "counter_signal": bool(sig.get("counter_signal")),
                        "counter_signal_risk_multiplier": counter_signal_risk_multiplier,
                        "counter_signal_canary_only": counter_signal_canary_only,
                        "counter_source_action": str(sig.get("counter_source_action") or ""),
                        "counter_source_confirm_reason": str(sig.get("counter_source_confirm_reason") or ""),
                        "stale_or_warning_context": stale_or_warning_context,
                        "entry_quality_policy": entry_quality,
                        "entry_quality_risk_multiplier": entry_quality_risk_multiplier,
                        "continuation_addon": bool(sig.get("continuation_addon")),
                        "continuation_parent_position_id": str(sig.get("continuation_parent_position_id") or ""),
                        "continuation_addon_risk_multiplier": continuation_addon_risk_multiplier,
                        "minimum_lot_canary": minimum_lot_canary,
                        "minimum_lot_canary_reason": str(canary_decision.get("reason") or ""),
                        "minimum_lot_canary_used_today": canary_decision.get("canaries_used_today"),
                        "allowed_cash_risk": assurance_cash_risk,
                        "actual_cash_risk": actual_cash_risk,
                        "target_rr": assurance.target_rr,
                        "target_cash_reward": target_cash_reward,
                        "protect_r": assurance.protect_r,
                        "trail_r": assurance.trail_r,
                        "protect_lock_r": assurance.protect_lock_r,
                        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "signal_id": signal_id,
                        "window_label": window_label,
                        "confirm_status": confirm_status,
                        "confirm_reason": confirm_reason,
                        "confirm_timestamp_utc": confirm_timestamp_utc,
                    },
                    "signal_context": signal_context,
                    # Top-level mirrors so the journal/weekly review can read
                    # them without traversing the metadata dict.
                    "signal_confidence": signal_context["signal_confidence"],
                    "consensus_state": signal_context["consensus_state"],
                    "validator_status": signal_context["validator_status"],
                }
                logger.info(
                    "[XAUEX] Executing %s %s | Lot:%.2f assurance=%s score=%.2f risk_budget:%.2f/%.2f actual_risk:%.2f target_rr:%.2f SL:%.2f TP:%.2f signal_sl:%.2f atr_sl:%.2f structure_sl:%.2f | Confidence:%.2f | %s",
                    signal_symbol or live_symbol or "XAUUSD",
                    dir_label,
                    lot,
                    assurance.bucket,
                    assurance.score,
                    assurance_cash_risk,
                    cash_risk_budget,
                    actual_cash_risk,
                    assurance.target_rr,
                    stop_loss_price,
                    take_profit_price,
                    signal_stop_distance,
                    atr_stop_distance,
                    structure_stop_distance,
                    confidence,
                    reasoning,
                )

                placement_pattern = (
                    pattern_evidence.pattern
                    if pattern_evidence.factor == "PATTERN_MATCH"
                    else PatternType.NONE
                )
                placement_level = pattern_evidence.level if pattern_evidence.level is not None else current_price

                pos_id = await self.executor.place_market_order(
                    direction=direction,
                    lot_size=lot,
                    stop_loss_price=stop_loss_price,
                    take_profit_price=take_profit_price,
                    pattern=placement_pattern,
                    level=placement_level,
                    owner="xauex",
                    metadata=session_metadata,
                )

                if pos_id:
                    logger.info("[XAUEX] Order placed: position_id=%s", pos_id)
                    self._record_xauex_trade(now_utc)
                    await self.write_state()
                    if not str(pos_id).startswith("order:"):
                        self._append_trade_entry_on_chart(str(pos_id), dir_label, current_price, "xauex")
                else:
                    logger.warning("[XAUEX] Order placement returned None")

                self._mark_slot_used(
                    slot=slot,
                    signal_id=signal_id,
                    reason="ORDER_PLACED" if pos_id is not None else "ORDER_NOT_PLACED",
                    signal_time=now_utc,
                    signal_action=action,
                    signal_confidence=confidence,
                    window_label=window_label,
                    confirm_status=confirm_status,
                    confirm_reason=confirm_reason,
                    confirm_timestamp_utc=confirm_timestamp_utc,
                    terminal=True,
                    minimum_lot_canary=minimum_lot_canary and pos_id is not None,
                    counter_signal=bool(sig.get("counter_signal")) and pos_id is not None,
                    policy_factors=list(entry_quality.get("policy_factors", [])),
                    hard_block_score=int(entry_quality.get("hard_block_score", 0) or 0),
                    pattern_evidence=pattern_evidence.to_dict(),
                )
                await self.write_state()
            except Exception:
                logger.exception(
                    "[XAUEX] Signal poll iteration crashed for slot=%s signal_id=%s; keeping poller alive.",
                    slot,
                    signal_id,
                )
                self._mark_slot_used(
                    slot=slot,
                    signal_id=signal_id,
                    reason="EXECUTION_EXCEPTION",
                    signal_time=now_utc,
                    signal_action=action,
                    signal_confidence=confidence,
                    window_label=window_label,
                    confirm_status=confirm_status,
                    confirm_reason=confirm_reason,
                    confirm_timestamp_utc=confirm_timestamp_utc,
                    terminal=False,
                )
                await self.write_state()
                continue

    async def _monitor_xauex_positions(self) -> None:
        """Close XAUEX positions on cash TP/SL or at the London force-flat time."""
        while self.running:
            await asyncio.sleep(10)
            try:
                if self.api_client is None or self.executor is None:
                    continue

                try:
                    positions = await self.api_client.get_open_positions()
                except Exception as exc:
                    logger.warning("[XAUEX] Position monitor refresh failed: %s", exc)
                    continue

                open_ids = {position.position_id for position in positions}
                self._clear_stale_close_requests(open_position_ids=open_ids, now_utc=datetime.now(timezone.utc))
                self.risk_gates.set_open_position_count(count_tradeable_open_positions(positions))

                if not positions:
                    continue

                now_utc = datetime.now(timezone.utc)
                force_flat_due = self._xauex_force_flat_due(now_utc)

                for position in positions:
                    try:
                        if position.position_id in self._xauex_close_requested:
                            continue

                        tracked = self.executor.position_manager.get_position(position.position_id)
                        if tracked is not None:
                            tracked.unrealised_pnl = position.unrealised_pnl
                        if tracked is None:
                            logger.warning(
                                "[XAUEX] Broker position %s not found in local tracker; skipping XAUEX management.",
                                position.position_id,
                            )
                            continue
                        if tracked.owner != "xauex":
                            continue

                        session = tracked.metadata.setdefault("session", {})
                        session.setdefault("phase", "OBSERVE")
                        session.setdefault("direction", tracked.direction)
                        session.setdefault("entry_price", tracked.entry_price)
                        session.setdefault("initial_risk_distance", abs(tracked.entry_price - tracked.stop_loss))
                        session.setdefault("confidence_bucket", "medium")
                        session.setdefault("protect_r", float(self.config.xauex_session_protect_r))
                        session.setdefault("trail_r", float(self.config.xauex_session_trail_r))
                        session.setdefault("protect_lock_r", float(self.config.xauex_session_protect_lock_r))

                        candidate_session = advance_xauex_session_phase(
                            session,
                            current_price=position.current_price,
                            protect_r=float(session.get("protect_r", self.config.xauex_session_protect_r)),
                            trail_r=float(session.get("trail_r", self.config.xauex_session_trail_r)),
                        )
                        updated_session = confirm_xauex_session_phase_transition(
                            session,
                            candidate_session,
                            unrealised_pnl=position.unrealised_pnl,
                            lot_size=float(getattr(tracked, "lot_size", 0.0) or 0.0),
                            contract_size=float(getattr(self.symbol_spec, "lot_size", 0.0) or 0.0),
                        )
                        tracked.metadata["session"] = updated_session

                        new_stop_loss: Optional[float] = None
                        previous_phase = str(session.get("phase", "OBSERVE")).upper()
                        current_phase = str(updated_session.get("phase", previous_phase)).upper()
                        correlation_id = str(updated_session.get("signal_id") or position.position_id)
                        if current_phase != previous_phase:
                            self._journal_event(
                                "session_phase_transition",
                                {
                                    "position_id": position.position_id,
                                    "previous_phase": previous_phase,
                                    "current_phase": current_phase,
                                    "progress_r": updated_session.get("progress_r"),
                                },
                                correlation_id=correlation_id,
                            )
                        if current_phase == "PROTECT" and previous_phase == "OBSERVE":
                            new_stop_loss = self._xauex_protect_stop_price(
                                direction=tracked.direction,
                                entry_price=tracked.entry_price,
                                initial_risk_distance=float(updated_session.get("initial_risk_distance", 0.0) or 0.0),
                                lock_r=float(updated_session.get("protect_lock_r", self.config.xauex_session_protect_lock_r) or 0.0),
                            )
                        elif current_phase == "TRAIL":
                            new_stop_loss = self._xauex_trailing_stop_price(
                                direction=tracked.direction,
                                current_price=position.current_price,
                                confidence_bucket=str(updated_session.get("confidence_bucket", "medium")),
                            )

                        if (
                            new_stop_loss is not None
                            and self.executor.validate_sl_modification(tracked, new_stop_loss)
                            and self.executor.validate_sl_against_market(tracked, new_stop_loss, self.symbol_spec)
                        ):
                            amended = await self.api_client.amend_position_sltp(
                                position_id=position.position_id,
                                stop_loss=new_stop_loss,
                                take_profit=tracked.take_profit,
                            )
                            if amended:
                                tracked.stop_loss = new_stop_loss
                                self._journal_event(
                                    "stop_updated",
                                    {
                                        "position_id": position.position_id,
                                        "stop_loss": new_stop_loss,
                                        "take_profit": tracked.take_profit,
                                        "phase": current_phase,
                                    },
                                    correlation_id=correlation_id,
                                )

                        close_reason: Optional[str] = None
                        cash_take_profit_threshold = self._xauex_cash_take_profit_threshold(updated_session)
                        if force_flat_due:
                            close_reason = "FORCE_FLAT_LONDON"
                        elif position.unrealised_pnl >= cash_take_profit_threshold:
                            close_reason = f"CASH_TP_GBP_{cash_take_profit_threshold:.2f}"
                        elif position.unrealised_pnl <= -self.config.xauex_cash_stop_loss_gbp:
                            close_reason = f"CASH_SL_GBP_{self.config.xauex_cash_stop_loss_gbp:.2f}"

                        if close_reason is None:
                            continue

                        logger.info(
                            "[XAUEX] Closing position %s reason=%s pnl=%.2f volume=%.2f",
                            position.position_id,
                            close_reason,
                            position.unrealised_pnl,
                            position.volume,
                        )

                        closed = await self.api_client.close_position(
                            position_id=position.position_id,
                            volume_lots=position.volume,
                        )
                        if closed:
                            self._xauex_close_requested[position.position_id] = now_utc
                    except Exception:
                        logger.exception(
                            "[XAUEX] Position monitor failed for position %s; keeping monitor alive.",
                            getattr(position, "position_id", "unknown"),
                        )
                        continue
            except Exception:
                logger.exception("[XAUEX] Position monitor iteration crashed; keeping monitor alive.")

    async def _poll_manual_trade_commands(self) -> None:
        """Poll the separate manual trade command file and execute manual-only actions."""
        command_path = Path(self.config.xauex_manual_command_path)
        while self.running:
            await asyncio.sleep(2)
            if self.api_client is None or self.executor is None:
                continue
            command_result = consume_manual_trade_command(
                command_path,
                secret=self.config.xauex_manual_command_secret,
                seen_command_ids=self._manual_command_ids,
                journal_path=self.config.xauex_event_journal_path,
                replay_guard=self._manual_replay_guard,
            )
            if not command_result.file_found:
                continue
            if command_result.payload is None:
                self._manual_trade_status = {
                    "ok": False,
                    "reason": command_result.rejection_reason or "invalid manual command",
                    "command_id": command_result.command_id,
                    "updated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                logger.warning("[MANUAL] Rejected command envelope: %s", command_result.rejection_reason)
                await self.write_state()
                continue
            payload = command_result.payload
            command_id = command_result.command_id

            valid, reason = validate_manual_trade_command(payload)
            if not valid:
                self._manual_trade_status = {
                    "ok": False,
                    "reason": reason,
                    "command_id": command_id,
                    "updated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                logger.warning("[MANUAL] Rejected command: %s", reason)
                self._journal_event(
                    "manual_command_rejected",
                    {"command_id": command_id, "reason": reason, "payload": payload},
                    correlation_id=command_id,
                )
                await self.write_state()
                continue

            command = str(payload.get("command", "open") or "open").lower()
            if command == "close":
                position_id = str(payload.get("position_id", "") or "")
                tracked = self.executor.position_manager.get_position(position_id)
                if tracked is None or tracked.owner != "manual":
                    self._manual_trade_status = {
                        "ok": False,
                        "reason": f"manual position {position_id} not found",
                        "command_id": command_id,
                        "updated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    }
                    logger.warning("[MANUAL] Manual close rejected for position %s", position_id)
                    self._journal_event(
                        "manual_command_rejected",
                        {"command_id": command_id, "reason": "POSITION_NOT_FOUND", "position_id": position_id},
                        correlation_id=command_id,
                    )
                    await self.write_state()
                    continue
                closed = await self.api_client.close_position(position_id=position_id, volume_lots=tracked.lot_size)
                self._manual_trade_status = {
                    "ok": bool(closed),
                    "command": "close",
                    "position_id": position_id,
                    "command_id": command_id,
                    "updated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                self._journal_event(
                    "manual_close_requested",
                    {"command_id": command_id, "position_id": position_id, "ok": bool(closed)},
                    correlation_id=command_id,
                )
                await self.write_state()
                continue

            action = str(payload.get("action")).upper()
            lot_size = float(payload.get("lot_size"))
            stop_loss = float(payload.get("stop_loss"))
            take_profit = float(payload.get("take_profit"))
            block_reason = manual_trade_global_block_reason(
                kill_switch_active=bool(self.kill_switch_active),
                auth_failure=bool(self.executor.HALTED_AUTH_FAILURE),
            )
            if block_reason is not None:
                self._manual_trade_status = {
                    "ok": False,
                    "reason": block_reason,
                    "command_id": command_id,
                    "updated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                logger.warning("[MANUAL] Rejected open command: %s", block_reason)
                self._journal_event(
                    "manual_command_rejected",
                    {"command_id": command_id, "reason": block_reason, "payload": payload},
                    correlation_id=command_id,
                )
                await self.write_state()
                continue
            bid, ask = self.api_client.get_current_quote()
            current_price = ask if action == "BUY" else bid
            if current_price is None:
                self._manual_trade_status = {
                    "ok": False,
                    "reason": "price unavailable",
                    "command_id": command_id,
                    "updated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                self._journal_event(
                    "manual_command_rejected",
                    {"command_id": command_id, "reason": "PRICE_UNAVAILABLE", "payload": payload},
                    correlation_id=command_id,
                )
                await self.write_state()
                continue
            valid_prices, price_reason = validate_manual_trade_prices(payload, current_price=current_price)
            if not valid_prices:
                self._manual_trade_status = {
                    "ok": False,
                    "reason": price_reason,
                    "command_id": command_id,
                    "updated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                logger.warning("[MANUAL] Rejected open command: %s", price_reason)
                self._journal_event(
                    "manual_command_rejected",
                    {"command_id": command_id, "reason": price_reason, "payload": payload},
                    correlation_id=command_id,
                )
                await self.write_state()
                continue

            pos_id = await self.executor.place_market_order(
                direction=1 if action == "BUY" else -1,
                lot_size=lot_size,
                stop_loss_price=stop_loss,
                take_profit_price=take_profit,
                pattern=PatternType.NONE,
                level=current_price,
                owner="manual",
                metadata={"manual_command": dict(payload), "correlation_id": command_id, "command_id": command_id},
            )
            if pos_id and not str(pos_id).startswith("order:"):
                self._append_trade_entry_on_chart(str(pos_id), "LONG" if action == "BUY" else "SHORT", current_price, "manual")
            self._manual_trade_status = {
                "ok": bool(pos_id),
                "command": "open",
                "action": action,
                "position_id": pos_id,
                "command_id": command_id,
                "updated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            await self.write_state()

    async def _poll_account_snapshot(self) -> None:
        """Refresh account balance/equity periodically so the dashboard stays live."""
        while self.running:
            await asyncio.sleep(15)
            if self.api_client is None:
                continue
            try:
                await self._refresh_account_snapshot()
                await self.write_state()
            except Exception as exc:
                logger.warning("[ACCOUNT] Refresh failed: %s", exc)

    async def _poll_dashboard_state_refresh(self) -> None:
        """Refresh the state file from in-memory quote/status data without extra broker API calls."""
        while self.running:
            await asyncio.sleep(5)
            try:
                self._refresh_latest_quote_snapshot()
                await self.write_state()
            except Exception as exc:
                logger.warning("[STATE] Dashboard refresh failed: %s", exc)

    async def _check_token_refresh(self) -> None:
        expiry = getattr(self.config, "ctrader_token_expiry", 0)
        now_ts = datetime.now(timezone.utc).timestamp()
        if expiry and (expiry - now_ts) < 300:
            logger.info("[TOKEN] Refreshing OAuth token...")
            from auth import refresh_token
            await refresh_token(self.config)

    async def _restore_risk_state(self) -> None:
        """Restore RiskState from dedicated risk_state.json (survives restarts)."""
        restored = await load_risk_state(self.config.state_file_path)
        if restored is not None:
            self.risk_state = restored
            logger.info("[STARTUP] Risk state restored from risk_state.json.")

    def _risk_state_wiring_error(self) -> Optional[str]:
        """Return an explicit fault code when risk accounting references diverge."""
        if self.executor is None or self.risk_gates is None:
            return "RISK_STATE_WIRING_INVALID"
        if self.executor.risk_gates is not self.risk_gates:
            return "RISK_STATE_WIRING_INVALID"
        if self.risk_gates.state is not self.risk_state:
            return "RISK_STATE_WIRING_INVALID"
        if self.executor.risk_gates.state is not self.risk_state:
            return "RISK_STATE_WIRING_INVALID"
        return None

    async def _on_execution_event(self, event) -> None:
        """Synchronize local state from unsolicited broker execution events."""
        if self.executor is None or self.risk_gates is None or self.symbol_spec is None:
            return

        try:
            if getattr(event, "executionType", None) != 3:  # ORDER_FILLED
                return

            should_refresh_account = False
            if event.HasField("deal") and event.deal.HasField("closePositionDetail"):
                close_detail = event.deal.closePositionDetail
                money_digits = getattr(close_detail, "moneyDigits", 2) or 2
                pnl = close_detail.grossProfit / (10 ** money_digits)
                await self.executor.on_position_closed(
                    position_id=str(event.deal.positionId),
                    close_price=event.deal.executionPrice,
                    pnl=pnl,
                )
                should_refresh_account = True
            elif event.HasField("position"):
                position = event.position
                position_id = str(position.positionId)
                order_id = str(event.order.orderId) if event.HasField("order") else None
                pending_market = self.executor.consume_pending_market_order(order_id)
                tracked = self.executor.position_manager.get_position(position_id)

                if tracked is None:
                    direction = (pending_market or {}).get("direction") or ("LONG" if position.tradeData.tradeSide == 1 else "SHORT")
                    lot_size = (pending_market or {}).get("lot_size")
                    if lot_size is None:
                        lot_size = position.tradeData.volume / (self.symbol_spec.lot_size * 100)
                    tracked = TrackedPosition(
                        position_id=position_id,
                        direction=direction,
                        entry_price=position.price,
                        stop_loss=(pending_market or {}).get("stop_loss", position.stopLoss if position.HasField("stopLoss") else 0.0),
                        take_profit=(pending_market or {}).get("take_profit", position.takeProfit if position.HasField("takeProfit") else 0.0),
                        lot_size=lot_size,
                        open_time_utc=datetime.fromtimestamp(
                            position.tradeData.openTimestamp / 1000,
                            tz=timezone.utc,
                        ),
                        pattern=(pending_market or {}).get("pattern", PatternType.NONE),
                        level=(pending_market or {}).get("level", position.price),
                        owner=(pending_market or {}).get("owner", "strategy"),
                        metadata=dict((pending_market or {}).get("metadata") or {}),
                    )
                    self.executor.position_manager.add(tracked)
                    self._append_trade_entry_on_chart(position_id, tracked.direction, tracked.entry_price, tracked.owner)
                else:
                    tracked.entry_price = position.price
                    if position.HasField("stopLoss"):
                        tracked.stop_loss = position.stopLoss
                    elif pending_market is not None:
                        tracked.stop_loss = pending_market.get("stop_loss", tracked.stop_loss)
                    if position.HasField("takeProfit"):
                        tracked.take_profit = position.takeProfit
                    elif pending_market is not None:
                        tracked.take_profit = pending_market.get("take_profit", tracked.take_profit)
                    if pending_market is not None:
                        tracked.pattern = pending_market.get("pattern", tracked.pattern)
                        tracked.level = pending_market.get("level", tracked.level)
                        tracked.owner = pending_market.get("owner", tracked.owner)
                        tracked.metadata.update(dict(pending_market.get("metadata") or {}))

                if order_id is not None:
                    await self.executor.on_order_filled(
                        order_id=order_id,
                        position_id=position_id,
                        entry_price=tracked.entry_price,
                    )
                should_refresh_account = True
            else:
                return

            if should_refresh_account:
                await self._refresh_account_snapshot()

            self.risk_gates.set_open_position_count(
                count_tradeable_open_positions(self.executor.position_manager.get_open_positions())
            )
            await self.write_state()
        except Exception as exc:
            logger.error("[EXECUTION EVENT] Failed to process broker event: %s", exc)

    async def _refresh_account_snapshot(self) -> None:
        """Refresh balance/equity/currency from broker after realised P&L events."""
        if self.api_client is None:
            return
        account_data = await self.api_client.get_account()
        self.account = {
            "balance": account_data.balance,
            "equity": account_data.equity,
            "currency": account_data.currency,
            "open_pnl": account_data.open_pnl,
        }


# ──────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────

async def main() -> None:
    config = load_config()

    # File logging — rotating, 10 MB × 5 backups, written from a background
    # thread via QueueHandler so log calls never block the asyncio event loop.
    log_dir = os.path.dirname(config.log_file_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        config.log_file_path, maxBytes=10 * 1024 * 1024, backupCount=5,
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))

    log_queue: queue.SimpleQueue = queue.SimpleQueue()
    queue_handler = logging.handlers.QueueHandler(log_queue)
    queue_listener = logging.handlers.QueueListener(
        log_queue, file_handler, respect_handler_level=True
    )
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(queue_handler)
    queue_listener.start()

    orchestrator = BotOrchestrator(config)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig,
            lambda: asyncio.create_task(orchestrator.shutdown()),
        )

    try:
        await orchestrator.startup()
        await orchestrator.main_loop()
    except SystemExit:
        raise
    except Exception:
        logger.exception("[FATAL] Unhandled exception — exiting for systemd restart.")
        sys.exit(1)
    finally:
        queue_listener.stop()


if __name__ == "__main__":
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
        )
    asyncio.run(main())
