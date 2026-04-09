from analyst import _utils


def test_default_model_prefers_analyst_override(monkeypatch):
    monkeypatch.setenv("LLM_MODEL_NAME", "llama-3.1-8b-instant")
    monkeypatch.setenv("XAUEX_ANALYST_MODEL", "llama-3.3-70b-versatile")

    assert _utils.default_model() == "llama-3.3-70b-versatile"


def test_call_llm_uses_openai_compatible_client(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeResponse:
        def __init__(self, content: str):
            self.choices = [type("_Choice", (), {"message": type("_Message", (), {"content": content})()})()]

    class _FakeCompletions:
        def create(self, **kwargs):
            captured["create_kwargs"] = kwargs
            return _FakeResponse("```json\ntrimmed output\n```")

    class _FakeChat:
        def __init__(self):
            self.completions = _FakeCompletions()

    class _FakeClient:
        def __init__(self, *, api_key: str, base_url: str):
            captured["api_key"] = api_key
            captured["base_url"] = base_url
            self.chat = _FakeChat()

    monkeypatch.setenv("XAUEX_ANALYST_API_KEY", "test-key")
    monkeypatch.setenv("XAUEX_ANALYST_BASE_URL", "https://api.example.test/v1")
    monkeypatch.setenv("XAUEX_ANALYST_MODEL", "test-model")
    monkeypatch.setattr(_utils, "OpenAI", _FakeClient)

    result = _utils.call_llm("hello analyst")

    assert result == "trimmed output"
    assert captured["api_key"] == "test-key"
    assert captured["base_url"] == "https://api.example.test/v1"
    assert captured["create_kwargs"]["model"] == "test-model"
    assert captured["create_kwargs"]["messages"][1]["content"] == "hello analyst"
