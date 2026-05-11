from types import SimpleNamespace

import pytest

from bot.api.client import ApiClient


class _FakeTransport:
    def __init__(self, host, port, tls_server_name=None):
        self.host = host
        self.port = port
        self.tls_server_name = tls_server_name
        self._message_callback = None
        self._disconnect_callback = None
        self.connected = False

    def set_message_callback(self, callback):
        self._message_callback = callback

    def set_disconnect_callback(self, callback):
        self._disconnect_callback = callback

    async def connect(self, timeout=30.0):
        self.connected = True

    async def close(self):
        self.connected = False


def _config():
    return SimpleNamespace(
        ctrader_account_id="46610962",
        ctrader_host="demo-uk-eqx-01.p.c-trader.com",
        ctrader_port=5035,
        ctrader_tls_server_name="connect.spotware.com",
        ctrader_client_id="client-id",
        ctrader_client_secret="client-secret",
        ctrader_access_token="access-token",
        ctrader_refresh_token="refresh-token",
        ctrader_token_expiry=2_000_000_000,
    )


@pytest.mark.asyncio
async def test_connect_refreshes_expired_token_before_authentication(monkeypatch):
    config = _config()
    config.ctrader_token_expiry = 0
    client = ApiClient(config)
    calls = []

    monkeypatch.setattr("bot.api.client.CTraderTransport", _FakeTransport)

    async def fake_refresh_token_if_needed(self):
        calls.append("refresh_token_if_needed")
        return True

    async def fake_app_auth(self):
        calls.append("app_auth")

    async def fake_validate_access(self):
        calls.append("validate_access")

    async def fake_account_auth(self):
        calls.append("account_auth")

    monkeypatch.setattr(ApiClient, "refresh_token_if_needed", fake_refresh_token_if_needed)
    monkeypatch.setattr(ApiClient, "_app_auth", fake_app_auth)
    monkeypatch.setattr(
        ApiClient,
        "_validate_access_token_accounts",
        fake_validate_access,
        raising=False,
    )
    monkeypatch.setattr(ApiClient, "_account_auth", fake_account_auth)

    await client.connect()

    assert calls == [
        "refresh_token_if_needed",
        "app_auth",
        "validate_access",
        "account_auth",
    ]


@pytest.mark.asyncio
async def test_connect_validates_access_token_accounts_before_account_auth(monkeypatch):
    client = ApiClient(_config())
    calls = []

    monkeypatch.setattr("bot.api.client.CTraderTransport", _FakeTransport)

    async def fake_app_auth(self):
        calls.append("app_auth")

    async def fake_validate_access(self):
        calls.append("validate_access")

    async def fake_account_auth(self):
        calls.append("account_auth")

    monkeypatch.setattr(ApiClient, "_app_auth", fake_app_auth)
    monkeypatch.setattr(ApiClient, "_validate_access_token_accounts", fake_validate_access, raising=False)
    monkeypatch.setattr(ApiClient, "_account_auth", fake_account_auth)

    await client.connect()

    assert calls == ["app_auth", "validate_access", "account_auth"]


@pytest.mark.asyncio
async def test_connect_passes_tls_server_name_override_to_transport(monkeypatch):
    config = _config()
    config.ctrader_tls_server_name = "connect.spotware.com"
    client = ApiClient(config)
    captured = {}

    class _CapturingTransport(_FakeTransport):
        def __init__(self, host, port, tls_server_name=None):
            super().__init__(host, port)
            captured["host"] = host
            captured["port"] = port
            captured["tls_server_name"] = tls_server_name

    monkeypatch.setattr("bot.api.client.CTraderTransport", _CapturingTransport)

    async def fake_app_auth(self):
        return None

    async def fake_validate_access(self):
        return None

    async def fake_account_auth(self):
        return None

    monkeypatch.setattr(ApiClient, "_app_auth", fake_app_auth)
    monkeypatch.setattr(ApiClient, "_validate_access_token_accounts", fake_validate_access, raising=False)
    monkeypatch.setattr(ApiClient, "_account_auth", fake_account_auth)

    await client.connect()

    assert captured == {
        "host": "demo-uk-eqx-01.p.c-trader.com",
        "port": 5035,
        "tls_server_name": "connect.spotware.com",
    }


@pytest.mark.asyncio
async def test_app_auth_raises_on_proto_error_response():
    client = ApiClient(_config())

    async def fake_send_and_wait(message, timeout=10.0):
        return SimpleNamespace(
            errorCode="CANT_ROUTE_REQUEST",
            description="Cannot route request",
        )

    client._send_and_wait = fake_send_and_wait

    with pytest.raises(RuntimeError, match="CANT_ROUTE_REQUEST"):
        await client._app_auth()


@pytest.mark.asyncio
async def test_validate_access_token_accounts_allows_empty_account_list():
    client = ApiClient(_config())

    async def fake_send_and_wait(message, timeout=10.0):
        return SimpleNamespace(ctidTraderAccountId=[])

    client._send_and_wait = fake_send_and_wait

    await client._validate_access_token_accounts()
