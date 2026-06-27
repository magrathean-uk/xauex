from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from xauex.signal.dsa_sidecar import (
    DsaDecision,
    DsaSidecarClient,
    DsaSidecarConfig,
    build_dsa_research_snapshot,
    map_dsa_decision_to_shadow_signal,
)


def test_dsa_sidecar_config_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("XAUEX_DSA_ENABLED", raising=False)
    monkeypatch.delenv("XAUEX_DSA_BASE_URL", raising=False)
    monkeypatch.delenv("XAUEX_DSA_SYMBOL", raising=False)

    cfg = DsaSidecarConfig.from_env()

    assert cfg.enabled is False
    assert cfg.base_url == "http://127.0.0.1:18090/api/v1"
    assert cfg.symbol == "AAPL"
    assert cfg.shadow_only is True
    assert cfg.min_confidence == 0.65


def test_dsa_sidecar_config_reads_env_and_normalizes_base_url(monkeypatch):
    monkeypatch.setenv("XAUEX_DSA_ENABLED", "1")
    monkeypatch.setenv("XAUEX_DSA_BASE_URL", "http://127.0.0.1:18090/api/v1/")
    monkeypatch.setenv("XAUEX_DSA_SYMBOL", " msft ")
    monkeypatch.setenv("XAUEX_DSA_ADMIN_COOKIE", "session-cookie")
    monkeypatch.setenv("XAUEX_DSA_TIMEOUT_SECONDS", "3.5")
    monkeypatch.setenv("XAUEX_DSA_MIN_CONFIDENCE", "0.72")
    monkeypatch.setenv("XAUEX_DSA_SHADOW_ONLY", "false")

    cfg = DsaSidecarConfig.from_env()

    assert cfg.enabled is True
    assert cfg.base_url == "http://127.0.0.1:18090/api/v1"
    assert cfg.symbol == "MSFT"
    assert cfg.admin_cookie == "session-cookie"
    assert cfg.timeout_seconds == 3.5
    assert cfg.min_confidence == 0.72
    assert cfg.shadow_only is False


def test_fetch_latest_decision_parses_latest_response_and_sends_admin_cookie():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/api/v1/decision-signals/latest/AAPL"
        assert request.headers["Cookie"] == "admin_session=abc"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "stock_code": "AAPL",
                        "action": "buy",
                        "confidence": 0.81,
                        "entry_low": 195.0,
                        "entry_high": 198.0,
                        "stop_loss": 190.0,
                        "target_price": 205.0,
                        "reason": "Breakout with improving breadth",
                        "status": "active",
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://testserver")
    cfg = DsaSidecarConfig(
        enabled=True,
        base_url="http://testserver/api/v1",
        symbol="AAPL",
        admin_cookie="abc",
    )

    decision = DsaSidecarClient(cfg, client=client).fetch_latest_decision()

    assert len(requests) == 1
    assert decision == DsaDecision(
        stock_code="AAPL",
        action="buy",
        confidence=0.81,
        entry_low=195.0,
        entry_high=198.0,
        stop_loss=190.0,
        target_price=205.0,
        reason="Breakout with improving breadth",
        status="active",
        raw={
            "stock_code": "AAPL",
            "action": "buy",
            "confidence": 0.81,
            "entry_low": 195.0,
            "entry_high": 198.0,
            "stop_loss": 190.0,
            "target_price": 205.0,
            "reason": "Breakout with improving breadth",
            "status": "active",
        },
    )


def test_build_dsa_research_snapshot_fails_closed_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    cfg = DsaSidecarConfig(enabled=True, base_url="http://testserver/api/v1", symbol="AAPL")
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://testserver")

    snapshot = build_dsa_research_snapshot(cfg, client=client)

    assert snapshot["enabled"] is True
    assert snapshot["status"] == "unavailable"
    assert snapshot["symbol"] == "AAPL"
    assert snapshot["error_type"] == "ConnectError"
    assert snapshot["shadow_signal"]["action"] == "HOLD"


def test_non_directional_dsa_actions_map_to_hold():
    decision = DsaDecision(stock_code="AAPL", action="watch", confidence=0.90)

    shadow = map_dsa_decision_to_shadow_signal(
        decision,
        symbol="AAPL",
        min_confidence=0.65,
        current_price=198.0,
    )

    assert shadow["action"] == "HOLD"
    assert shadow["reason"] == "DSA action watch is advisory-only."


def test_dsa_buy_maps_to_shadow_signal_only_when_price_geometry_is_valid():
    decision = DsaDecision(
        stock_code="AAPL",
        action="buy",
        confidence=0.82,
        stop_loss=190.0,
        target_price=206.0,
        reason="Plan has clear invalidation",
    )

    shadow = map_dsa_decision_to_shadow_signal(
        decision,
        symbol="AAPL",
        min_confidence=0.65,
        current_price=198.0,
    )

    assert shadow["symbol"] == "AAPL"
    assert shadow["action"] == "BUY"
    assert shadow["confidence"] == 0.82
    assert shadow["shadow_only"] is True
    assert shadow["stop_loss_distance"] == 8.0
    assert shadow["take_profit_distance"] == 8.0


def test_dsa_expired_or_low_confidence_decisions_map_to_hold():
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    expired_decision = DsaDecision(
        stock_code="AAPL",
        action="sell",
        confidence=0.95,
        stop_loss=202.0,
        target_price=190.0,
        expires_at=expired,
    )

    expired_shadow = map_dsa_decision_to_shadow_signal(
        expired_decision,
        symbol="AAPL",
        min_confidence=0.65,
        current_price=198.0,
    )

    low_confidence_shadow = map_dsa_decision_to_shadow_signal(
        DsaDecision(stock_code="AAPL", action="buy", confidence=0.50),
        symbol="AAPL",
        min_confidence=0.65,
        current_price=198.0,
    )

    assert expired_shadow["action"] == "HOLD"
    assert expired_shadow["reason"] == "DSA decision is expired."
    assert low_confidence_shadow["action"] == "HOLD"
    assert low_confidence_shadow["reason"] == "DSA confidence 0.50 is below threshold 0.65."
