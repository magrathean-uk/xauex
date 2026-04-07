import sys
from pathlib import Path
from datetime import datetime, timezone


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from diagnostics import build_diagnostics_snapshot


def test_diagnostics_snapshot_surfaces_current_quote_signal_and_manual_state():
    state = {
        "meta": {"bot_status": "RUNNING"},
        "account": {"balance": 100.0, "equity": 105.0, "open_pnl": 5.0, "currency": "GBP"},
        "risk": {
            "daily_pnl": 5.0,
            "weekly_pnl": 8.0,
            "day_start_balance": 95.0,
            "week_start_balance": 92.0,
            "mirofish_trades_taken_london": 1,
        },
        "open_positions": [
            {"position_id": "o-1", "direction": "BUY", "owner": "oracle", "unrealised_pnl": 2.5}
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
