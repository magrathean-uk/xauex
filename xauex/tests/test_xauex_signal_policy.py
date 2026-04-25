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

from bot.api.models import SymbolSpec
from xauex.signal.assets import resolve_asset
from xauex.signal.signal_parser import _fallback_direction, _normalize_signal

BotOrchestrator = _MODULE.BotOrchestrator


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
