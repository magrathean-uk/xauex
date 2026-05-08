# ruff: noqa: E402
"""Post-trade journal must surface the signal-context fields lifted by the
executor onto closed_trades_today entries."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from xauex.analyst.post_trade_journal import build_trade_prompt


def _trade(**overrides):
    base = {
        "position_id": "p-1",
        "direction": "LONG",
        "entry_price": 4720.0,
        "close_price": 4730.0,
        "stop_loss": 4700.0,
        "take_profit": 4750.0,
        "lot_size": 0.01,
        "pnl": 10.0,
        "pattern": "BULLISH_ENGULFING",
        "level": 4720.0,
        "close_time_utc": "2026-05-08T14:00:00Z",
    }
    base.update(overrides)
    return base


def test_build_trade_prompt_omits_signal_context_block_when_absent():
    prompt = build_trade_prompt(_trade())
    assert "SIGNAL CONTEXT" not in prompt


def test_build_trade_prompt_includes_signal_context_block_when_present():
    trade = _trade(
        signal_confidence=0.62,
        consensus_state="aligned",
        validator_status="reviewed",
        regime_filter="TREND_ALIGNED_UPPER_THIRD_KEPT_BUY",
        daily_trend_bias=1,
        range_position="UPPER_THIRD",
        market_snapshot_age_seconds=4200,
    )
    prompt = build_trade_prompt(trade)
    assert "SIGNAL CONTEXT" in prompt
    assert "0.62" in prompt
    assert "aligned" in prompt
    assert "TREND_ALIGNED_UPPER_THIRD_KEPT_BUY" in prompt
    assert "UPPER_THIRD" in prompt


def test_build_trade_prompt_handles_partial_signal_context():
    trade = _trade(signal_confidence=0.48)
    prompt = build_trade_prompt(trade)
    assert "SIGNAL CONTEXT" in prompt
    assert "0.48" in prompt
