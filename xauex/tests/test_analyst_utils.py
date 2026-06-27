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


def test_resolve_llm_settings_defaults_to_groq_base_url(monkeypatch, tmp_path):
    monkeypatch.setattr(_utils, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(_utils, "XAUEX_ROOT", tmp_path / "xauex")
    monkeypatch.setenv("XAUEX_ANALYST_API_KEY", "test-key")
    monkeypatch.delenv("XAUEX_ANALYST_BASE_URL", raising=False)
    monkeypatch.delenv("XAUEX_SIGNAL_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("XAUEX_SIGNAL_PARSER_LLM_BASE_URL", raising=False)

    _, base_url, _ = _utils._resolve_llm_settings()

    assert base_url == "https://api.groq.com/openai/v1"


def test_call_llm_uses_gemini_analyst_output_budget(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeResponse:
        choices = [type("_Choice", (), {"message": type("_Message", (), {"content": "weekly review"})()})()]
        usage = type("_Usage", (), {"completion_tokens": 12, "prompt_tokens": 30, "total_tokens": 42})()

    class _FakeCompletions:
        def create(self, **kwargs):
            captured["create_kwargs"] = kwargs
            return _FakeResponse()

    class _FakeClient:
        def __init__(self):
            self.chat = type("_Chat", (), {"completions": _FakeCompletions()})()

    monkeypatch.setenv("XAUEX_ANALYST_API_KEY", "google-service-account")
    monkeypatch.setenv("XAUEX_ANALYST_BASE_URL", "https://aiplatform.googleapis.com/v1/projects/p/locations/global/endpoints/openapi")
    monkeypatch.setenv("XAUEX_ANALYST_MODEL", "google/gemini-3.5-flash")
    monkeypatch.setattr(_utils, "OpenAI", None)
    monkeypatch.setattr(_utils, "create_chat_client", lambda **kwargs: _FakeClient())

    result = _utils.call_llm("weekly prompt")

    assert result == "weekly review"
    assert captured["create_kwargs"]["model"] == "google/gemini-3.5-flash"
    assert captured["create_kwargs"]["max_tokens"] == 3200
    assert captured["create_kwargs"]["reasoning_effort"] == "low"
    assert captured["create_kwargs"]["temperature"] == 0.2


def test_call_llm_empty_length_response_names_token_budget_root_cause(monkeypatch):
    class _FakeResponse:
        choices = [type("_Choice", (), {"message": None, "finish_reason": "length"})()]
        usage = type(
            "_Usage",
            (),
            {
                "completion_tokens": 179,
                "prompt_tokens": 763,
                "total_tokens": 2359,
            },
        )()
        raw = {
            "usage": {
                "completion_tokens": 179,
                "completion_tokens_details": {"reasoning_tokens": 1417},
                "prompt_tokens": 763,
                "total_tokens": 2359,
            }
        }

    class _FakeCompletions:
        def create(self, **kwargs):
            return _FakeResponse()

    class _FakeClient:
        def __init__(self):
            self.chat = type("_Chat", (), {"completions": _FakeCompletions()})()

    monkeypatch.setenv("XAUEX_ANALYST_API_KEY", "google-service-account")
    monkeypatch.setenv("XAUEX_ANALYST_BASE_URL", "https://aiplatform.googleapis.com/v1/projects/p/locations/global/endpoints/openapi")
    monkeypatch.setenv("XAUEX_ANALYST_MODEL", "google/gemini-3.5-flash")
    monkeypatch.setattr(_utils, "OpenAI", None)
    monkeypatch.setattr(_utils, "create_chat_client", lambda **kwargs: _FakeClient())

    try:
        _utils.call_llm("weekly prompt")
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected RuntimeError")

    assert "ran out of output tokens" in message
    assert "finish_reason=length" in message
    assert "reasoning_tokens=1417" in message
