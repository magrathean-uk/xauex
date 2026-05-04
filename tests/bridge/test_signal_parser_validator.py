from xauex.signal.assets import resolve_asset
from xauex.signal.config import SignalConfig
from xauex.signal.signal_parser import (
    _apply_validator_result,
    _build_analyst_debate,
    _build_decision_packet,
    _enrich_decision_packet_with_debate,
    _model_completion_options,
    _request_json_completion,
    _validator_system_prompt,
)


def test_build_decision_packet_prefers_structured_inputs():
    asset = resolve_asset("XAUUSD")
    payload = {
        "price_features": {"price_bias": "SELL", "momentum_3": -3.2},
        "memory_summary": {"trade_count": 2, "net_pnl": 45.0, "notes": ["Shorts worked on higher yields."]},
        "market_snapshot": {
            "series": {
                "usd_broad_index": {"value": 122.1, "change_1d": 0.4, "bias": "SELL"},
                "us10y_yield": {"value": 4.35, "change_1d": 0.07, "bias": "SELL"},
            }
        },
        "event_flags": {"cpi_release_recent": False, "fed_event_recent": True},
        "input_freshness": {"context_age_seconds": 1200, "market_snapshot_age_seconds": 900},
        "context_items": [
            {
                "source_name": "Federal Reserve press releases RSS",
                "tier": 1,
                "freshness_score": 0.95,
                "title": "Fed statement remains hawkish",
                "summary": "The Committee remains focused on inflation risks.",
            }
        ],
    }

    packet = _build_decision_packet(
        asset=asset,
        prediction_payload=payload,
        actions=[{"agent_name": "price_structure", "action_type": "SELL", "content": "momentum negative"}],
        report_markdown="# Report\nRates up, USD firm, gold pressured.",
        window_label="midday",
    )

    assert packet["window_label"] == "midday"
    assert packet["market_snapshot"]["series"]["us10y_yield"]["bias"] == "SELL"
    assert packet["top_context_items"][0]["tier"] == 1
    assert "Rates up, USD firm, gold pressured." in packet["report_excerpt"]


def test_build_decision_packet_includes_us_open_session_profile():
    asset = resolve_asset("XAUUSD")

    packet = _build_decision_packet(
        asset=asset,
        prediction_payload={
            "price_features": {"price_bias": "BUY"},
            "memory_summary": {"trade_count": 1},
            "market_snapshot": {"series": {}},
            "event_flags": {"fed_event_recent": False},
            "input_freshness": {"market_snapshot_state": "fresh"},
            "context_items": [],
        },
        actions=[{"agent_name": "price_structure", "action_type": "BUY", "content": "breakout"}],
        report_markdown="# Report\nGold often reacts sharply into the US cash open.",
        window_label="us_open",
    )

    assert packet["window_label"] == "us_open"
    assert packet["session_profile"]["timezone"] == "America/New_York"
    assert "US open" in packet["session_profile"]["description"]
    assert "US rates" in " ".join(packet["session_profile"]["dominant_drivers"])


def test_validator_disagreement_downgrades_confidence_without_forcing_hold():
    asset = resolve_asset("XAUUSD")
    signal = {
        "schema_version": 2,
        "symbol": asset.symbol,
        "asset_class": asset.asset_class,
        "action": "SELL",
        "confidence": 0.82,
        "reasoning": "Higher yields and a firmer dollar pressure gold.",
        "stop_loss_distance": 12.0,
        "take_profit_distance": 24.0,
        "distance_unit": asset.distance_unit,
        "timestamp_utc": "2026-04-14T00:00:00Z",
        "execution_supported": True,
    }
    merged = _apply_validator_result(
        asset=asset,
        signal=signal,
        validator_result={
            "decision": "DISAGREE",
            "confidence_adjustment": -0.16,
            "reasoning": "Price still leans lower, but the macro case is less clean than the primary parser suggests.",
            "hard_blocker": False,
        },
    )

    assert merged["action"] == "SELL"
    assert merged["confidence"] == 0.66
    assert merged["consensus_state"] == "disagreed"
    assert merged["validator_status"] == "reviewed"


