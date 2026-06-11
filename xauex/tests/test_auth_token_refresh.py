# ruff: noqa: E402
"""Token refresh must update the live config — cTrader rotates refresh tokens
on every use, so a stale in-memory token replayed by a second refresh path
crash-loops the bot (observed live 2026-06-12: 3,580 restarts)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from xauex import auth as auth_module


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return str(self._payload)


class _FakeSession:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self._status = status
        self.posted: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, url, data=None):
        self.posted.append({"url": url, "data": data})
        return _FakeResponse(self._payload, self._status)


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        ctrader_client_id="client-id",
        ctrader_client_secret="client-secret",
        ctrader_access_token="old-access",
        ctrader_refresh_token="old-refresh",
        ctrader_token_expiry=1,
    )


@pytest.mark.asyncio
async def test_refresh_token_updates_live_config(monkeypatch, tmp_path):
    session = _FakeSession(
        {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}
    )
    monkeypatch.setattr(auth_module.aiohttp, "ClientSession", lambda *a, **k: session)
    monkeypatch.setattr(auth_module, "_ENV_FILE", tmp_path / ".env")
    config = _config()

    await auth_module.refresh_token(config)

    assert config.ctrader_access_token == "new-access"
    assert config.ctrader_refresh_token == "new-refresh"
    assert config.ctrader_token_expiry > 1_000_000_000
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "CTRADER_REFRESH_TOKEN=new-refresh" in env_text


@pytest.mark.asyncio
async def test_refresh_token_raises_clear_error_without_access_token(monkeypatch, tmp_path):
    session = _FakeSession({"errorCode": "INVALID_GRANT", "description": "token burned"})
    monkeypatch.setattr(auth_module.aiohttp, "ClientSession", lambda *a, **k: session)
    monkeypatch.setattr(auth_module, "_ENV_FILE", tmp_path / ".env")
    config = _config()

    with pytest.raises(RuntimeError, match="INVALID_GRANT"):
        await auth_module.refresh_token(config)

    # config must stay untouched on failure
    assert config.ctrader_refresh_token == "old-refresh"
    assert not (tmp_path / ".env").exists()
