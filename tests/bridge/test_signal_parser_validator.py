from dataclasses import replace

from xauex.signal.assets import resolve_asset
from xauex.signal.config import SignalConfig
from xauex.signal.signal_parser import (
    HARD_STALE_MARKET_SNAPSHOT_SECONDS,
    _apply_validator_result,
    _build_analyst_debate,
    _build_decision_packet,
    _enrich_decision_packet_with_debate,
    _model_completion_options,
    _parse_json_response,
    _request_json_completion,
    _validator_system_prompt,
    parse_signal,
)

_FRESH_BUSINESS_DAY_LAG = 2


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


def test_parse_signal_hard_holds_when_daily_macro_series_is_too_stale(monkeypatch):
    """When daily-publishing series (yields, breakevens, VIX, BTC) exceed
    the hard-stale threshold the parser must HOLD before any LLM call. The
    aggregate max-age field is ignored because slow-publishing series
    (USD trade-weighted index, WTI oil) have a natural ~7-day lag."""
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()
    asset = resolve_asset("XAUUSD")

    def _explode(**kwargs):
        raise AssertionError("LLM client must not be invoked when snapshot is hard-stale")

    monkeypatch.setattr("xauex.signal.signal_parser.create_chat_client", _explode)

    stale_age = HARD_STALE_MARKET_SNAPSHOT_SECONDS + 60

    signal = parse_signal(
        asset=asset,
        actions=[{"agent_name": "price_structure", "action_type": "SELL", "content": "negative momentum"}],
        report_markdown="# Report\nGold pressured.",
        config=cfg,
        prediction_payload={
            "price_features": {"price_bias": "SELL"},
            "memory_summary": {},
            "market_snapshot": {"series": {}},
            "event_flags": {},
            "input_freshness": {
                "market_snapshot_state": "warning",
                "market_snapshot_age_seconds": stale_age + 86400,
                "daily_publishing_max_age_seconds": stale_age,
                "hard_blocker": False,
            },
            "context_items": [],
        },
        window_label="us_open",
        decision_mode="baseline",
    )

    assert signal["action"] == "HOLD"
    assert signal["confidence"] == 0.0
    assert signal["consensus_state"] == "blocked"
    assert signal["validator_status"] == "skipped"
    assert "stale" in signal["reasoning"].lower()


def test_parse_signal_does_not_hard_hold_when_only_slow_series_are_old(monkeypatch):
    """The DTWEXBGS / DTWEXAFEGS USD trade-weighted indexes lag by ~7 days
    by design. When only those slow-publishing series are old but daily
    series are fresh, the guard must NOT block."""
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()
    asset = resolve_asset("XAUUSD")

    class _Usage:
        prompt_tokens = 50
        completion_tokens = 25
        total_tokens = 75

    class _Response:
        def __init__(self, content):
            self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})]
            self.usage = _Usage()

    parser_responses = iter([
        _Response('{"action":"SELL","confidence":0.6,"reasoning":"r","stop_loss_distance":12,"take_profit_distance":24}'),
        _Response('{"decision":"ALIGNED","confidence_adjustment":0.0,"reasoning":"ok","hard_blocker":false}'),
    ])

    class _Client:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": type("Completions", (), {"create": staticmethod(lambda **kwargs: next(parser_responses))})})()

    monkeypatch.setattr("xauex.signal.signal_parser.create_chat_client", lambda **kwargs: _Client())

    signal = parse_signal(
        asset=asset,
        actions=[{"agent_name": "price_structure", "action_type": "SELL", "content": "neg"}],
        report_markdown="# Report\nGold pressured.",
        config=cfg,
        prediction_payload={
            "price_features": {"price_bias": "SELL"},
            "memory_summary": {},
            "market_snapshot": {"series": {}},
            "event_flags": {},
            "input_freshness": {
                "market_snapshot_state": "warning",
                # 7.5-day USD index lag is normal cadence.
                "market_snapshot_age_seconds": 649507,
                # Yields/VIX at 2.5 days = under the 3-day threshold.
                "daily_publishing_max_age_seconds": 217507,
                "hard_blocker": False,
            },
            "context_items": [],
        },
        window_label="us_open",
        decision_mode="baseline",
    )

    assert signal["action"] in {"BUY", "SELL"}
    assert signal["consensus_state"] != "blocked"