def test_validator_hard_blocker_forces_hold():
    asset = resolve_asset("XAUUSD")
    signal = {
        "schema_version": 2,
        "symbol": asset.symbol,
        "asset_class": asset.asset_class,
        "action": "BUY",
        "confidence": 0.74,
        "reasoning": "Falling real yields support gold.",
        "stop_loss_distance": 12.0,
        "take_profit_distance": 24.0,
        "distance_unit": asset.distance_unit,
        "timestamp_utc": "2026-04-14T00:00:00Z",
        "execution_supported": True,
    }
    merged = _apply_validator_result(
        asset=asset,
        signal=signal,
        validator_result={
            "decision": "BLOCK",
            "confidence_adjustment": -0.3,
            "reasoning": "Structured inputs are stale during the live window.",
            "hard_blocker": True,
        },
    )

    assert merged["action"] == "HOLD"
    assert merged["confidence"] == 0.0
    assert merged["stop_loss_distance"] == 0.0
    assert merged["take_profit_distance"] == 0.0
    assert merged["consensus_state"] == "blocked"


def test_validator_prompt_reserves_disagreement_for_direct_contradictions():
    prompt = _validator_system_prompt(resolve_asset("XAUUSD"))

    assert "prefer ALIGNED" in prompt
    assert "Reserve DISAGREE for direct contradictions" in prompt
    assert "prefer DISAGREE over ALIGNED" not in prompt
    assert "only when the same setup is being repeated" in prompt


def test_gpt_oss_models_use_low_reasoning_and_larger_visible_output_budget():
    options = _model_completion_options(
        "openai/gpt-oss-120b",
        max_tokens=350,
        response_schema={
            "name": "signal_decision",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        },
    )

    assert options["reasoning_effort"] == "low"
    assert options["max_completion_tokens"] == 700
    assert options["response_format"]["type"] == "json_schema"
    assert options["response_format"]["json_schema"]["strict"] is True
    assert "include_reasoning" not in options
    assert options["extra_body"]["include_reasoning"] is False


def test_non_reasoning_models_keep_requested_completion_budget():
    options = _model_completion_options("llama-3.3-70b-versatile", max_tokens=350)

    assert "reasoning_effort" not in options
    assert options["max_completion_tokens"] == 350
    assert "response_format" not in options
    assert "extra_body" not in options


def test_request_json_completion_retries_with_json_object_mode_after_invalid_strict_response():
    class _Usage:
        prompt_tokens = 120
        completion_tokens = 18
        total_tokens = 138

    class _Message:
        def __init__(self, content: str):
            self.content = content

    class _Choice:
        def __init__(self, content: str):
            self.message = _Message(content)

    class _Response:
        def __init__(self, content: str):
            self.choices = [_Choice(content)]
            self.usage = _Usage()

    class _Completions:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return _Response("not valid json")
            return _Response('{"action":"SELL","confidence":0.61}')

    class _Client:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": _Completions()})()

    client = _Client()

    response, parsed, mode = _request_json_completion(
        client=client,
        model="openai/gpt-oss-120b",
        messages=[{"role": "system", "content": "Return JSON."}],
        temperature=0.1,
        max_tokens=350,
        response_schema={
            "name": "signal_decision",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["action", "confidence"],
                "additionalProperties": False,
            },
        },
    )

    assert parsed == {"action": "SELL", "confidence": 0.61}
    assert mode == "json_object"
    assert len(client.chat.completions.calls) == 2
    assert client.chat.completions.calls[0]["response_format"]["type"] == "json_schema"
    assert client.chat.completions.calls[1]["response_format"]["type"] == "json_object"
    assert client.chat.completions.calls[0]["max_completion_tokens"] == 700
    assert client.chat.completions.calls[0]["extra_body"]["include_reasoning"] is False


