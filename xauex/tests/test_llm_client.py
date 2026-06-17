from xauex.shared import llm_client


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
