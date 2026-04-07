from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tui_diagnostics import format_diagnostics_panel


def test_format_diagnostics_panel_shows_summary_reply_next_and_issues():
    diagnostics = {
        "overall_status": "degraded",
        "summary": "Oracle has a live SELL signal but no open trade.",
        "reply": "The entry window is closed, so the signal will not execute today.",
        "recommended_action": "Wait for the next London pre-open run.",
        "current_issues": [
            {"component": "signal", "summary": "Signal is stale.", "next_action": "Refresh before trading."},
            {"component": "quote", "summary": "Quote is 22 seconds old.", "next_action": "Wait for the next tick."},
        ],
        "components": {
            "quote": {"state": "stale"},
            "signal": {"state": "stale", "action": "SELL"},
            "positions": {"state": "flat"},
            "manual": {"state": "idle"},
        },
    }

    panel = format_diagnostics_panel(diagnostics)

    assert "DIAGNOSTICS [DEGRADED]" in panel
    assert "Components: quote=stale signal=SELL positions=flat manual=idle" in panel
    assert "Summary: Oracle has a live SELL signal but no open trade." in panel
    assert "Reply: The entry window is closed, so the signal will not execute today." in panel
    assert "Next: Wait for the next London pre-open run." in panel
    assert "- Signal: Signal is stale." in panel
    assert "- Quote: Quote is 22 seconds old." in panel


def test_format_diagnostics_panel_includes_transport_errors():
    panel = format_diagnostics_panel(
        {
            "overall_status": "healthy",
            "summary": "Everything is running normally.",
            "reply": "No action required.",
            "recommended_action": "Keep monitoring.",
            "current_issues": [],
            "components": {},
        },
        transport_errors=["state fetch failed: timeout", "health fetch failed: connection reset"],
    )

    assert "Transport:" in panel
    assert "state fetch failed: timeout" in panel
    assert "health fetch failed: connection reset" in panel


def test_format_diagnostics_panel_handles_missing_payload():
    panel = format_diagnostics_panel(None)

    assert panel.startswith("DIAGNOSTICS [UNKNOWN]")
    assert "Summary: No diagnostics snapshot is available yet." in panel