def test_build_analyst_debate_returns_bull_and_bear_cases(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()
    asset = resolve_asset("XAUUSD")
    packet = _build_decision_packet(
        asset=asset,
        prediction_payload={
            "price_features": {"price_bias": "SELL", "momentum_3": -2.4},
            "memory_summary": {"trade_count": 3, "net_pnl": 55.0, "notes": ["Recent shorts worked."]},
            "market_snapshot": {"series": {"usd_broad_index": {"value": 121.0, "bias": "SELL"}}},
            "event_flags": {"fed_event_recent": False},
            "input_freshness": {"market_snapshot_state": "fresh"},
            "context_items": [],
        },
        actions=[{"agent_name": "price_structure", "action_type": "SELL", "content": "momentum negative"}],
        report_markdown="# Report\nRates firm, gold pressured.",
        window_label="morning",
    )

    class _Usage:
        prompt_tokens = 200
        completion_tokens = 40
        total_tokens = 240

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
            if len(self.calls) == 1:
                return _Response('{"stance":"BULL","summary":"Gold could bounce if yields fade.","key_points":["Yields may soften","Gold held support"],"risk_flags":["USD strength persists"]}')
            return _Response('{"stance":"BEAR","summary":"USD strength and yields still pressure gold.","key_points":["Dollar is firm","Momentum remains negative"],"risk_flags":["Short squeeze risk"]}')

    class _Client:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": _Completions()})()

    debate, usage = _build_analyst_debate(
        asset=asset,
        decision_packet=packet,
        config=cfg,
        client_factory=lambda **_: _Client(),
    )

    assert debate["mode"] == "analyst_debate"
    assert debate["degraded"] is False
    assert debate["bull_case"]["summary"] == "Gold could bounce if yields fade."
    assert debate["bear_case"]["summary"] == "USD strength and yields still pressure gold."
    assert [stage["stage"] for stage in usage] == ["bull_case", "bear_case"]


def test_build_analyst_debate_degrades_when_a_side_fails(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()
    asset = resolve_asset("XAUUSD")
    packet = _build_decision_packet(
        asset=asset,
        prediction_payload={"price_features": {}, "memory_summary": {}, "market_snapshot": {}, "event_flags": {}, "input_freshness": {}, "context_items": []},
        actions=[],
        report_markdown="# Report\nMixed.",
        window_label="midday",
    )

    class _Usage:
        prompt_tokens = 150
        completion_tokens = 20
        total_tokens = 170

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
            if len(self.calls) == 1:
                return _Response('{"stance":"BULL","summary":"Gold may rebound on softer yields.","key_points":["Yield pullback possible"],"risk_flags":["USD still firm"]}')
            return _Response("not valid json")

    class _Client:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": _Completions()})()

    debate, usage = _build_analyst_debate(
        asset=asset,
        decision_packet=packet,
        config=cfg,
        client_factory=lambda **_: _Client(),
    )

    assert debate["degraded"] is True
    assert debate["bull_case"]["summary"] == "Gold may rebound on softer yields."
    assert debate["bear_case"]["status"] == "unavailable"
    assert debate["summary"] == "Analyst debate degraded; baseline decision packet remains authoritative."
    assert [stage["stage"] for stage in usage] == ["bull_case"]


def test_degraded_analyst_debate_does_not_modify_parser_packet():
    asset = resolve_asset("XAUUSD")
    packet = _build_decision_packet(
        asset=asset,
        prediction_payload={"price_features": {}, "memory_summary": {}, "market_snapshot": {}, "event_flags": {}, "input_freshness": {}, "context_items": []},
        actions=[],
        report_markdown="# Report\nMixed.",
        window_label="current",
    )

    enriched = _enrich_decision_packet_with_debate(
        packet,
        {
            "mode": "analyst_debate",
            "degraded": True,
            "summary": "Analyst debate degraded; baseline decision packet remains authoritative.",
        },
    )

    assert "debate" not in enriched
