import json
from dataclasses import replace

from xauex.signal.assets import resolve_asset
from xauex.signal.candidate_graph import run_tradingagents_candidate
from xauex.signal.config import SignalConfig


def test_run_tradingagents_candidate_returns_all_stages_and_scratchpad(monkeypatch, tmp_path):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = replace(SignalConfig.from_env(), archive_dir=str(tmp_path / "archive"))
    asset = resolve_asset("XAUUSD")
    packet = {
        "asset": "XAUUSD",
        "window_label": "morning",
        "session_profile": {"slot": "MORNING"},
        "price_features": {"price_bias": "BUY"},
        "market_snapshot": {"series": {}},
        "input_freshness": {"market_snapshot_state": "fresh"},
        "top_context_items": [{"title": "Yields soften", "summary": "Gold bid."}],
        "recent_actions": "- [price_structure] BUY: breakout",
        "report_excerpt": "Gold breaks higher as yields soften.",
    }

    responses = [
        '{"summary":"Fresh bullish structure.","price_structure":"Breakout above resistance.","snapshot_freshness":"fresh","key_context":["Yields soften"]}',
        '{"thesis":"BUY gold on softer yields.","confidence":0.65,"key_points":["Breakout","USD fades"],"risks":["Reversal risk"]}',
        '{"thesis":"SELL risk from failed breakout.","confidence":0.42,"key_points":["Crowded long"],"risks":["Squeeze risk"]}',
        '{"action":"BUY","confidence":0.64,"reasoning":"Bull thesis dominates.","stop_loss_distance":12.0,"take_profit_distance":24.0}',
        '{"decision":"APPROVE","confidence_adjustment":0.0,"reasoning":"Geometry and freshness are acceptable.","hard_blocker":false}',
        '{"action":"BUY","confidence":0.64,"reasoning":"Approved candidate decision.","stop_loss_distance":12.0,"take_profit_distance":24.0}',
    ]

    class _Usage:
        prompt_tokens = 50
        completion_tokens = 10
        total_tokens = 60

    class _Message:
        def __init__(self, content):
            self.content = content

    class _Choice:
        def __init__(self, content):
            self.message = _Message(content)

    class _Response:
        def __init__(self, content):
            self.choices = [_Choice(content)]
            self.usage = _Usage()

    class _Completions:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return _Response(responses[len(self.calls) - 1])

    class _Client:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": _Completions()})()

    graph, usage = run_tradingagents_candidate(
        asset=asset,
        decision_packet=packet,
        config=cfg,
        client_factory=lambda **kwargs: _Client(),
    )

    assert graph["mode"] == "tradingagents_candidate"
    assert graph["degraded"] is False
    assert graph["stages"] == [
        "market_analyst",
        "bull_case",
        "bear_case",
        "trader_proposal",
        "risk_reviewer",
        "portfolio_decision",
    ]
    assert graph["final_decision"]["action"] == "BUY"
    assert [stage["stage"] for stage in usage] == graph["stages"]
    scratchpad_path = tmp_path / "archive" / "candidate_scratchpads" / graph["scratchpad_path"].rsplit("/", 1)[-1]
    rows = [json.loads(line) for line in scratchpad_path.read_text(encoding="utf-8").splitlines()]
    assert "stage_start" in [row["event"] for row in rows]
    assert rows[-1]["event"] == "final_decision"


def test_tradingagents_candidate_uses_openrouter_schema_routing(monkeypatch, tmp_path):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = replace(
        SignalConfig.from_env(),
        archive_dir=str(tmp_path / "archive"),
        parser_llm_base_url="https://openrouter.ai/api/v1",
        parser_llm_model="anthropic/claude-opus-4.7",
        debate_analyst_model="anthropic/claude-opus-4.7",
    )
    asset = resolve_asset("XAUUSD")
    packet = {
        "asset": "XAUUSD",
        "window_label": "morning",
        "session_profile": {"slot": "MORNING"},
        "price_features": {"price_bias": "BUY"},
        "market_snapshot": {"series": {}},
        "input_freshness": {"market_snapshot_state": "fresh"},
        "top_context_items": [{"title": "Yields soften", "summary": "Gold bid."}],
        "recent_actions": "- [price_structure] BUY: breakout",
        "report_excerpt": "Gold breaks higher as yields soften.",
    }

    responses = [
        '{"summary":"Fresh bullish structure.","price_structure":"Breakout above resistance.","snapshot_freshness":"fresh","key_context":["Yields soften"]}',
        '{"thesis":"BUY gold on softer yields.","confidence":0.65,"key_points":["Breakout","USD fades"],"risks":["Reversal risk"]}',
        '{"thesis":"SELL risk from failed breakout.","confidence":0.42,"key_points":["Crowded long"],"risks":["Squeeze risk"]}',
        '{"action":"BUY","confidence":0.64,"reasoning":"Bull thesis dominates.","stop_loss_distance":12.0,"take_profit_distance":24.0}',
        '{"decision":"APPROVE","confidence_adjustment":0.0,"reasoning":"Geometry and freshness are acceptable.","hard_blocker":false}',
        '{"action":"BUY","confidence":0.64,"reasoning":"Approved candidate decision.","stop_loss_distance":12.0,"take_profit_distance":24.0}',
    ]

    class _Usage:
        prompt_tokens = 50
        completion_tokens = 10
        total_tokens = 60

    class _Message:
        def __init__(self, content):
            self.content = content

    class _Choice:
        def __init__(self, content):
            self.message = _Message(content)

    class _Response:
        def __init__(self, content):
            self.choices = [_Choice(content)]
            self.usage = _Usage()

    class _Completions:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return _Response(responses[len(self.calls) - 1])

    class _Client:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": _Completions()})()

    client = _Client()
    graph, usage = run_tradingagents_candidate(
        asset=asset,
        decision_packet=packet,
        config=cfg,
        client_factory=lambda **kwargs: client,
    )

    assert graph["degraded"] is False
    assert len(usage) == 6
    first_call = client.chat.completions.calls[0]
    assert first_call["response_format"]["type"] == "json_schema"
    assert first_call["provider"] == {"require_parameters": True}
    assert first_call["max_tokens"] == 240
    assert "max_completion_tokens" not in first_call


def test_run_tradingagents_candidate_degrades_on_stage_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = replace(SignalConfig.from_env(), archive_dir=str(tmp_path / "archive"))
    asset = resolve_asset("XAUUSD")

    class _Completions:
        def create(self, **kwargs):
            raise RuntimeError("provider unavailable")

    class _Client:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": _Completions()})()

    graph, usage = run_tradingagents_candidate(
        asset=asset,
        decision_packet={"asset": "XAUUSD", "window_label": "morning"},
        config=cfg,
        client_factory=lambda **kwargs: _Client(),
    )

    assert graph["degraded"] is True
    assert graph["final_decision"]["action"] == "HOLD"
    assert usage == []
