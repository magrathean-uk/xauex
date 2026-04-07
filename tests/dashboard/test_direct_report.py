from __future__ import annotations

import importlib
import json
from pathlib import Path


def _load_dashboard_module(monkeypatch, tmp_path: Path):
    state_path = tmp_path / "state.json"
    cmd_path = tmp_path / "cmd.json"
    brief_path = tmp_path / "latest_signal_brief.md"
    brief_meta_path = tmp_path / "latest_signal_brief.json"
    evidence_path = tmp_path / "latest_signal_evidence.json"

    state_path.write_text(
        json.dumps(
            {
                "meta": {"bot_status": "RUNNING", "last_updated_utc": "2026-04-07T01:02:03Z"},
                "account": {"balance": 12345.67, "equity": 12400.1, "open_pnl": 54.43},
                "risk": {"daily_pnl": 12.5, "weekly_pnl": 34.5, "mirofish_trades_taken_london": 0},
                "open_positions": [],
                "closed_trades_today": [],
                "signal_history": [{"action": "BUY", "confidence": 0.8}],
                "levels": {"daily": {"low": 2325.1, "high": 2364.8}},
            }
        ),
        encoding="utf-8",
    )
    cmd_path.write_text(
        json.dumps(
            {
                "generated_at_utc": "2026-04-07T01:00:00Z",
                "mirofish_signal": {
                    "action": "BUY",
                    "symbol": "XAUUSD",
                    "confidence": 0.81,
                    "reasoning": "Breakout confirmed from local momentum.",
                    "source": {"mode": "direct"},
                },
            }
        ),
        encoding="utf-8",
    )
    brief_path.write_text("# Direct brief\n\nFed is dovish.", encoding="utf-8")
    brief_meta_path.write_text(
        json.dumps(
            {
                "updated_at_utc": "2026-04-07T01:01:00Z",
                "title": "Latest Brief",
                "usage": {"prompt_tokens": 120, "completion_tokens": 45},
            }
        ),
        encoding="utf-8",
    )
    evidence_path.write_text(
        json.dumps(
            {
                "prediction_mode": "direct",
                "context_summary": "Macro context remains supportive for gold.",
                "weights": {"price_action": 0.45, "macro": 0.35},
                "recent_runs": [{"action": "BUY"}],
                "price_features": {"price_bias": "bullish", "range_position": "upper"},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("STATE_FILE_PATH", str(state_path))
    monkeypatch.setenv("CMD_FILE_PATH", str(cmd_path))
    monkeypatch.setenv("BRIDGE_BRIEF_OUTPUT_PATH", str(brief_path))
    monkeypatch.setenv("BRIDGE_EVIDENCE_OUTPUT_PATH", str(evidence_path))

    import dashboard_web.app as dashboard_app

    dashboard_app = importlib.reload(dashboard_app)
    dashboard_app.STATE_PATH = state_path
    dashboard_app.CMD_PATH = cmd_path
    dashboard_app.BRIEF_PATH = brief_path
    dashboard_app.BRIEF_META_PATH = brief_meta_path
    dashboard_app.EVIDENCE_PATH = evidence_path
    dashboard_app.app.config.update(TESTING=True)
    return dashboard_app


def test_direct_report_route_renders_local_artifacts(monkeypatch, tmp_path):
    dashboard_app = _load_dashboard_module(monkeypatch, tmp_path)
    client = dashboard_app.app.test_client()

    response = client.get("/report/direct")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Direct Report" in body
    assert "Breakout confirmed from local momentum." in body
    assert "Fed is dovish." in body
    assert "Macro context remains supportive for gold." in body
    assert "Diagnostics" in body


def test_dashboard_payload_uses_direct_report_link_when_report_id_missing(monkeypatch, tmp_path):
    dashboard_app = _load_dashboard_module(monkeypatch, tmp_path)
    client = dashboard_app.app.test_client()

    response = client.get("/api/dashboard")

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["links"]["report"] == "/report/direct"
    assert payload["links"]["report_download"] is None
    assert payload["links"]["report_direct"] == "/report/direct"


def test_homepage_report_link_defaults_to_direct_report(monkeypatch, tmp_path):
    dashboard_app = _load_dashboard_module(monkeypatch, tmp_path)
    client = dashboard_app.app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="report-link"' in body
    assert 'href="/report/direct"' in body
