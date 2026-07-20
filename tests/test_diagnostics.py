# ruff: noqa: E402

import sys
from pathlib import Path
from datetime import datetime, timezone


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from xauex.shared.diagnostics import build_diagnostics_snapshot


def test_diagnostics_snapshot_surfaces_current_quote_signal_and_manual_state():
    state = {
        "meta": {"bot_status": "RUNNING"},
        "account": {"balance": 100.0, "equity": 105.0, "open_pnl": 5.0, "currency": "GBP"},
        "risk": {
            "daily_pnl": 5.0,
            "weekly_pnl": 8.0,
            "day_start_balance": 95.0,
            "week_start_balance": 92.0,
            "xauex_trades_taken_london": 1,
        },
        "open_positions": [
            {"position_id": "o-1", "direction": "BUY", "owner": "xauex", "unrealised_pnl": 2.5}
        ],
        "last_signal": {"action": "BUY", "confidence": 0.81, "reasoning": "Strong momentum."},
        "runtime": {
            "latest_quote": {"bid": 4653.68, "ask": 4653.78, "mid": 4653.73, "updated_at_utc": "2026-04-07T12:57:05Z"},
            "manual_trade_status": {"ok": False, "reason": "waiting"},
        },
    }

    diagnostics = build_diagnostics_snapshot(
        state,
        reference_time=datetime(2026, 4, 7, 12, 57, 6, tzinfo=timezone.utc),
    )

    assert diagnostics["schema_version"] == 1
    assert diagnostics["overall_status"] in {"healthy", "degraded", "blocked"}
    assert diagnostics["components"]["quote"]["state"] == "live"
    assert diagnostics["components"]["signal"]["action"] == "BUY"
    assert diagnostics["components"]["manual"]["state"] == "idle"
    assert diagnostics["summary"]
    assert diagnostics["reply"]
    assert diagnostics["current_issues"]


def test_diagnostics_snapshot_uses_normalized_oracle_signal_and_runtime_run_count():
    state = {
        "meta": {"bot_status": "RUNNING"},
        "account": {"balance": 100.0, "equity": 105.0, "open_pnl": 5.0, "currency": "GBP"},
        "risk": {
            "daily_pnl": 5.0,
            "weekly_pnl": 8.0,
            "day_start_balance": 95.0,
            "week_start_balance": 92.0,
            "xauex_trades_taken_london": 1,
        },
        "open_positions": [],
        "last_signal": {"action": "SELL", "confidence": 0.11, "reasoning": "Stale state signal."},
        "signal_history": [{"action": "SELL", "confidence": 0.11, "reasoning": "Stale state signal."}],
        "runtime": {
            "xauex_signal_runs_taken_london": 1,
            "latest_quote": {"bid": 4653.68, "ask": 4653.78, "mid": 4653.73, "updated_at_utc": "2026-04-07T12:57:05Z"},
            "manual_trade_status": {"ok": False, "reason": "waiting"},
        },
    }

    diagnostics = build_diagnostics_snapshot(
        state,
        oracle_signal={"action": "BUY", "confidence": 0.81, "reasoning": "Fresh direct signal."},
        reference_time=datetime(2026, 4, 7, 12, 57, 6, tzinfo=timezone.utc),
    )

    assert diagnostics["components"]["signal"]["action"] == "BUY"
    assert diagnostics["components"]["signal"]["reasoning"] == "Fresh direct signal."
    assert diagnostics["components"]["risk"]["signal_runs_taken_today"] == 1


def test_diagnostics_snapshot_marks_stale_signal_critical_during_london_window():
    state = {
        "meta": {"bot_status": "RUNNING"},
        "account": {"balance": 100.0, "equity": 105.0, "open_pnl": 5.0, "currency": "GBP"},
        "risk": {
            "daily_pnl": 5.0,
            "weekly_pnl": 8.0,
            "day_start_balance": 95.0,
            "week_start_balance": 92.0,
            "xauex_trades_taken_london": 1,
        },
        "open_positions": [],
        "last_signal": {
            "action": "BUY",
            "confidence": 0.81,
            "reasoning": "Strong momentum.",
            "generated_at_utc": "2026-04-07T12:45:00Z",
        },
        "runtime": {
            "latest_quote": {"bid": 4653.68, "ask": 4653.78, "mid": 4653.73, "updated_at_utc": "2026-04-07T12:57:05Z"},
            "manual_trade_status": {"ok": False, "reason": "waiting"},
        },
    }

    diagnostics = build_diagnostics_snapshot(
        state,
        reference_time=datetime(2026, 4, 7, 12, 57, 6, tzinfo=timezone.utc),
    )

    stale_issue = next(item for item in diagnostics["current_issues"] if item["code"] == "SIGNAL_STALE")
    assert stale_issue["severity"] == "critical"
    assert diagnostics["overall_status"] == "blocked"


