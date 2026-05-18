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