def test_parse_signal_allows_weekend_calendar_lag_when_business_age_is_fresh(monkeypatch):
    """Friday observations can be >3 calendar days old by Tuesday's London
    windows. If business-day age is still within tolerance, do not hard-HOLD
    solely because FRED has not published the next daily row yet."""
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()
    asset = resolve_asset("XAUUSD")

    class _Usage:
        prompt_tokens = 50
        completion_tokens = 25
        total_tokens = 75

    class _Response:
        def __init__(self, content):
            self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})]
            self.usage = _Usage()

    parser_responses = iter([
        _Response('{"action":"BUY","confidence":0.62,"reasoning":"fresh enough by business days","stop_loss_distance":12,"take_profit_distance":24}'),
        _Response('{"decision":"ALIGNED","confidence_adjustment":0.0,"reasoning":"ok","hard_blocker":false}'),
    ])

    class _Client:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": type("Completions", (), {"create": staticmethod(lambda **kwargs: next(parser_responses))})})()

    monkeypatch.setattr("xauex.signal.signal_parser.create_chat_client", lambda **kwargs: _Client())

    signal = parse_signal(
        asset=asset,
        actions=[{"agent_name": "price_structure", "action_type": "BUY", "content": "supportive"}],
        report_markdown="# Report\nGold supported.",
        config=cfg,
        prediction_payload={
            "price_features": {"price_bias": "BUY"},
            "memory_summary": {},
            "market_snapshot": {"series": {}},
            "event_flags": {},
            "input_freshness": {
                "market_snapshot_state": "warning",
                "market_snapshot_age_seconds": HARD_STALE_MARKET_SNAPSHOT_SECONDS + 86400,
                "daily_publishing_max_age_seconds": HARD_STALE_MARKET_SNAPSHOT_SECONDS + 86400,
                "daily_publishing_max_business_age_days": _FRESH_BUSINESS_DAY_LAG,
                "hard_blocker": False,
            },
            "context_items": [],
        },
        window_label="us_open",
        decision_mode="baseline",
    )

    assert signal["action"] in {"BUY", "SELL"}
    assert signal["consensus_state"] != "blocked"


