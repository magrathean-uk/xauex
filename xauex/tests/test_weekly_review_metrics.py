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


from xauex.analyst.weekly_review import (
    build_deterministic_summary,
    build_review_prompt,
    compute_decision_metrics,
    compute_trade_metrics,
    finalize_analyst_commentary,
    filter_journal_to_week,
    format_trade_metrics,
)


def _trade(
    direction: str,
    pnl: float,
    pattern: str = "NONE",
    confidence: float = 0.5,
    *,
    counter_signal: bool = False,
    requested_cash_risk: float | None = None,
    effective_cash_risk: float | None = None,
    minimum_risk_floor_applied: bool = False,
) -> dict:
    session = {"counter_signal": counter_signal}
    if requested_cash_risk is not None:
        session["requested_cash_risk"] = requested_cash_risk
    if effective_cash_risk is not None:
        session["effective_cash_risk"] = effective_cash_risk
    if minimum_risk_floor_applied:
        session["minimum_risk_floor_applied"] = True
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
            "metadata": {"session": session},
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


def test_compute_trade_metrics_does_not_flag_three_of_five_as_direction_skew():
    journal = [_trade("SHORT", 5.0) for _ in range(3)] + [_trade("LONG", -5.0) for _ in range(2)]
    metrics = compute_trade_metrics(journal)
    assert metrics["direction_skew_ratio"] == 0.6
    assert metrics["direction_skew_alert"] is False


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
    assert "PATTERN COVERAGE ALERT" in formatted
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
    assert "PATTERN COVERAGE ALERT" not in formatted


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


def test_compute_trade_metrics_separates_counter_lane_and_effective_risk():
    journal = [
        _trade(
            "SHORT",
            12.0,
            counter_signal=True,
            requested_cash_risk=6.0,
            effective_cash_risk=12.0,
            minimum_risk_floor_applied=True,
        ),
        _trade(
            "LONG",
            -5.0,
            requested_cash_risk=8.0,
            effective_cash_risk=8.0,
        ),
    ]

    metrics = compute_trade_metrics(journal)

    assert metrics["counter_signal_count"] == 1
    assert metrics["counter_signal_pnl"] == 12.0
    assert metrics["baseline_count"] == 1
    assert metrics["baseline_pnl"] == -5.0
    assert metrics["risk_telemetry_count"] == 2
    assert metrics["requested_cash_risk"] == 14.0
    assert metrics["effective_cash_risk"] == 20.0
    assert metrics["risk_floor_lift_count"] == 1
    assert metrics["confidence_by_lane"] == {
        "baseline_high": {"count": 0, "pnl": 0.0},
        "baseline_low": {"count": 1, "pnl": -5.0},
        "counter_high": {"count": 0, "pnl": 0.0},
        "counter_low": {"count": 1, "pnl": 12.0},
    }


def test_decision_metrics_use_window_pattern_evidence_and_counter_execution():
    ledger = {
        "days": [
            {
                "date_london": "2026-05-06",
                "windows": {
                    "morning": {
                        "outcome": "TRADED",
                        "reason": "ORDER_PLACED",
                        "counter_signal": True,
                        "pattern_evidence": {"factor": "PATTERN_MISSING"},
                    },
                    "midday": {
                        "outcome": "BLOCKED",
                        "reason": "HARD_BLOCKER",
                        "pattern_evidence": {"factor": "PATTERN_DIRECTION_MISMATCH"},
                    },
                    "us_open": {
                        "outcome": "BLOCKED",
                        "reason": "HARD_BLOCKER",
                        "pattern_evidence": {"factor": "PATTERN_MISSING"},
                    },
                },
            }
        ]
    }
    start = datetime(2026, 5, 4, tzinfo=timezone.utc)
    end = datetime(2026, 5, 10, 23, 59, 59, tzinfo=timezone.utc)

    metrics = compute_decision_metrics(ledger, start, end)

    assert metrics["window_count"] == 3
    assert metrics["traded"] == 1
    assert metrics["blocked"] == 2
    assert metrics["counter_signal_trades"] == 1
    assert metrics["pattern_evaluated"] == 3
    assert metrics["pattern_missing"] == 2
    assert metrics["pattern_direction_mismatches"] == 1
    assert metrics["pattern_match_rate"] == 0.0
    assert metrics["pattern_coverage_alert"] is True


def test_review_prompt_states_approved_pattern_policy_and_does_not_invent_shadow_comparison():
    start = datetime(2026, 5, 4, tzinfo=timezone.utc)
    end = datetime(2026, 5, 10, 23, 59, 59, tzinfo=timezone.utc)
    prompt = build_review_prompt(
        {"account": {"currency": "GBP"}, "risk": {}},
        [_trade("SHORT", 5.0) for _ in range(3)] + [_trade("LONG", -5.0) for _ in range(2)],
        [],
        start,
        end,
        ledger={"days": []},
    )

    assert "Direction skew alert: false" in prompt
    assert "PATTERN_MISSING is intentionally allowed at reduced requested risk" in prompt
    assert "Do not recommend a hard pattern gate from coverage alone" in prompt
    assert "Execution lanes:" in prompt
    assert "did primary and shadow strategies agree" not in prompt
    assert "Live PnL above is GBP" in prompt
    assert "do not claim that confidence caused the outcome difference" in prompt
    assert "not authorization to relax a live gate" in prompt
    assert "cannot prove whether drawdown limits were approached" in prompt


def test_deterministic_summary_uses_decision_coverage_boolean():
    summary = build_deterministic_summary(
        state={"account": {"currency": "GBP"}},
        trade_metrics={
            "trade_count": 0,
            "wins": 0,
            "losses": 0,
            "net_pnl": 0.0,
            "direction_skew_ratio": 0.0,
            "direction_skew_alert": False,
            "baseline_count": 0,
            "baseline_pnl": 0.0,
            "counter_signal_count": 0,
            "counter_signal_pnl": 0.0,
        },
        decision_metrics={
            "pattern_match_rate": 0.0,
            "pattern_matches": 0,
            "pattern_evaluated": 3,
            "pattern_coverage_alert": True,
            "window_count": 3,
            "traded": 0,
            "blocked": 3,
        },
    )

    assert "Coverage alert: true" in summary


def test_analyst_commentary_requires_completion_marker_or_gets_safe_fallback():
    complete, complete_flag = finalize_analyst_commentary("Concise review.\nREVIEW_COMPLETE")
    truncated, truncated_flag = finalize_analyst_commentary("Recommendation cut off")

    assert complete == "Concise review."
    assert complete_flag is True
    assert truncated_flag is False
    assert "DETERMINISTIC FALLBACK RECOMMENDATIONS" in truncated
    assert "shadow only" in truncated


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
