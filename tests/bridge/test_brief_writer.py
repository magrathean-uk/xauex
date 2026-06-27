from dataclasses import replace

from xauex.signal.assets import resolve_asset
from xauex.signal.brief_writer import write_brief
from xauex.signal.config import SignalConfig


def test_brief_writer_uses_openrouter_json_object_routing(monkeypatch, tmp_path):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = replace(
        SignalConfig.from_env(),
        brief_llm_base_url="https://openrouter.ai/api/v1",
        brief_llm_model="google/gemini-3.1-flash-lite",
    )
    asset = resolve_asset("XAUUSD")
    output_path = tmp_path / "brief.md"

    class _Usage:
        prompt_tokens = 3150
        completion_tokens = 200
        total_tokens = 3350

    class _Message:
        content = '{"title":"Hold","summary_markdown":"**HOLD** while waiting.","key_points":["No trade"]}'

    class _Choice:
        message = _Message()

    class _Response:
        choices = [_Choice()]
        usage = _Usage()

    class _Completions:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return _Response()

    class _Client:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": _Completions()})()

    client = _Client()
    monkeypatch.setattr("xauex.signal.brief_writer.create_chat_client", lambda **kwargs: client)

    meta = write_brief(
        asset=asset,
        signal={"symbol": "XAUUSD", "action": "HOLD", "confidence": 0.0, "reasoning": "No edge."},
        actions=[],
        report_markdown="No fresh edge.",
        simulation_id=None,
        report_id=None,
        output_path=str(output_path),
        config=cfg,
    )

    call = client.chat.completions.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    assert call["provider"] == {"require_parameters": True}
    assert call["max_tokens"] == 260
    assert "max_completion_tokens" not in call
    assert meta["usage"]["estimated_cost_usd"] == 0.0010875


def test_brief_writer_uses_larger_budget_for_gemini_35(monkeypatch, tmp_path):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = replace(
        SignalConfig.from_env(),
        brief_llm_base_url="https://aiplatform.googleapis.com/v1/projects/test/locations/global/endpoints/openapi",
        brief_llm_model="google/gemini-3.5-flash",
    )
    asset = resolve_asset("XAUUSD")
    output_path = tmp_path / "brief.md"

    class _Usage:
        prompt_tokens = 3150
        completion_tokens = 200
        total_tokens = 3350

    class _Message:
        content = '{"title":"Buy","summary_markdown":"**BUY** while momentum holds.","key_points":["Momentum"]}'

    class _Choice:
        message = _Message()

    class _Response:
        choices = [_Choice()]
        usage = _Usage()
        model = "google/gemini-3.5-flash"

    class _Completions:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return _Response()

    class _Client:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": _Completions()})()

    client = _Client()
    monkeypatch.setattr("xauex.signal.brief_writer.create_chat_client", lambda **kwargs: client)

    write_brief(
        asset=asset,
        signal={"symbol": "XAUUSD", "action": "BUY", "confidence": 0.67, "reasoning": "Momentum."},
        actions=[],
        report_markdown="Momentum remains positive.",
        simulation_id=None,
        report_id=None,
        output_path=str(output_path),
        config=cfg,
    )

    call = client.chat.completions.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    assert call["max_tokens"] >= 1600


def test_brief_writer_retries_empty_length_response(monkeypatch, tmp_path):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = replace(
        SignalConfig.from_env(),
        brief_llm_base_url="https://aiplatform.googleapis.com/v1/projects/test/locations/global/endpoints/openapi",
        brief_llm_model="google/gemini-3.5-flash",
    )
    asset = resolve_asset("XAUUSD")
    output_path = tmp_path / "brief.md"

    class _Usage:
        prompt_tokens = 3150
        completion_tokens = 200
        total_tokens = 3350

    class _Message:
        def __init__(self, content):
            self.content = content

    class _Choice:
        def __init__(self, content):
            self.message = _Message(content)

    class _Response:
        def __init__(self, content, finish_reason):
            self.choices = [_Choice(content)]
            self.usage = _Usage()
            self.model = "google/gemini-3.5-flash"
            self.raw = {"choices": [{"finish_reason": finish_reason}]}

    class _Completions:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return _Response(None, "length")
            return _Response(
                '{"title":"Recovered","summary_markdown":"**BUY** after retry.","key_points":["Retry worked"]}',
                "stop",
            )

    class _Client:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": _Completions()})()

    client = _Client()
    monkeypatch.setattr("xauex.signal.brief_writer.create_chat_client", lambda **kwargs: client)

    meta = write_brief(
        asset=asset,
        signal={"symbol": "XAUUSD", "action": "BUY", "confidence": 0.67, "reasoning": "Momentum."},
        actions=[],
        report_markdown="Momentum remains positive.",
        simulation_id=None,
        report_id=None,
        output_path=str(output_path),
        config=cfg,
    )

    assert meta["title"] == "Recovered"
    assert len(client.chat.completions.calls) == 2
    assert client.chat.completions.calls[1]["max_tokens"] > client.chat.completions.calls[0]["max_tokens"]