def test_parse_signal_blocks_daily_inputs_stale_by_business_days(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()
    asset = resolve_asset("XAUUSD")

    def _explode(**kwargs):
        raise AssertionError("LLM client must not be invoked when business-day stale")

    monkeypatch.setattr("xauex.signal.signal_parser.create_chat_client", _explode)

    signal = parse_signal(
        asset=asset,
        actions=[{"agent_name": "price_structure", "action_type": "BUY", "content": "supportive"}],
        report_markdown="# Report\nGold supported.",
        config=cfg,
        prediction_payload={
            "price_features": {"price_bias": "BUY"},
            "memory_summary": {},
            "market_snapshot": {"series": {}},
            "event_flags": {},
            "input_freshness": {
                "market_snapshot_state": "warning",
                "market_snapshot_age_seconds": HARD_STALE_MARKET_SNAPSHOT_SECONDS + 86400,
                "daily_publishing_max_age_seconds": HARD_STALE_MARKET_SNAPSHOT_SECONDS + 86400,
                "daily_publishing_max_business_age_days": _FRESH_BUSINESS_DAY_LAG + 1,
                "hard_blocker": False,
            },
            "context_items": [],
        },
        window_label="us_open",
        decision_mode="baseline",
    )

    assert signal["action"] == "HOLD"
    assert signal["confidence"] == 0.0
    assert signal["consensus_state"] == "blocked"
    assert "business days" in signal["reasoning"]


def test_parse_signal_blocks_low_confidence_directional_flip_via_persistence(monkeypatch, tmp_path):
    """The May 8 incident showed MIDDAY=BUY 0.68 → US_OPEN=SELL 0.48 — three
    direction flips inside one trading day. With persistence wired through,
    a low-confidence flip against the locked direction must downgrade to
    HOLD."""
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    state_path = tmp_path / "directional_state.json"
    monkeypatch.setenv("XAUEX_SIGNAL_DIRECTIONAL_STATE_PATH", str(state_path))

    # Pre-populate today's directional state to BUY (mimicking a prior MIDDAY lock).
    from datetime import datetime as _datetime
    from zoneinfo import ZoneInfo
    today_london = _datetime.now(ZoneInfo("Europe/London")).strftime("%Y-%m-%d")
    state_path.write_text(
        '{'
        f'"date_london": "{today_london}", '
        '"primary_direction": "BUY", '
        '"set_at_utc": "2026-05-08T10:30:08Z", '
        '"set_by_window": "midday", '
        '"confidence": 0.68, '
        '"macro_signature": {"dxy_sign": -1, "yields_sign": -1}'
        '}'
    )

    cfg = SignalConfig.from_env()
    asset = resolve_asset("XAUUSD")

    class _Usage:
        prompt_tokens = 50
        completion_tokens = 25
        total_tokens = 75

    class _Response:
        def __init__(self, content):
            self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})]
            self.usage = _Usage()

    parser_responses = iter([
        _Response('{"action":"SELL","confidence":0.48,"reasoning":"r","stop_loss_distance":12,"take_profit_distance":24}'),
        _Response('{"decision":"ALIGNED","confidence_adjustment":0.0,"reasoning":"ok","hard_blocker":false}'),
    ])

    class _Client:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": type("Completions", (), {"create": staticmethod(lambda **kwargs: next(parser_responses))})})()

    monkeypatch.setattr("xauex.signal.signal_parser.create_chat_client", lambda **kwargs: _Client())

    signal = parse_signal(
        asset=asset,
        actions=[{"agent_name": "price_structure", "action_type": "SELL", "content": "neg"}],
        report_markdown="# Report\nGold pressured.",
        config=cfg,
        prediction_payload={
            "price_features": {"price_bias": "SELL"},
            "memory_summary": {},
            "market_snapshot": {
                "series": {
                    # Macro signature unchanged — DXY and yields still down (gold-supportive)
                    "usd_broad_index": {"value": 118.0, "change_1d": -0.3, "bias": "BUY"},
                    "us10y_yield": {"value": 4.1, "change_1d": -0.05, "bias": "BUY"},
                },
            },
            "event_flags": {},
            "input_freshness": {"market_snapshot_state": "fresh", "market_snapshot_age_seconds": 600},
            "context_items": [],
        },
        window_label="us_open",
        decision_mode="baseline",
    )

    # The persistence layer must downgrade SELL 0.48 → HOLD because the BUY
    # lock is in place and the new confidence is below the flip threshold.
    assert signal["action"] == "HOLD"
    assert signal["confidence"] == 0.0
    assert signal["consensus_state"] == "blocked"
    persistence = signal["decision_packet"]["directional_persistence"]
    assert persistence["policy"] == "FLIP_BLOCKED_LOW_CONFIDENCE"


def test_parse_signal_does_not_hard_hold_when_snapshot_is_fresh_enough(monkeypatch):
    """The hard-stale guard must not interfere with normal fresh-snapshot
    operation. Use age below the threshold."""
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()
    asset = resolve_asset("XAUUSD")

    fresh_age = HARD_STALE_MARKET_SNAPSHOT_SECONDS // 2

    class _Usage:
        prompt_tokens = 50
        completion_tokens = 25
        total_tokens = 75

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

    parser_responses = [
        _Response('{"action":"SELL","confidence":0.6,"reasoning":"r","stop_loss_distance":12,"take_profit_distance":24}'),
        _Response('{"decision":"ALIGNED","confidence_adjustment":0.0,"reasoning":"ok","hard_blocker":false}'),
    ]

    class _Completions:
        def __init__(self, queue):
            self.queue = queue

        def create(self, **kwargs):
            return self.queue.pop(0)

    class _Client:
        def __init__(self, queue):
            self.chat = type("Chat", (), {"completions": _Completions(queue)})()

    queue = list(parser_responses)

    monkeypatch.setattr(
        "xauex.signal.signal_parser.create_chat_client",
        lambda **kwargs: _Client(queue),
    )

    signal = parse_signal(
        asset=asset,
        actions=[{"agent_name": "price_structure", "action_type": "SELL", "content": "neg"}],
        report_markdown="# Report\nGold pressured.",
        config=cfg,
        prediction_payload={
            "price_features": {"price_bias": "SELL"},
            "memory_summary": {},
            "market_snapshot": {"series": {}},
            "event_flags": {},
            "input_freshness": {
                "market_snapshot_state": "warning",
                "market_snapshot_age_seconds": fresh_age,
                "hard_blocker": False,
            },
            "context_items": [],
        },
        window_label="us_open",
        decision_mode="baseline",
    )

    assert signal["action"] in {"BUY", "SELL"}
    assert signal["consensus_state"] != "blocked"


