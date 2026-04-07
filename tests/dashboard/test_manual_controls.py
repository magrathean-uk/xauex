from __future__ import annotations

import importlib
import json
from pathlib import Path


def _load_dashboard_module(monkeypatch, tmp_path: Path):
    state_path = tmp_path / "state.json"
    cmd_path = tmp_path / "cmd.json"
    manual_cmd_path = tmp_path / "manual_trade_cmd.json"
    brief_path = tmp_path / "latest_signal_brief.md"
    brief_meta_path = tmp_path / "latest_signal_brief.json"
    evidence_path = tmp_path / "latest_signal_evidence.json"

    state_path.write_text(
        json.dumps(
            {
                "meta": {"bot_status": "RUNNING", "last_updated_utc": "2026-04-07T01:02:03Z"},
                "account": {"balance": 12345.67, "equity": 12400.1, "open_pnl": 54.43},
                "risk": {"daily_pnl": 12.5, "weekly_pnl": 34.5, "mirofish_trades_taken_london": 1},
                "open_positions": [
                    {
                        "position_id": "m-123",
                        "direction": "BUY",
                        "entry_price": 2361.2,
                        "unrealised_pnl": 8.4,
                        "owner": "manual",
                    },
                    {
                        "position_id": "o-456",
                        "direction": "SELL",
                        "entry_price": 2358.9,
                        "unrealised_pnl": -2.2,
                        "owner": "oracle",
                    },
                ],
                "closed_trades_today": [],
                "recent_h1_closes": [2354.1, 2358.8, 2361.0, 2359.4],
                "trade_entries_on_chart": [{"bar_index": 1, "direction": "BUY", "price": 2358.8}],
                "signal_history": [{"action": "BUY", "confidence": 0.8}],
                "levels": {"daily": {"low": 2325.1, "high": 2364.8}},
                "runtime": {
                    "latest_quote": {
                        "bid": 2362.2,
                        "ask": 2362.7,
                        "mid": 2362.45,
                        "updated_at_utc": "2026-04-07T01:02:05Z",
                    },
                    "manual_trade_status": {
                        "ok": False,
                        "state": "idle",
                        "reason": "waiting for operator",
                        "updated_at_utc": "2026-04-07T01:01:30Z",
                    }
                },
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
    monkeypatch.setenv("MIROFISH_MANUAL_COMMAND_PATH", str(manual_cmd_path))
    monkeypatch.setenv("BRIDGE_BRIEF_OUTPUT_PATH", str(brief_path))
    monkeypatch.setenv("BRIDGE_EVIDENCE_OUTPUT_PATH", str(evidence_path))

    import dashboard_web.app as dashboard_app

    dashboard_app = importlib.reload(dashboard_app)
    dashboard_app.STATE_PATH = state_path
    dashboard_app.CMD_PATH = cmd_path
    dashboard_app.MANUAL_CMD_PATH = manual_cmd_path
    dashboard_app.BRIEF_PATH = brief_path
    dashboard_app.BRIEF_META_PATH = brief_meta_path
    dashboard_app.EVIDENCE_PATH = evidence_path
    dashboard_app.app.config.update(TESTING=True)
    return dashboard_app


def test_dashboard_payload_includes_chart_section(monkeypatch, tmp_path):
    dashboard_app = _load_dashboard_module(monkeypatch, tmp_path)
    client = dashboard_app.app.test_client()

    response = client.get("/api/dashboard")

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["chart"]["recent_h1_closes"] == [2354.1, 2358.8, 2361.0, 2359.4]
    assert payload["chart"]["trade_entries"] == [{"bar_index": 1, "direction": "BUY", "price": 2358.8}]
    assert payload["quote"]["mid"] == 2362.45
    assert payload["quote"]["ask"] == 2362.7
    assert payload["manual_positions"][0]["position_id"] == "m-123"


def test_dashboard_payload_exposes_auth_and_manual_status(monkeypatch, tmp_path):
    dashboard_app = _load_dashboard_module(monkeypatch, tmp_path)
    client = dashboard_app.app.test_client()

    response = client.get("/api/dashboard")

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["auth"]["authenticated"] is True
    assert payload["auth"]["controls_enabled"] is True
    assert payload["manual_trade_status"]["state"] == "idle"
    assert payload["manual_trade_status"]["reason"] == "waiting for operator"


def test_manual_trade_requires_json(monkeypatch, tmp_path):
    dashboard_app = _load_dashboard_module(monkeypatch, tmp_path)
    client = dashboard_app.app.test_client()

    response = client.post("/api/manual-trade", data="not-json", headers={"Content-Type": "text/plain"})

    assert response.status_code == 400


def test_manual_trade_endpoint_writes_manual_command_file(monkeypatch, tmp_path):
    dashboard_app = _load_dashboard_module(monkeypatch, tmp_path)
    client = dashboard_app.app.test_client()

    response = client.post(
        "/api/manual-trade",
        json={"command": "open", "action": "BUY", "lot_size": 0.25, "stop_loss": 2351.5, "take_profit": 2364.0},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["status"] == "queued"
    command = json.loads(dashboard_app.MANUAL_CMD_PATH.read_text(encoding="utf-8"))
    assert command["command"] == "open"
    assert command["action"] == "BUY"
    assert command["lot_size"] == 0.25
    assert command["stop_loss"] == 2351.5
    assert command["take_profit"] == 2364.0


def test_manual_trade_endpoint_requires_stop_loss_and_take_profit(monkeypatch, tmp_path):
    dashboard_app = _load_dashboard_module(monkeypatch, tmp_path)
    client = dashboard_app.app.test_client()

    response = client.post(
        "/api/manual-trade",
        json={"command": "open", "action": "BUY", "lot_size": 0.25},
    )

    assert response.status_code == 400


def test_manual_close_endpoint_writes_manual_close_command(monkeypatch, tmp_path):
    dashboard_app = _load_dashboard_module(monkeypatch, tmp_path)
    client = dashboard_app.app.test_client()

    response = client.post(
        "/api/manual-close",
        json={"command": "close", "position_id": "m-123"},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["status"] == "queued"
    command = json.loads(dashboard_app.MANUAL_CMD_PATH.read_text(encoding="utf-8"))
    assert command["command"] == "close"
    assert command["position_id"] == "m-123"
