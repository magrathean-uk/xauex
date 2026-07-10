import httpx
import pytest

from xauex.shared import llm_client


def _response(status_code, *, payload=None, headers=None):
    request = httpx.Request("POST", "https://provider.invalid/chat/completions")
    return httpx.Response(
        status_code,
        json=payload if payload is not None else {},
        headers=headers,
        request=request,
    )


class _SequenceHttpClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def post(self, url, json, timeout=None):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self):
        return None


def _client_with_outcomes(monkeypatch, outcomes):
    fake_http = _SequenceHttpClient(outcomes)
    monkeypatch.setattr(llm_client.httpx, "Client", lambda **kwargs: fake_http)
    client = llm_client.create_chat_client(
        api_key="test-key",
        base_url="https://provider.invalid",
    )
    return client, fake_http


def test_transient_server_errors_are_retried(monkeypatch):
    sleeps = []
    monkeypatch.setattr(llm_client.time, "sleep", sleeps.append)
    client, fake_http = _client_with_outcomes(
        monkeypatch,
        [
            _response(502, payload={"error": {"message": "bad gateway"}}),
            _response(502, payload={"error": {"message": "bad gateway"}}),
            _response(200, payload={"choices": []}),
        ],
    )

    result = client._request(path="/chat/completions", payload={})

    assert result == {"choices": []}
    assert fake_http.calls == 3
    assert sleeps == [1.0, 2.0]


def test_transport_errors_are_retried(monkeypatch):
    sleeps = []
    monkeypatch.setattr(llm_client.time, "sleep", sleeps.append)
    request = httpx.Request("POST", "https://provider.invalid/chat/completions")
    client, fake_http = _client_with_outcomes(
        monkeypatch,
        [
            httpx.ConnectError("connection reset", request=request),
            _response(200, payload={"choices": []}),
        ],
    )

    client._request(path="/chat/completions", payload={})

    assert fake_http.calls == 2
    assert sleeps == [1.0]


def test_retry_exhaustion_preserves_provider_error(monkeypatch):
    sleeps = []
    monkeypatch.setattr(llm_client.time, "sleep", sleeps.append)
    client, fake_http = _client_with_outcomes(
        monkeypatch,
        [
            _response(503, payload={"error": {"message": "upstream unavailable"}})
            for _ in range(4)
        ],
    )

    with pytest.raises(RuntimeError, match="upstream unavailable"):
        client._request(path="/chat/completions", payload={})

    assert fake_http.calls == 4
    assert sleeps == [1.0, 2.0, 4.0]


def test_retry_after_header_is_honored_and_capped(monkeypatch):
    sleeps = []
    monkeypatch.setattr(llm_client.time, "sleep", sleeps.append)
    client, fake_http = _client_with_outcomes(
        monkeypatch,
        [
            _response(429, headers={"Retry-After": "90"}),
            _response(200, payload={"choices": []}),
        ],
    )

    client._request(path="/chat/completions", payload={})

    assert fake_http.calls == 2
    assert sleeps == [30.0]


def test_non_retryable_client_error_fails_immediately(monkeypatch):
    sleeps = []
    monkeypatch.setattr(llm_client.time, "sleep", sleeps.append)
    client, fake_http = _client_with_outcomes(
        monkeypatch,
        [_response(400, payload={"error": {"message": "invalid model"}})],
    )

    with pytest.raises(RuntimeError, match="invalid model"):
        client._request(path="/chat/completions", payload={})

    assert fake_http.calls == 1
    assert sleeps == []


def test_create_chat_client_posts_openai_compatible_request(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeResponse:
        status_code = 200
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "model": "llama-3.1-8b-instant",
                "choices": [
                    {
                        "message": {
                            "content": '{"status":"ok"}',
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 4,
                    "total_tokens": 16,
                },
            }

    class _FakeHttpClient:
        def __init__(self, *, headers, timeout):
            captured["headers"] = headers
            captured["client_timeout"] = timeout

        def post(self, url, json, timeout=None):
            captured["url"] = url
            captured["payload"] = json
            captured["request_timeout"] = timeout
            return _FakeResponse()

        def close(self):
            return None

    monkeypatch.setattr(llm_client.httpx, "Client", _FakeHttpClient)

    client = llm_client.create_chat_client(
        api_key="test-key",
        base_url="https://api.groq.com/openai/v1",
        timeout=45.0,
    )
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": "hi"}],
        max_completion_tokens=128,
        response_format={"type": "json_object"},
        extra_body={"include_reasoning": False},
        timeout=12,
    )

    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["client_timeout"] == 45.0
    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert captured["request_timeout"] == 12
    assert captured["payload"]["model"] == "llama-3.1-8b-instant"
    assert captured["payload"]["max_completion_tokens"] == 128
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["include_reasoning"] is False
    assert response.model == "llama-3.1-8b-instant"
    assert response.choices[0].message.content == '{"status":"ok"}'
    assert response.usage.prompt_tokens == 12
    assert response.usage.total_tokens == 16


def test_create_chat_client_uses_google_service_account_token(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeCredentials:
        token = None

        def refresh(self, request):
            captured["refresh_request"] = request
            self.token = "ya29.service-account-token"

    class _FakeServiceAccountCredentials:
        @staticmethod
        def from_service_account_file(path, scopes):
            captured["credential_path"] = path
            captured["credential_scopes"] = tuple(scopes)
            return _FakeCredentials()

    class _FakeResponse:
        status_code = 200
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "model": "google/gemini-3.5-flash",
                "choices": [{"message": {"content": "OK"}}],
                "usage": {},
            }

    class _FakeHttpClient:
        def __init__(self, *, headers, timeout):
            captured["headers"] = headers
            captured["client_timeout"] = timeout

        def post(self, url, json, timeout=None):
            captured["url"] = url
            captured["payload"] = json
            return _FakeResponse()

        def close(self):
            return None

    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/secure/unique-rarity.json")
    monkeypatch.setenv("XAUEX_GOOGLE_APPLICATION_CREDENTIALS", "/secure/unique-rarity.json")
    monkeypatch.setattr(
        llm_client,
        "service_account",
        type("_ServiceAccountModule", (), {"Credentials": _FakeServiceAccountCredentials}),
        raising=False,
    )
    monkeypatch.setattr(llm_client, "GoogleAuthRequest", lambda: object(), raising=False)
    monkeypatch.setattr(llm_client.httpx, "Client", _FakeHttpClient)

    client = llm_client.create_chat_client(
        api_key="google-service-account",
        base_url="https://aiplatform.googleapis.com/v1/projects/unique-rarity-496408-k8/locations/global/endpoints/openapi",
        timeout=45.0,
    )
    response = client.chat.completions.create(
        model="google/gemini-3.5-flash",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=32,
    )

    assert captured["credential_path"] == "/secure/unique-rarity.json"
    assert captured["credential_scopes"] == ("https://www.googleapis.com/auth/cloud-platform",)
    assert captured["headers"]["Authorization"] == "Bearer ya29.service-account-token"
    assert captured["headers"]["Content-Type"] == "application/json"
    assert captured["url"].endswith("/chat/completions")
    assert captured["payload"]["model"] == "google/gemini-3.5-flash"
    assert response.choices[0].message.content == "OK"
