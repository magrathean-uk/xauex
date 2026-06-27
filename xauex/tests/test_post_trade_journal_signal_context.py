# ruff: noqa: E402
"""Post-trade journal must surface the signal-context fields lifted by the
executor onto closed_trades_today entries."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from xauex.analyst import post_trade_journal
from xauex.analyst.post_trade_journal import build_trade_prompt
from xauex.shared.event_journal import append_event


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


def test_run_journals_event_sourced_close_when_state_closed_trades_empty(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    cursor_path = tmp_path / "cursor.json"
    journal_path = tmp_path / "journal.json"
    event_path = tmp_path / "events.jsonl"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path.write_text(
        json.dumps({"meta": {"last_updated_utc": now}, "closed_trades_today": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(post_trade_journal, "call_claude", lambda prompt, model: "journaled")

    append_event(
        event_path,
        source="executor",
        event_type="order_intent",
        correlation_id="sig-2026-06-19",
        timestamp_utc="2026-06-19T10:25:09Z",
        payload={
            "direction": "SELL",
            "lot_size": 0.01,
            "stop_loss_price": 4178.28,
            "take_profit_price": 4090.78,
            "entry_price": 4153.25,
            "owner": "xauex",
            "pattern": "NONE",
            "metadata": {
                "session": {
                    "signal_id": "sig-2026-06-19",
                    "assurance_reason": "HIGH_ASSURANCE",
                    "counter_signal": False,
                },
                "signal_context": {
                    "signal_action": "SELL",
                    "signal_confidence": 0.80,
                    "consensus_state": "aligned",
                    "validator_status": "reviewed",
                    "range_position": "LOWER_THIRD",
                    "market_snapshot_state": "fresh",
                },
            },
        },
    )
    append_event(
        event_path,
        source="executor",
        event_type="order_ack",
        correlation_id="sig-2026-06-19",
        timestamp_utc="2026-06-19T10:25:11Z",
        payload={"status": "filled", "position_id": "318423909"},
    )
    append_event(
        event_path,
        source="executor",
        event_type="position_opened",
        correlation_id="sig-2026-06-19",
        timestamp_utc="2026-06-19T10:25:12Z",
        payload={
            "position_id": "318423909",
            "direction": "SHORT",
            "entry_price": 4153.25,
            "stop_loss": 4178.28,
            "take_profit": 4090.78,
            "lot_size": 0.01,
            "owner": "xauex",
        },
    )
    append_event(
        event_path,
        source="executor",
        event_type="position_closed",
        correlation_id="sig-2026-06-19",
        timestamp_utc="2026-06-19T12:59:42Z",
        payload={
            "position_id": "318423909",
            "direction": "SHORT",
            "entry_price": 4153.25,
            "close_price": 4178.94,
            "pnl": -19.41,
            "owner": "xauex",
        },
    )

    post_trade_journal.run(
        state_path=str(state_path),
        cursor_path=str(cursor_path),
        journal_path=str(journal_path),
        event_journal_path=str(event_path),
    )

    entries = json.loads(journal_path.read_text(encoding="utf-8"))
    assert len(entries) == 1
    assert entries[0]["trade_id"] == "318423909"
    trade = entries[0]["entry"]
    assert trade["position_id"] == "318423909"
    assert trade["signal_confidence"] == 0.80
    assert trade["range_position"] == "LOWER_THIRD"
