# ruff: noqa: E402
"""Direction-aware weekly review metrics.

The May 8 review reported "win rate 50%, recommend increasing risk appetite" on
a system that was 82% short across its lifetime with shorts barely breaking
even (+$6) and longs producing nearly all the lifetime PnL (+$224). The new
metrics surface direction skew, pattern hit rate, and trend-regime breakdown
so the analyst LLM can act on real evidence instead of generic narrative.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from xauex.analyst.weekly_review import compute_trade_metrics, filter_journal_to_week, format_trade_metrics


def _trade(direction: str, pnl: float, pattern: str = "NONE", confidence: float = 0.5) -> dict:
    return {
        "trade_id": f"t-{direction}-{pnl}",
        "journalled_at_utc": "2026-05-06T12:00:00Z",
        "entry": {
            "direction": direction,
            "pnl": pnl,
            "pattern": pattern,
            "stop_loss": 4670.0,
            "take_profit": 4750.0,
            "entry_price": 4700.0,
            "close_price": 4700.0 + (pnl * 10),
            "lot_size": 0.01,
            "signal_confidence": confidence,
        },
        "journal": "auto",
    }


def test_compute_trade_metrics_returns_zero_metrics_for_empty_journal():
    metrics = compute_trade_metrics([])
    assert metrics["trade_count"] == 0
    assert metrics["long_count"] == 0
    assert metrics["short_count"] == 0
    assert metrics["pattern_hit_rate"] == 0.0
    assert metrics["direction_skew_alert"] is False


def test_compute_trade_metrics_breaks_down_by_direction():
    journal = [
        _trade("LONG", 18.0, pattern="BULLISH_ENGULFING"),
        _trade("LONG", 7.0, pattern="NONE"),
        _trade("SHORT", -18.0, pattern="NONE"),
        _trade("SHORT", -17.0, pattern="NONE"),
        _trade("SHORT", 5.0, pattern="BEARISH_CONTINUATION_CLOSE"),
    ]
    metrics = compute_trade_metrics(journal)
    assert metrics["trade_count"] == 5
    assert metrics["long_count"] == 2
    assert metrics["short_count"] == 3
    assert metrics["long_pnl"] == 25.0
    assert metrics["short_pnl"] == -30.0
    assert metrics["wins"] == 3
    assert metrics["losses"] == 2
    assert metrics["win_rate"] == 0.6


def test_compute_trade_metrics_flags_direction_skew_above_70():
    """When >70% of trades go in one direction, the analyst must see an
    explicit alert rather than silently aggregating."""
    journal = [_trade("SHORT", -5.0) for _ in range(8)] + [_trade("LONG", 5.0) for _ in range(2)]
    metrics = compute_trade_metrics(journal)
    assert metrics["direction_skew_alert"] is True
    assert metrics["direction_skew_ratio"] == 0.8


def test_compute_trade_metrics_flags_low_pattern_hit_rate():
    """Pattern hit rate < 25% must trigger an alert. The May 2026 incident had
    100% pattern=NONE for all April–May trades."""
    journal = [_trade("SHORT", -5.0, pattern="NONE") for _ in range(10)]
    metrics = compute_trade_metrics(journal)
    assert metrics["pattern_hit_rate"] == 0.0
    assert metrics["pattern_hit_rate_alert"] is True


def test_compute_trade_metrics_flags_low_pattern_hit_rate_with_one_pattern():
    journal = [
        *(_trade("SHORT", -5.0, pattern="NONE") for _ in range(8)),
        _trade("LONG", 18.0, pattern="BULLISH_ENGULFING"),
        _trade("LONG", 7.0, pattern="BULLISH_PIN_BAR"),
    ]
    metrics = compute_trade_metrics(journal)
    assert metrics["pattern_hit_rate"] == 0.2
    assert metrics["pattern_hit_rate_alert"] is True


def test_format_trade_metrics_includes_alert_lines_when_skew_or_pattern_low():
    journal = [_trade("SHORT", -5.0) for _ in range(10)]
    metrics = compute_trade_metrics(journal)
    formatted = format_trade_metrics(metrics)
    assert "DIRECTION SKEW ALERT" in formatted
    assert "PATTERN HIT RATE ALERT" in formatted
    assert "LONG" in formatted
    assert "SHORT" in formatted


def test_format_trade_metrics_omits_alerts_when_balanced():
    journal = [
        _trade("LONG", 8.0, pattern="BULLISH_ENGULFING"),
        _trade("LONG", -5.0, pattern="BULLISH_PIN_BAR"),
        _trade("SHORT", -5.0, pattern="BEARISH_PIN_BAR"),
        _trade("SHORT", 7.0, pattern="BEARISH_ENGULFING"),
    ]
    metrics = compute_trade_metrics(journal)
    formatted = format_trade_metrics(metrics)
    assert "DIRECTION SKEW ALERT" not in formatted
    assert "PATTERN HIT RATE ALERT" not in formatted


def test_compute_trade_metrics_handles_missing_or_unknown_direction_gracefully():
    journal = [
        {"trade_id": "x", "entry": {"pnl": 5.0, "pattern": "NONE"}},  # no direction
        _trade("LONG", 8.0),
    ]
    metrics = compute_trade_metrics(journal)
    assert metrics["trade_count"] == 2
    assert metrics["long_count"] == 1
    assert metrics["short_count"] == 0
    assert metrics["unknown_direction_count"] == 1


def test_filter_journal_to_week_uses_trade_close_time_before_journal_time():
    week_start = datetime(2026, 6, 15, tzinfo=timezone.utc)
    week_end = datetime(2026, 6, 21, 23, 59, 59, tzinfo=timezone.utc)
    entries = [
        {
            "trade_id": "old-backfill",
            "journalled_at_utc": "2026-06-21T16:25:21Z",
            "entry": {"close_time_utc": "2026-05-29T14:00:06Z", "direction": "LONG", "pnl": 12.14},
        },
        {
            "trade_id": "loss-week",
            "journalled_at_utc": "2026-06-21T16:25:37Z",
            "entry": {"close_time_utc": "2026-06-19T12:59:42Z", "direction": "SHORT", "pnl": -19.41},
        },
    ]

    filtered = filter_journal_to_week(entries, week_start, week_end)

    assert [entry["trade_id"] for entry in filtered] == ["loss-week"]
