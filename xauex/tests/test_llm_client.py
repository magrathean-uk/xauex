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
