from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "ops" / "run_xauex_daily_report.py"


def _load_report_module():
    spec = importlib.util.spec_from_file_location("run_xauex_daily_report", REPORT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_daily_report_treats_warning_diagnostics_with_manual_positions_as_non_failure(monkeypatch):
    report = _load_report_module()

    dashboard = {
        "data": {
            "signal": {
                "action": "BUY",
                "confidence": 0.81,
                "generated_at_utc": "2026-04-07T19:10:00Z",
            },
            "diagnostics": {
                "overall_status": "degraded",
                "summary": "Latest XAUEX decision is BUY at 81% confidence. Manual positions are open while XAUEX has no managed trade.",
                "current_issues": [
                    {
                        "component": "signal",
                        "severity": "warning",
                        "summary": "Signal is stale at 1260s old.",
                        "next_action": "Refresh before the next London entry window.",
                    }
                ],
                "components": {
                    "positions": {"xauex_open": 0, "manual_open": 1, "total_open": 1},
                    "signal": {"state": "live", "action": "BUY"},
                    "manual": {"state": "ready"},
                    "quote": {"state": "live"},
                },
                "reply": "Signal is stale at 1260s old.",
                "recommended_action": "Refresh before the next London entry window.",
            },
            "account": {
                "signal_runs_taken_today": 1,
                "signal_runs_cap": 2,
                "trades_taken_today": 0,
                "trade_cap": 2,
            },
        }
    }
    health = {"status": "degraded", "bot_status": "OBSERVE_ONLY"}

    monkeypatch.setattr(
        report,
        "_service_status",
        lambda unit: "active",
    )
    monkeypatch.setattr(
        report,
        "_fetch_json",
        lambda url: dashboard if "dashboard" in url else health,
    )

    result = report._render_report()

    assert result.ok is True
    assert result.subject == "XAUEX daily status OK"
    assert "Warnings:" in result.body
    assert "- signal: Signal is stale at 1260s old." in result.body
    assert "manual positions are open" in result.body.lower()
    assert "FAILED" not in result.body.splitlines()[0]


def test_daily_report_treats_xauex_max_position_halt_as_expected_when_xauex_position_is_open(monkeypatch):
    report = _load_report_module()

    dashboard = {
        "data": {
            "signal": {
                "action": "SELL",
                "confidence": 0.73,
                "generated_at_utc": "2026-04-07T12:45:00Z",
            },
            "diagnostics": {
                "overall_status": "degraded",
                "summary": "Latest XAUEX decision is SELL at 73% confidence. XAUEX has 1 managed position(s) open.",
                "current_issues": [],
                "components": {
                    "positions": {"xauex_open": 1, "manual_open": 0, "total_open": 1},
                    "signal": {"state": "live", "action": "SELL"},
                    "manual": {"state": "idle"},
                    "quote": {"state": "live"},
                },
            },
            "account": {
                "signal_runs_taken_today": 1,
                "signal_runs_cap": 2,
                "trades_taken_today": 1,
                "trade_cap": 2,
            },
        }
    }
    health = {"status": "ok", "bot_status": "HALTED_MAX_POSITIONS_REACHED"}

    monkeypatch.setattr(report, "_service_status", lambda unit: "active")
    monkeypatch.setattr(report, "_fetch_json", lambda url: dashboard if "dashboard" in url else health)

    result = report._render_report()

    assert result.ok is True
    assert result.subject == "XAUEX daily status OK"
    assert "bot status is HALTED_MAX_POSITIONS_REACHED" not in result.body


def test_daily_report_fails_on_critical_diagnostics_and_disconnected_health(monkeypatch):
    report = _load_report_module()

    dashboard = {
        "data": {
            "signal": {
                "action": "BUY",
                "confidence": 0.81,
                "generated_at_utc": "2026-04-07T12:45:00Z",
            },
            "diagnostics": {
                "overall_status": "blocked",
                "summary": "Latest XAUEX decision is BUY at 81% confidence. XAUEX has 1 managed position(s) open.",
                "current_issues": [
                    {
                        "component": "signal",
                        "severity": "critical",
                        "summary": "Signal is stale at 1260s old.",
                        "next_action": "Run the signal pipeline to refresh the signal before the next trade window.",
                    }
                ],
                "components": {
                    "positions": {"xauex_open": 1, "manual_open": 0, "total_open": 1},
                    "signal": {"state": "live", "action": "BUY"},
                    "manual": {"state": "idle"},
                    "quote": {"state": "live"},
                },
                "reply": "Signal is stale at 1260s old.",
                "recommended_action": "Run the signal pipeline to refresh the signal before the next trade window.",
            },
            "account": {
                "signal_runs_taken_today": 1,
                "signal_runs_cap": 2,
                "trades_taken_today": 1,
                "trade_cap": 2,
            },
        }
    }
    health = {"status": "disconnected", "bot_status": "HALTED_DISCONNECTED"}

    monkeypatch.setattr(
        report,
        "_service_status",
        lambda unit: "active",
    )
    monkeypatch.setattr(
        report,
        "_fetch_json",
        lambda url: dashboard if "dashboard" in url else health,
    )

    result = report._render_report()

    assert result.ok is False
    assert result.subject == "XAUEX daily status FAILED"
    assert "- bot health is disconnected" in result.body
    assert "- bot status is HALTED_DISCONNECTED" in result.body
    assert "- signal: Signal is stale at 1260s old." in result.body


def test_daily_report_includes_daily_cost_summary_and_budget_warning(monkeypatch):
    report = _load_report_module()

    dashboard = {
        "data": {
            "signal": {
                "action": "SELL",
                "confidence": 0.73,
                "generated_at_utc": "2026-04-07T12:45:00Z",
                "llm_usage": {"estimated_total_cost_usd": 0.0011},
            },
            "diagnostics": {
                "overall_status": "healthy",
                "summary": "Latest XAUEX decision is SELL at 73% confidence.",
                "current_issues": [],
                "components": {
                    "positions": {"xauex_open": 0, "manual_open": 0, "total_open": 0},
                    "signal": {"state": "live", "action": "SELL"},
                    "manual": {"state": "idle"},
                    "quote": {"state": "live"},
                },
            },
            "account": {
                "signal_runs_taken_today": 1,
                "signal_runs_cap": 2,
                "trades_taken_today": 0,
                "trade_cap": 2,
            },
        }
    }
    health = {"status": "ok", "bot_status": "RUNNING"}

    monkeypatch.setattr(report, "_service_status", lambda unit: "active")
    monkeypatch.setattr(report, "_fetch_json", lambda url: dashboard if "dashboard" in url else health)
    monkeypatch.setattr(report, "_daily_cost_summary", lambda: {"total_cost_usd": 0.17, "daily_cap_usd": 0.20, "run_count": 2})

    result = report._render_report()

    assert result.ok is True
    assert result.subject == "XAUEX daily status OK"
    assert "- daily LLM cost: 0.1700 USD across 2 run(s)" in result.body
    assert "- budget warning: daily cost is at 85% of cap" in result.body
