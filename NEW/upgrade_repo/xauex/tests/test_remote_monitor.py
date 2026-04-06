import json
import io
import urllib.error

from remote_monitor import MonitorResult, fetch_json_http, make_change_key, render_report


def test_render_report_includes_health_and_signal():
    result = MonitorResult(
        health={
            "status": "ok",
            "bot_status": "RUNNING",
            "observe_only": False,
            "uptime_seconds": 120,
            "last_tick_age_seconds": 1,
            "reconnect_count": 2,
            "open_positions": 0,
        },
        state={
            "meta": {"last_updated_utc": "2026-03-17T09:00:00Z"},
            "account": {"balance": 3000.0, "equity": 3002.0, "open_pnl": 0.0, "currency": "GBP"},
            "risk": {"daily_pnl": 1.5, "weekly_pnl": 4.0, "consecutive_losses_today": 0},
            "trend": {
                "alignment": "BULLISH",
                "reason": "OK",
                "daily_ema_8": 5010.0,
                "daily_ema_21": 5005.0,
                "exec_ema_50": 5008.0,
                "exec_ema_200": 4999.0,
            },
            "runtime": {"candle_index": 12, "pending_inside_bar_pairs": 0, "kill_switch_active": False},
            "last_signal": {
                "time_utc": "2026-03-17T09:00:00Z",
                "pattern": "BULLISH_PIN_BAR",
                "level_checked": 5018.66,
                "gate_result": "OK",
                "action": "EXECUTED",
            },
            "signal_history": [{"time_utc": "2026-03-17T09:00:00Z", "pattern": "BULLISH_PIN_BAR", "gate_result": "OK", "action": "EXECUTED"}],
            "closed_trades_today": [],
        },
        errors=[],
    )

    report = render_report(result)
    assert "Health" in report
    assert "status=ok" in report
    assert "Trend" in report
    assert "alignment=BULLISH" in report
    assert "Last Signal" in report
    assert "BULLISH_PIN_BAR" in report


def test_make_change_key_uses_signal_and_status():
    result = MonitorResult(
        health={"status": "ok", "bot_status": "RUNNING", "open_positions": 1},
        state={"last_signal": {"time_utc": "2026-03-17T09:00:00Z", "gate_result": "NO_PATTERN", "action": "SKIP"}},
        errors=[],
    )
    assert make_change_key(result) == (
        "ok",
        "RUNNING",
        1,
        "2026-03-17T09:00:00Z",
        "NO_PATTERN",
        "SKIP",
    )


def test_fetch_json_http_parses_json_body_from_http_error(monkeypatch):
    payload = json.dumps({"status": "degraded", "bot_status": "RUNNING"}).encode("utf-8")

    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(
            url="http://example.test/health",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=io.BytesIO(payload),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    data = fetch_json_http("http://example.test/health", 5.0)
    assert data["status"] == "degraded"
    assert data["bot_status"] == "RUNNING"