def test_validator_prompt_reserves_disagreement_for_direct_contradictions():
    prompt = _validator_system_prompt(resolve_asset("XAUUSD"))

    assert "prefer ALIGNED" in prompt
    assert "Reserve DISAGREE for direct contradictions" in prompt
    assert "prefer DISAGREE over ALIGNED" not in prompt


def test_validator_prompt_softens_default_to_aligned_and_uses_explicit_streak_field():
    """The legacy validator returned DISAGREE on any 'mixed/uncertain/conflicting'
    reasoning, which is the default state of any non-trending market. New
    contract: default to ALIGNED unless ≥2 macro drivers point opposite, and
    use the explicit consecutive_loss_direction streak field instead of
    inferring loss correlation from sparse aggregate counts."""
    prompt = _validator_system_prompt(resolve_asset("XAUUSD"))
    # Hedge-word detection has been dropped — those words are normal market
    # uncertainty, not direct contradictions.
    assert "hedges with words like" not in prompt
    assert '"mixed"' not in prompt
    assert '"uncertain"' not in prompt
    # Adjustment range narrowed from [-0.30, +0.10] to [-0.15, +0.10]
    assert "-0.15 to 0.10" in prompt
    # Direction-aware memory must be referenced explicitly.
    assert "consecutive_loss_direction" in prompt
    # 2+ macro drivers requirement, not single-driver opposition.
    assert "2+ macro drivers" in prompt or "two or more macro drivers" in prompt


def test_validator_schema_clamps_confidence_adjustment_to_softer_range():
    """The validator response schema must reflect the softer adjustment
    range so the LLM cannot return values outside [-0.15, +0.10]."""
    from xauex.signal.signal_parser import _validator_response_schema

    schema = _validator_response_schema()
    confidence_adjustment_field = schema["schema"]["properties"]["confidence_adjustment"]
    assert confidence_adjustment_field["minimum"] == -0.15
    assert confidence_adjustment_field["maximum"] == 0.10


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


def test_parse_json_response_handles_empty_choices_as_unavailable(caplog):
    class _Response:
        choices = []

    parsed = _parse_json_response(_Response())

    assert parsed is None
    assert "empty choices" in caplog.text.lower()