def test_diagnostics_snapshot_downgrades_stale_signal_outside_london_window():
    state = {
        "meta": {"bot_status": "RUNNING"},
        "account": {"balance": 100.0, "equity": 105.0, "open_pnl": 5.0, "currency": "GBP"},
        "risk": {
            "daily_pnl": 5.0,
            "weekly_pnl": 8.0,
            "day_start_balance": 95.0,
            "week_start_balance": 92.0,
            "xauex_trades_taken_london": 1,
        },
        "open_positions": [],
        "last_signal": {
            "action": "BUY",
            "confidence": 0.81,
            "reasoning": "Strong momentum.",
            "generated_at_utc": "2026-04-07T19:10:00Z",
        },
        "runtime": {
            "latest_quote": {"bid": 4653.68, "ask": 4653.78, "mid": 4653.73, "updated_at_utc": "2026-04-07T19:29:55Z"},
            "manual_trade_status": {"ok": False, "reason": "waiting"},
        },
    }

    diagnostics = build_diagnostics_snapshot(
        state,
        reference_time=datetime(2026, 4, 7, 19, 30, 6, tzinfo=timezone.utc),
    )

    stale_issue = next(item for item in diagnostics["current_issues"] if item["code"] == "SIGNAL_STALE")
    assert stale_issue["severity"] == "warning"
    assert diagnostics["overall_status"] == "degraded"


def test_diagnostics_snapshot_keeps_manual_only_positions_as_expected_runtime_state():
    state = {
        "meta": {"bot_status": "RUNNING"},
        "account": {"balance": 100.0, "equity": 100.0, "open_pnl": 0.0, "currency": "GBP"},
        "risk": {
            "daily_pnl": 0.0,
            "weekly_pnl": 0.0,
            "day_start_balance": 100.0,
            "week_start_balance": 100.0,
            "xauex_trades_taken_london": 0,
        },
        "open_positions": [
            {"position_id": "m-1", "direction": "BUY", "owner": "manual", "unrealised_pnl": 1.25}
        ],
        "last_signal": {
            "action": "HOLD",
            "confidence": 0.5,
            "reasoning": "No strong setup.",
            "generated_at_utc": "2026-04-07T12:57:00Z",
        },
        "runtime": {
            "latest_quote": {"bid": 4653.68, "ask": 4653.78, "mid": 4653.73, "updated_at_utc": "2026-04-07T12:57:05Z"},
            "manual_trade_status": {"ok": True, "state": "ready"},
        },
    }

    diagnostics = build_diagnostics_snapshot(
        state,
        reference_time=datetime(2026, 4, 7, 12, 57, 6, tzinfo=timezone.utc),
    )

    assert diagnostics["components"]["positions"]["manual_open"] == 1
    assert diagnostics["overall_status"] == "healthy"
    assert diagnostics["current_issues"] == []
    assert "Manual positions are open while XAUEX has no managed trade." in diagnostics["summary"]


def test_diagnostics_marks_invalid_risk_wiring_critical():
    diagnostics = build_diagnostics_snapshot(
        {
            "meta": {"bot_status": "RUNNING"},
            "runtime": {
                "risk_state_wiring": {
                    "valid": False,
                    "error": "RISK_STATE_WIRING_INVALID",
                },
            },
        },
        reference_time=datetime(2026, 4, 7, 12, 57, 6, tzinfo=timezone.utc),
    )

    issue = next(item for item in diagnostics["current_issues"] if item["code"] == "RISK_STATE_WIRING_INVALID")
    assert issue["severity"] == "critical"
    assert diagnostics["overall_status"] == "blocked"
