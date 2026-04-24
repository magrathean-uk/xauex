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
                "risk": {"daily_pnl": 12.5, "weekly_pnl": 34.5, "xauex_trades_taken_london": 1},
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
                        "owner": "xauex",
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
                "xauex_signal": {
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
    monkeypatch.setenv("XAUEX_MANUAL_COMMAND_PATH", str(manual_cmd_path))
    monkeypatch.setenv("XAUEX_SIGNAL_BRIEF_OUTPUT_PATH", str(brief_path))
    monkeypatch.setenv("XAUEX_SIGNAL_EVIDENCE_OUTPUT_PATH", str(evidence_path))

    import xauex.app.app as dashboard_app

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


def test_dashboard_payload_keeps_signal_run_cap_distinct_from_trade_cap(monkeypatch, tmp_path):
    dashboard_app = _load_dashboard_module(monkeypatch, tmp_path)
    client = dashboard_app.app.test_client()

    dashboard_app.STATE_PATH.write_text(
        json.dumps(
            {
                "meta": {"bot_status": "RUNNING", "last_updated_utc": "2026-04-07T01:02:03Z"},
                "account": {"balance": 12345.67, "equity": 12400.1, "open_pnl": 54.43},
                "risk": {
                    "daily_pnl": 12.5,
                    "weekly_pnl": 34.5,
                    "xauex_trades_taken_london": 0,
                    "xauex_signal_runs_london": [
                        {"slot": "MORNING", "date_london": "2026-04-07"},
                    ],
                },
                "open_positions": [],
                "closed_trades_today": [],
                "signal_history": [{"action": "BUY", "confidence": 0.8}],
                "runtime": {
                    "xauex_max_trades_per_day": 1,
                },
            }
        ),
        encoding="utf-8",
    )

    response = client.get("/api/dashboard")

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["account"]["signal_runs_taken_today"] == 1
    assert payload["account"]["signal_runs_cap"] == 3
    assert payload["account"]["trade_cap"] == 1
    assert "all three scheduled XAUEX windows" not in payload["trade_explanation"]


def test_dashboard_payload_caps_chart_window_and_signal_histories(monkeypatch, tmp_path):
    dashboard_app = _load_dashboard_module(monkeypatch, tmp_path)
    client = dashboard_app.app.test_client()

    dashboard_app.STATE_PATH.write_text(
        json.dumps(
            {
                "meta": {"bot_status": "RUNNING", "last_updated_utc": "2026-04-07T01:02:03Z"},
                "account": {"balance": 12345.67, "equity": 12400.1, "open_pnl": 54.43},
                "risk": {"daily_pnl": 12.5, "weekly_pnl": 34.5, "xauex_trades_taken_london": 1},
                "open_positions": [],
                "closed_trades_today": [],
                "recent_h1_closes": list(range(30)),
                "trade_entries_on_chart": [
                    {"bar_index": 5, "direction": "BUY", "price": 5.0},
                    {"bar_index": 28, "direction": "SELL", "price": 28.0},
                ],
                "signal_history": [{"action": f"SIG-{idx}"} for idx in range(25)],
                "shadow_signal_history": [{"action": f"SHADOW-{idx}"} for idx in range(25)],
                "runtime": {
                    "latest_quote": {
                        "bid": 2362.2,
                        "ask": 2362.7,
                        "mid": 2362.45,
                        "updated_at_utc": "2026-04-07T01:02:05Z",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    response = client.get("/api/dashboard")

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["recent_h1_closes"] == list(range(10, 30))
    assert payload["chart"]["recent_h1_closes"] == list(range(10, 30))
    assert payload["trade_entries_on_chart"] == [{"bar_index": 18, "direction": "SELL", "price": 28.0}]
    assert payload["chart"]["trade_entries"] == [{"bar_index": 18, "direction": "SELL", "price": 28.0}]
    assert len(payload["signal_history"]) == 12
    assert len(payload["shadow_signal_history"]) == 12


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


def test_dashboard_payload_includes_diagnostics(monkeypatch, tmp_path):
    dashboard_app = _load_dashboard_module(monkeypatch, tmp_path)
    client = dashboard_app.app.test_client()

    response = client.get("/api/dashboard")

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["diagnostics"]["schema_version"] == 1
    assert payload["diagnostics"]["components"]["quote"]["state"] in {"live", "stale"}
    assert payload["diagnostics"]["reply"]


def test_dashboard_payload_reuses_normalized_oracle_signal_and_run_count(monkeypatch, tmp_path):
    dashboard_app = _load_dashboard_module(monkeypatch, tmp_path)
    client = dashboard_app.app.test_client()

    dashboard_app.STATE_PATH.write_text(
        json.dumps(
            {
                "meta": {"bot_status": "RUNNING", "last_updated_utc": "2026-04-07T01:02:03Z"},
                "account": {"balance": 12345.67, "equity": 12400.1, "open_pnl": 54.43},
                "risk": {
                    "daily_pnl": 12.5,
                    "weekly_pnl": 34.5,
                    "xauex_trades_taken_london": 0,
                },
                "open_positions": [],
                "closed_trades_today": [],
                "signal_history": [{"action": "SELL", "confidence": 0.2}],
                "runtime": {
                    "xauex_signal_runs_taken_london": 1,
                    "latest_quote": {
                        "bid": 2362.2,
                        "ask": 2362.7,
                        "mid": 2362.45,
                        "updated_at_utc": "2026-04-07T01:02:05Z",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    dashboard_app.CMD_PATH.write_text(
        json.dumps(
            {
                "generated_at_utc": "2026-04-07T01:00:00Z",
                "xauex_signal": {
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

    response = client.get("/api/dashboard")

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["signal"]["action"] == "BUY"
    assert payload["diagnostics"]["components"]["signal"]["action"] == "BUY"
    assert payload["account"]["signal_runs_taken_today"] == 1
    assert payload["diagnostics"]["components"]["risk"]["signal_runs_taken_today"] == 1


def test_dashboard_payload_exposes_window_statuses_and_candidate_metrics(monkeypatch, tmp_path):
    dashboard_app = _load_dashboard_module(monkeypatch, tmp_path)
    client = dashboard_app.app.test_client()

    dashboard_app.STATE_PATH.write_text(
        json.dumps(
            {
                "meta": {"bot_status": "RUNNING", "last_updated_utc": "2026-04-07T01:02:03Z"},
                "account": {"balance": 12345.67, "equity": 12400.1, "open_pnl": 54.43},
                "risk": {
                    "daily_pnl": 12.5,
                    "weekly_pnl": 34.5,
                    "xauex_trades_taken_london": 1,
                    "xauex_signal_runs_london": [
                        {
                            "slot": "MORNING",
                            "date_london": "2026-04-07",
                            "signal_id": "2026-04-07T06:55:00Z",
                            "action": "ORDER_PLACED",
                            "reason": "ORDER_PLACED",
                            "confirm_status": "CONFIRMED",
                            "confirm_reason": "CONFIRMED",
                            "confirm_timestamp_utc": "2026-04-07T06:59:00Z",
                            "terminal": True,
                        },
                        {
                            "slot": "MIDDAY",
                            "date_london": "2026-04-07",
                            "signal_id": "2026-04-07T10:25:00Z",
                            "action": "SPREAD_TOO_WIDE",
                            "reason": "SPREAD_TOO_WIDE",
                            "confirm_status": "SKIP",
                            "confirm_reason": "SPREAD_TOO_WIDE",
                            "confirm_timestamp_utc": "2026-04-07T10:29:00Z",
                            "terminal": True,
                        },
                    ],
                },
                "runtime": {
                    "candidate_metrics": {
                        "total": 4,
                        "completed": 3,
                        "false_negative_wins": 2,
                        "expectancy_usd": 6.25,
                    }
                },
                "open_positions": [],
                "closed_trades_today": [],
                "signal_history": [],
                "shadow_signal_history": [],
            }
        ),
        encoding="utf-8",
    )
    dashboard_app.CMD_PATH.write_text(
        json.dumps(
            {
                "generated_at_utc": "2026-04-07T12:25:00Z",
                "xauex_signal": {
                    "action": "BUY",
                    "symbol": "XAUUSD",
                    "confidence": 0.81,
                    "reasoning": "Breakout confirmed from local momentum.",
                    "window_label": "us_open",
                    "confirm_status": "PENDING",
                    "confirm_reason": "WAITING_FOR_CONFIRM",
                    "source": {"mode": "direct"},
                },
            }
        ),
        encoding="utf-8",
    )

    response = client.get("/api/dashboard")

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["account"]["signal_runs_cap"] == 3
    assert len(payload["windows"]) == 3
    assert payload["windows"][0]["slot"] == "MORNING"
    assert payload["windows"][0]["confirm_status"] == "CONFIRMED"
    assert payload["windows"][1]["slot"] == "MIDDAY"
    assert payload["windows"][1]["confirm_status"] == "SKIP"
    assert payload["windows"][2]["slot"] == "US_OPEN"
    assert payload["windows"][2]["confirm_status"] == "PENDING"
    assert payload["candidate_metrics"]["false_negative_wins"] == 2


def test_diagnostics_endpoint_returns_structured_snapshot(monkeypatch, tmp_path):
    dashboard_app = _load_dashboard_module(monkeypatch, tmp_path)
    client = dashboard_app.app.test_client()

    response = client.get("/api/diagnostics")

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["schema_version"] == 1
    assert payload["summary"]
    assert payload["reply"]


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
    assert body["reply"]
    assert body["diagnostics"]["schema_version"] == 1
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
    assert body["reply"]
    assert body["diagnostics"]["schema_version"] == 1
    command = json.loads(dashboard_app.MANUAL_CMD_PATH.read_text(encoding="utf-8"))
    assert command["command"] == "close"
    assert command["position_id"] == "m-123"