def test_openrouter_opus_uses_json_object_mode_with_supported_parameters():
    class _Usage:
        prompt_tokens = 8700
        completion_tokens = 140
        total_tokens = 8840

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
            return _Response('{"action":"HOLD","confidence":0.0}')

    class _Client:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": _Completions()})()

    client = _Client()

    response, parsed, mode = _request_json_completion(
        client=client,
        model="anthropic/claude-opus-4.7",
        base_url="https://openrouter.ai/api/v1",
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

    assert response is not None
    assert parsed == {"action": "HOLD", "confidence": 0.0}
    assert mode == "json_object"
    assert client.chat.completions.calls[0]["response_format"]["type"] == "json_object"
    assert client.chat.completions.calls[0]["provider"] == {"require_parameters": True}
    assert client.chat.completions.calls[0]["max_tokens"] == 350
    assert "max_completion_tokens" not in client.chat.completions.calls[0]
    assert "temperature" not in client.chat.completions.calls[0]


def test_openrouter_gpt55_schema_request_omits_unsupported_temperature():
    class _Usage:
        prompt_tokens = 8700
        completion_tokens = 140
        total_tokens = 8840

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
            return _Response('{"decision":"APPROVE","confidence_adjustment":0.0,"reasoning":"ok","hard_blocker":false}')

    class _Client:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": _Completions()})()

    client = _Client()

    response, parsed, mode = _request_json_completion(
        client=client,
        model="openai/gpt-5.5",
        base_url="https://openrouter.ai/api/v1",
        messages=[{"role": "system", "content": "Return JSON."}],
        temperature=0.1,
        max_tokens=220,
        response_schema={
            "name": "signal_validator",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "decision": {"type": "string"},
                    "confidence_adjustment": {"type": "number"},
                    "reasoning": {"type": "string"},
                    "hard_blocker": {"type": "boolean"},
                },
                "required": ["decision", "confidence_adjustment", "reasoning", "hard_blocker"],
                "additionalProperties": False,
            },
        },
    )

    assert response is not None
    assert parsed["decision"] == "APPROVE"
    assert mode == "json_schema"
    assert client.chat.completions.calls[0]["provider"] == {"require_parameters": True}
    assert client.chat.completions.calls[0]["max_completion_tokens"] == 220
    assert client.chat.completions.calls[0]["reasoning"] == {"effort": "minimal", "exclude": True}
    assert "max_tokens" not in client.chat.completions.calls[0]
    assert "temperature" not in client.chat.completions.calls[0]


def test_new_openrouter_model_prices_are_estimated():
    from xauex.signal.signal_parser import _estimate_cost_usd

    assert _estimate_cost_usd("anthropic/claude-opus-4.7", 8744, 150) == 0.04747
    assert _estimate_cost_usd("openai/gpt-5.5", 8651, 63) == 0.045145
    assert _estimate_cost_usd("google/gemini-3.1-flash-lite", 3150, 200) == 0.0010875
    assert _estimate_cost_usd("google/gemini-3.1-flash-lite-20260507", 3150, 200) == 0.0010875


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


def _client_factory_for_parser_tests(
    *,
    parser_action="SELL",
    parser_confidence=0.55,
    validator_decision="ALIGNED",
):
    class _Usage:
        prompt_tokens = 120
        completion_tokens = 25
        total_tokens = 145

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
            system_prompt = str(kwargs.get("messages", [{}])[0].get("content", ""))
            if "risk-aware validator" in system_prompt:
                if validator_decision == "BLOCK":
                    return _Response(
                        '{"decision":"BLOCK","confidence_adjustment":-0.3,'
                        '"reasoning":"Freshness blocker remains active.","hard_blocker":true}'
                    )
                return _Response(
                    '{"decision":"ALIGNED","confidence_adjustment":0.0,'
                    '"reasoning":"Validator agrees with the proposed trade.","hard_blocker":false}'
                )
            return _Response(
                '{"action":"%s","confidence":%.2f,"reasoning":"Baseline parser remains directional.",'
                '"stop_loss_distance":12.0,"take_profit_distance":24.0}'
                % (parser_action, parser_confidence)
            )

    class _Client:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": _Completions()})()

    clients = []

    def _factory(**kwargs):
        client = _Client()
        clients.append(client)
        return client

    _factory.clients = clients
    return _factory


