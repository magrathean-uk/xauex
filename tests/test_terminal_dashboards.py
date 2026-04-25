from __future__ import annotations

# ruff: noqa: E402

import asyncio
from pathlib import Path
import sys

from textual.widgets import Static


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from xauex.dashboard import XAUEXDashboard
from xauex.remote_dashboard import RemoteSnapshot, RemoteXAUEXDashboard


def _sample_state() -> dict:
    return {
        "meta": {"bot_status": "RUNNING", "last_updated_utc": "2026-04-07T10:00:00Z"},
        "account": {"balance": 3000.0, "equity": 3025.0, "open_pnl": 25.0, "currency": "GBP"},
        "risk": {
            "daily_pnl": 20.0,
            "weekly_pnl": 35.0,
            "day_start_balance": 2980.0,
            "week_start_balance": 2965.0,
            "consecutive_losses_today": 0,
        },
        "runtime": {"strategy_data_ready": True, "strategy_data_reason": "ready"},
        "trend": {"alignment": "SHORT", "reason": "Momentum rollover"},
        "levels": {
            "monthly": {"open": 4600.0, "high": 4700.0, "low": 4500.0, "close": 4650.0},
            "weekly": {"open": 4620.0, "high": 4680.0, "low": 4580.0, "close": 4660.0},
        },
        "recent_h1_closes": [4600.0, 4610.0, 4605.0, 4598.0, 4592.0],
        "trade_entries_on_chart": [],
        "closed_trades_today": [],
        "signal_history": [],
        "shadow_signal_history": [],
        "last_signal": {
            "time_utc": "2026-04-07T09:35:00Z",
            "action": "SELL",
            "entry_source": "direct_prediction",
            "gate_result": "ENTRY_WINDOW_CLOSED",
            "reason": "Window closed.",
        },
        "diagnostics": {
            "overall_status": "degraded",
            "summary": "Oracle has a live SELL signal but no open trade.",
            "reply": "The entry window is closed, so the signal will not execute today.",
            "recommended_action": "Wait for the next London pre-open run.",
            "current_issues": [
                {"component": "signal", "summary": "Signal is stale."},
                {"component": "quote", "summary": "Quote is 22 seconds old."},
            ],
            "components": {
                "quote": {"state": "stale"},
                "signal": {"state": "stale", "action": "SELL"},
                "positions": {"state": "flat"},
                "manual": {"state": "idle"},
            },
        },
    }


def test_local_dashboard_renders_diagnostics_panel():
    async def runner() -> str:
        app = XAUEXDashboard()
        async with app.run_test() as _pilot:
            app._update_ui(_sample_state())
            return str(app.query_one("#diagnostics-panel", Static).content)

    panel = asyncio.run(runner())

    assert "DIAGNOSTICS [DEGRADED]" in panel
    assert "Reply: The entry window is closed" in panel
    assert "Next: Wait for the next London pre-open run." in panel


def test_remote_dashboard_renders_diagnostics_and_transport_errors():
    async def runner() -> str:
        app = RemoteXAUEXDashboard(
            health_url="http://example.test/health",
            state_url="http://example.test/state",
            refresh_seconds=2.0,
            timeout=5.0,
        )
        snapshot = RemoteSnapshot(
            health={"bot_status": "RUNNING", "status": "ok", "observe_only": False},
            state=_sample_state(),
            errors=["state fetch failed: timeout"],
        )
        async with app.run_test() as _pilot:
            app._update_ui(snapshot)
            return str(app.query_one("#diagnostics-panel", Static).content)

    panel = asyncio.run(runner())

    assert "DIAGNOSTICS [DEGRADED]" in panel
    assert "Transport:" in panel
    assert "state fetch failed: timeout" in panel