def test_parse_signal_blocks_low_confidence_price_bias_conflict(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    monkeypatch.setenv("XAUEX_SIGNAL_DIRECTIONAL_STATE_PATH", "")
    cfg = SignalConfig.from_env()
    asset = resolve_asset("XAUUSD")
    client_factory = _client_factory_for_parser_tests(parser_action="SELL", parser_confidence=0.62)

    monkeypatch.setattr("xauex.signal.signal_parser.create_chat_client", client_factory)

    signal = parse_signal(
        asset=asset,
        actions=[{"agent_name": "price_structure", "action_type": "SELL", "content": "macro pressure"}],
        report_markdown="# Report\nUSD and rates pressure gold.",
        config=cfg,
        prediction_payload={
            "price_features": {
                "price_bias": "BUY",
                "daily_trend_bias": -1,
                "range_position": "LOWER_THIRD",
                "regime_filter": "NONE",
            },
            "memory_summary": {},
            "market_snapshot": {"overall_bias": "SELL", "series": {}},
            "event_flags": {},
            "input_freshness": {"market_snapshot_state": "fresh", "hard_blocker": False},
            "context_items": [],
        },
        window_label="morning",
        decision_mode="baseline",
    )

    assert signal["action"] == "HOLD"
    assert signal["confidence"] == 0.0
    assert signal["stop_loss_distance"] == 0.0
    assert signal["take_profit_distance"] == 0.0
    assert signal["consensus_state"] == "blocked"
    assert signal["price_conflict_guard"]["policy"] == "PRICE_BIAS_CONFLICT_LOW_CONFIDENCE"
    assert signal["price_conflict_guard"]["original_action"] == "SELL"
    assert signal["price_conflict_guard"]["original_confidence"] == 0.62
    assert signal["price_conflict_guard"]["price_bias"] == "BUY"
    assert signal["price_conflict_guard"]["market_snapshot_overall_bias"] == "SELL"
    assert signal["decision_packet"]["price_features"]["price_bias"] == "BUY"
    assert signal["decision_packet"]["price_conflict_guard"]["policy"] == "PRICE_BIAS_CONFLICT_LOW_CONFIDENCE"


def test_parse_signal_gives_gpt55_validator_enough_output_budget(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    monkeypatch.setenv("XAUEX_SIGNAL_DIRECTIONAL_STATE_PATH", "")
    cfg = replace(
        SignalConfig.from_env(),
        validator_llm_base_url="https://openrouter.ai/api/v1",
        validator_llm_model="openai/gpt-5.5",
    )
    asset = resolve_asset("XAUUSD")
    client_factory = _client_factory_for_parser_tests(parser_action="SELL", parser_confidence=0.72)

    monkeypatch.setattr("xauex.signal.signal_parser.create_chat_client", client_factory)

    parse_signal(
        asset=asset,
        actions=[{"agent_name": "price_structure", "action_type": "SELL", "content": "macro pressure"}],
        report_markdown="# Report\nUSD and rates pressure gold.",
        config=cfg,
        prediction_payload={
            "price_features": {"price_bias": "SELL"},
            "memory_summary": {},
            "market_snapshot": {"overall_bias": "SELL", "series": {}},
            "event_flags": {},
            "input_freshness": {"market_snapshot_state": "fresh", "hard_blocker": False},
            "context_items": [],
        },
        window_label="morning",
        decision_mode="baseline",
    )

    validator_call = client_factory.clients[1].chat.completions.calls[0]
    assert validator_call["model"] == "openai/gpt-5.5"
    assert validator_call["max_completion_tokens"] == 900
    assert validator_call["reasoning"] == {"effort": "minimal", "exclude": True}


def test_parse_signal_allows_strong_price_bias_conflict(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    monkeypatch.setenv("XAUEX_SIGNAL_DIRECTIONAL_STATE_PATH", "")
    cfg = SignalConfig.from_env()
    asset = resolve_asset("XAUUSD")
    client_factory = _client_factory_for_parser_tests(parser_action="SELL", parser_confidence=0.68)

    monkeypatch.setattr("xauex.signal.signal_parser.create_chat_client", client_factory)

    signal = parse_signal(
        asset=asset,
        actions=[{"agent_name": "price_structure", "action_type": "SELL", "content": "macro pressure"}],
        report_markdown="# Report\nUSD and rates pressure gold.",
        config=cfg,
        prediction_payload={
            "price_features": {"price_bias": "BUY"},
            "memory_summary": {},
            "market_snapshot": {"overall_bias": "SELL", "series": {}},
            "event_flags": {},
            "input_freshness": {"market_snapshot_state": "fresh", "hard_blocker": False},
            "context_items": [],
        },
        window_label="morning",
        decision_mode="baseline",
    )

    assert signal["action"] == "SELL"
    assert signal["confidence"] == 0.68
    assert "price_conflict_guard" not in signal


def test_parse_signal_allows_aligned_price_bias(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    monkeypatch.setenv("XAUEX_SIGNAL_DIRECTIONAL_STATE_PATH", "")
    cfg = SignalConfig.from_env()
    asset = resolve_asset("XAUUSD")
    client_factory = _client_factory_for_parser_tests(parser_action="SELL", parser_confidence=0.62)

    monkeypatch.setattr("xauex.signal.signal_parser.create_chat_client", client_factory)

    signal = parse_signal(
        asset=asset,
        actions=[{"agent_name": "price_structure", "action_type": "SELL", "content": "price and macro pressure"}],
        report_markdown="# Report\nPrice and rates pressure gold.",
        config=cfg,
        prediction_payload={
            "price_features": {
                "price_bias": "SELL",
                "daily_trend_bias": -1,
                "range_position": "LOWER_THIRD",
                "regime_filter": "TREND_ALIGNED_LOWER_THIRD_KEPT_SELL",
            },
            "memory_summary": {},
            "market_snapshot": {"overall_bias": "SELL", "series": {}},
            "event_flags": {},
            "input_freshness": {"market_snapshot_state": "fresh", "hard_blocker": False},
            "context_items": [],
        },
        window_label="morning",
        decision_mode="baseline",
    )

    assert signal["action"] == "SELL"
    assert signal["confidence"] == 0.62
    assert signal["decision_packet"]["price_features"]["regime_filter"] == "TREND_ALIGNED_LOWER_THIRD_KEPT_SELL"
    assert "price_conflict_guard" not in signal


def _candidate_graph_result(*, degraded=False, action="BUY"):
    return {
        "mode": "tradingagents_candidate",
        "degraded": degraded,
        "stages": [
            "market_analyst",
            "bull_case",
            "bear_case",
            "trader_proposal",
            "risk_reviewer",
            "portfolio_decision",
        ],
        "summary": "Candidate graph completed." if not degraded else "Candidate graph degraded.",
        "scratchpad_path": "/tmp/candidate_scratchpad.jsonl",
        "final_decision": {
            "action": action,
            "confidence": 0.63,
            "reasoning": "Candidate portfolio decision favors the stronger side.",
            "stop_loss_distance": 12.0,
            "take_profit_distance": 24.0,
        },
    }


def test_parse_signal_tradingagents_candidate_enriches_packet_and_stores_graph(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()
    asset = resolve_asset("XAUUSD")
    client_factory = _client_factory_for_parser_tests()
    candidate_calls = []

    def fake_candidate(**kwargs):
        candidate_calls.append(kwargs)
        return _candidate_graph_result(action="BUY"), [
            {"stage": "portfolio_decision", "model": "candidate", "prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60, "estimated_cost_usd": 0.0}
        ]

    monkeypatch.setattr("xauex.signal.signal_parser.create_chat_client", client_factory)
    monkeypatch.setattr("xauex.signal.signal_parser.run_tradingagents_candidate", fake_candidate)

    signal = parse_signal(
        asset=asset,
        actions=[{"agent_name": "price_structure", "action_type": "BUY", "content": "gold broke higher"}],
        report_markdown="# Report\nGold bid as yields soften and the dollar fades.",
        config=cfg,
        prediction_payload={
            "price_features": {"price_bias": "BUY"},
            "memory_summary": {},
            "market_snapshot": {"series": {}},
            "event_flags": {},
            "input_freshness": {"market_snapshot_state": "fresh"},
            "context_items": [],
        },
        window_label="morning",
        decision_mode="tradingagents_candidate",
    )

    assert signal["action"] == "BUY"
    assert signal["decision_mode"] == "tradingagents_candidate"
    assert signal["candidate_graph"]["mode"] == "tradingagents_candidate"
    assert signal["candidate_graph"]["stages"][-1] == "portfolio_decision"
    assert signal["decision_packet"]["candidate_graph"]["summary"] == "Candidate graph completed."
    assert candidate_calls[0]["asset"] == asset


def test_parse_signal_degraded_candidate_falls_back_to_baseline_parser(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()
    asset = resolve_asset("XAUUSD")
    client_factory = _client_factory_for_parser_tests(parser_action="SELL")

    monkeypatch.setattr("xauex.signal.signal_parser.create_chat_client", client_factory)
    monkeypatch.setattr(
        "xauex.signal.signal_parser.run_tradingagents_candidate",
        lambda **kwargs: (_candidate_graph_result(degraded=True, action="BUY"), []),
    )

    signal = parse_signal(
        asset=asset,
        actions=[{"agent_name": "price_structure", "action_type": "SELL", "content": "negative momentum"}],
        report_markdown="# Report\nGold pressured by a firmer dollar and higher yields.",
        config=cfg,
        prediction_payload={
            "price_features": {"price_bias": "SELL"},
            "memory_summary": {},
            "market_snapshot": {"series": {}},
            "event_flags": {},
            "input_freshness": {"market_snapshot_state": "fresh"},
            "context_items": [],
        },
        window_label="morning",
        decision_mode="tradingagents_candidate",
    )

    assert signal["action"] == "SELL"
    assert signal["candidate_graph"]["degraded"] is True
    assert "candidate_graph" not in signal["decision_packet"]


def test_parse_signal_validator_can_block_candidate_output(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()
    asset = resolve_asset("XAUUSD")
    client_factory = _client_factory_for_parser_tests(validator_decision="BLOCK")

    monkeypatch.setattr("xauex.signal.signal_parser.create_chat_client", client_factory)
    monkeypatch.setattr(
        "xauex.signal.signal_parser.run_tradingagents_candidate",
        lambda **kwargs: (_candidate_graph_result(degraded=False, action="BUY"), []),
    )

    signal = parse_signal(
        asset=asset,
        actions=[{"agent_name": "price_structure", "action_type": "BUY", "content": "gold broke higher"}],
        report_markdown="# Report\nGold bid as yields soften.",
        config=cfg,
        prediction_payload={
            "price_features": {"price_bias": "BUY"},
            "memory_summary": {},
            "market_snapshot": {"series": {}},
            "event_flags": {},
            "input_freshness": {"market_snapshot_state": "fresh"},
            "context_items": [],
        },
        window_label="morning",
        decision_mode="tradingagents_candidate",
    )

    assert signal["action"] == "HOLD"
    assert signal["confidence"] == 0.0
    assert signal["consensus_state"] == "blocked"
    assert signal["candidate_graph"]["final_decision"]["action"] == "BUY"


def test_parse_signal_preserves_candidate_hold_without_directional_fallback(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()
    asset = resolve_asset("XAUUSD")
    client_factory = _client_factory_for_parser_tests()

    monkeypatch.setattr("xauex.signal.signal_parser.create_chat_client", client_factory)
    monkeypatch.setattr(
        "xauex.signal.signal_parser.run_tradingagents_candidate",
        lambda **kwargs: (
            _candidate_graph_result(degraded=False, action="HOLD")
            | {
                "final_decision": {
                    "action": "HOLD",
                    "confidence": 0.0,
                    "reasoning": "Candidate portfolio manager stays flat for this window.",
                    "stop_loss_distance": 0.0,
                    "take_profit_distance": 0.0,
                }
            },
            [],
        ),
    )

    signal = parse_signal(
        asset=asset,
        actions=[{"agent_name": "price_structure", "action_type": "BUY", "content": "gold broke higher"}],
        report_markdown="# Report\nGold bid as yields soften and buyers press upside.",
        config=cfg,
        prediction_payload={
            "price_features": {"price_bias": "BUY"},
            "memory_summary": {},
            "market_snapshot": {"series": {}},
            "event_flags": {},
            "input_freshness": {"market_snapshot_state": "fresh"},
            "context_items": [],
        },
        window_label="morning",
        decision_mode="tradingagents_candidate",
    )

    assert signal["action"] == "HOLD"
    assert signal["confidence"] == 0.0
    assert signal["candidate_graph"]["final_decision"]["action"] == "HOLD"
