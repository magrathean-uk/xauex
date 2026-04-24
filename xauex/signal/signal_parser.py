"""Parse XAUEX signal-model output into a structured asset-aware trading signal."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from math import exp
from typing import Any

from xauex.signal.assets import AssetProfile
from xauex.signal.config import SignalConfig
from xauex.live_windows import get_live_window
from xauex.shared.llm_client import create_chat_client

logger = logging.getLogger(__name__)

_TOKEN_PRICES_USD_PER_MILLION: dict[str, tuple[float, float]] = {
    'llama-3.1-8b-instant': (0.05, 0.08),
    'llama-3.3-70b-versatile': (0.59, 0.79),
    'openai/gpt-oss-20b': (0.075, 0.30),
    'openai/gpt-oss-120b': (0.15, 0.60),
}


def parse_signal(
    *,
    asset: AssetProfile,
    actions: list,
    report_markdown: str,
    config: SignalConfig,
    prediction_payload: dict[str, Any] | None = None,
    window_label: str = 'current',
    decision_mode: str | None = None,
) -> dict:
    decision_mode = decision_mode or getattr(config, 'decision_mode', 'baseline') or 'baseline'
    decision_packet = _build_decision_packet(
        asset=asset,
        prediction_payload=prediction_payload or {},
        actions=actions,
        report_markdown=report_markdown,
        window_label=window_label,
    )
    parser_client = create_chat_client(
        api_key=config.parser_llm_api_key,
        base_url=config.parser_llm_base_url,
    )

    logger.info(
        '[PARSER] Calling %s for %s (%d chars report, %d actions)',
        config.parser_llm_model,
        asset.symbol,
        len(report_markdown),
        len(actions),
    )

    if bool((decision_packet.get('input_freshness') or {}).get('hard_blocker')):
        reason = str((decision_packet.get('input_freshness') or {}).get('summary') or 'Structured inputs are not tradeable right now.')
        signal = _hold_signal(asset, reason)
        signal['decision_mode'] = decision_mode
        signal['validator_status'] = 'skipped'
        signal['validator_summary'] = reason
        signal['consensus_state'] = 'blocked'
        signal['llm_usage'] = _combine_usage(provider=config.parser_llm_base_url, stages=[])
        signal['decision_packet'] = {
            'decision_mode': decision_mode,
            'window_label': decision_packet['window_label'],
            'input_freshness': decision_packet['input_freshness'],
            'market_snapshot': decision_packet['market_snapshot'],
            'event_flags': decision_packet['event_flags'],
        }
        return signal

    debate_usage: list[dict[str, Any]] = []
    debate: dict[str, Any] | None = None
    if decision_mode == 'analyst_debate':
        debate, debate_usage = _build_analyst_debate(
            asset=asset,
            decision_packet=decision_packet,
            config=config,
        )
        decision_packet = _enrich_decision_packet_with_debate(decision_packet, debate)

    response, parsed, parser_mode = _request_json_completion(
        client=parser_client,
        model=config.parser_llm_model,
        messages=[
            {'role': 'system', 'content': _system_prompt(asset)},
            {'role': 'user', 'content': _parser_user_prompt(asset, decision_packet)},
        ],
        temperature=0.1,
        max_tokens=350,
        response_schema=_signal_response_schema(asset),
    )
    if parsed is None or response is None:
        logger.error('[PARSER] Invalid JSON from model for %s', asset.symbol)
        return _hold_signal(asset, 'LLM returned invalid JSON')

    fallback = _fallback_direction(actions, report_markdown, parsed.get('reasoning'))
    # Hard-blocker freshness is handled above (line 58) and returns HOLD before
    # we reach this point. A 'warning' or 'stale' state is milder but still
    # means our macro context is questionable - so refuse to rescue HOLD with
    # keyword bias in that case. The parser's HOLD stays HOLD.
    freshness_state = str(
        (decision_packet.get('input_freshness') or {}).get('market_snapshot_state', '')
    ).lower()
    if freshness_state in ('warning', 'stale', 'blocked'):
        fallback = {
            **fallback,
            'action': 'HOLD',
            'confidence': 0.0,
            'summary': (
                f"{fallback.get('summary', '')} Fallback suppressed: "
                f"market snapshot state={freshness_state}."
            ).strip(),
        }
    signal = _normalize_signal(asset, parsed, fallback=fallback)
    signal['decision_mode'] = decision_mode
    if debate is not None:
        signal['debate'] = debate
    parser_usage = _extract_usage(
        response,
        provider=config.parser_llm_base_url,
        model=config.parser_llm_model,
        stage='parser',
    )
    parser_usage['response_mode'] = parser_mode

    validator_usage: dict[str, Any] | None = None
    validator_result: dict[str, Any] | None = None
    try:
        validator_client = create_chat_client(
            api_key=config.validator_llm_api_key,
            base_url=config.validator_llm_base_url,
        )
        validator_response, validator_result, validator_mode = _request_json_completion(
            client=validator_client,
            model=config.validator_llm_model,
            messages=[
                {'role': 'system', 'content': _validator_system_prompt(asset)},
                {'role': 'user', 'content': _validator_user_prompt(asset, decision_packet, signal)},
            ],
            temperature=0.1,
            max_tokens=220,
            response_schema=_validator_response_schema(),
        )
        if validator_response is not None:
            validator_usage = _extract_usage(
                validator_response,
                provider=config.validator_llm_base_url,
                model=config.validator_llm_model,
                stage='validator',
            )
            validator_usage['response_mode'] = validator_mode
    except Exception as exc:  # pragma: no cover - network/provider dependent
        logger.warning('[VALIDATOR] Validator unavailable for %s: %s', asset.symbol, exc)

    if validator_result is not None:
        signal = _apply_validator_result(asset=asset, signal=signal, validator_result=validator_result)
    else:
        signal['validator_status'] = 'unavailable'
        signal['validator_summary'] = 'Validator unavailable.'
        signal['consensus_state'] = 'unreviewed'

    signal['llm_usage'] = _combine_usage(
        provider=config.parser_llm_base_url,
        stages=[stage for stage in [*debate_usage, parser_usage, validator_usage] if stage],
    )
    signal['decision_packet'] = {
        'decision_mode': decision_mode,
        'window_label': decision_packet['window_label'],
        'input_freshness': decision_packet['input_freshness'],
        'market_snapshot': decision_packet['market_snapshot'],
        'event_flags': decision_packet['event_flags'],
    }
    if debate is not None:
        signal['decision_packet']['debate'] = debate
    return signal


def _normalize_signal(asset: AssetProfile, parsed: dict, *, fallback: dict[str, Any]) -> dict:
    action = str(parsed.get('action', 'HOLD')).upper()
    if action not in {'BUY', 'SELL', 'HOLD'}:
        action = fallback['action']

    try:
        confidence = float(parsed.get('confidence', 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    reasoning = str(parsed.get('reasoning', 'No reasoning provided'))[:500]

    if action == 'HOLD' and not _reasoning_hard_blocker(reasoning):
        action = fallback['action']
        confidence = max(confidence, fallback['confidence'])
        reasoning = (
            f'{reasoning} Directional fallback: {fallback["summary"]}'
            if reasoning
            else fallback['summary']
        )[:500]

    if action in {'BUY', 'SELL'} and confidence <= 0.0:
        confidence = fallback['confidence']

    sl = _safe_float(parsed.get('stop_loss_distance'), asset.default_stop_loss_distance)
    sl = max(asset.min_stop_loss_distance, min(asset.max_stop_loss_distance, sl))

    tp = _safe_float(parsed.get('take_profit_distance'), sl * 2.0)
    rr_min = sl * asset.min_take_profit_rr
    rr_max = sl * asset.max_take_profit_rr
    tp = max(rr_min, min(rr_max, tp))

    if action == 'HOLD':
        sl_value = 0.0
        tp_value = 0.0
    else:
        sl_value = round(sl, 2 if asset.distance_unit == 'usd' else 1)
        tp_value = round(tp, 2 if asset.distance_unit == 'usd' else 1)

    signal = {
        'schema_version': 2,
        'symbol': asset.symbol,
        'asset_class': asset.asset_class,
        'action': action,
        'confidence': round(confidence, 2),
        'reasoning': reasoning,
        'stop_loss_distance': sl_value,
        'take_profit_distance': tp_value,
        'distance_unit': asset.distance_unit,
        'timestamp_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'execution_supported': asset.execution_supported,
    }

    if asset.distance_unit == 'usd':
        signal['stop_loss_usd'] = sl_value
        signal['take_profit_usd'] = tp_value

    return signal


def _hold_signal(asset: AssetProfile, reason: str) -> dict:
    signal = {
        'schema_version': 2,
        'symbol': asset.symbol,
        'asset_class': asset.asset_class,
        'action': 'HOLD',
        'confidence': 0.0,
        'reasoning': reason,
        'stop_loss_distance': 0.0,
        'take_profit_distance': 0.0,
        'distance_unit': asset.distance_unit,
        'timestamp_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'execution_supported': asset.execution_supported,
    }
    if asset.distance_unit == 'usd':
        signal['stop_loss_usd'] = 0.0
        signal['take_profit_usd'] = 0.0
    return signal


def _extract_usage(response: Any, *, provider: str, model: str, stage: str) -> dict:
    usage = getattr(response, 'usage', None)
    if usage is None:
        return {
            'provider': provider,
            'model': model,
            'stage': stage,
            'usage_available': False,
        }

    prompt_tokens = int(getattr(usage, 'prompt_tokens', 0) or 0)
    completion_tokens = int(getattr(usage, 'completion_tokens', 0) or 0)
    total_tokens = int(getattr(usage, 'total_tokens', prompt_tokens + completion_tokens) or 0)
    estimated_cost_usd = _estimate_cost_usd(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    if estimated_cost_usd is not None:
        logger.info(
            '[PARSER] Usage model=%s prompt=%d completion=%d total=%d est_cost_usd=%.6f',
            model,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            estimated_cost_usd,
        )
    else:
        logger.info(
            '[PARSER] Usage model=%s prompt=%d completion=%d total=%d',
            model,
            prompt_tokens,
            completion_tokens,
            total_tokens,
        )
    return {
        'provider': provider,
        'model': model,
        'stage': stage,
        'usage_available': True,
        'prompt_tokens': prompt_tokens,
        'completion_tokens': completion_tokens,
        'total_tokens': total_tokens,
        'estimated_cost_usd': estimated_cost_usd,
    }


def _estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    prices = _TOKEN_PRICES_USD_PER_MILLION.get(model)
    if prices is None:
        return None
    input_price, output_price = prices
    return round((prompt_tokens / 1_000_000 * input_price) + (completion_tokens / 1_000_000 * output_price), 8)


def _model_completion_options(
    model: str,
    *,
    max_tokens: int,
    response_schema: dict[str, Any] | None = None,
    json_object: bool = False,
) -> dict[str, Any]:
    options: dict[str, Any] = {
        'max_completion_tokens': max_tokens,
    }
    if model.startswith('openai/gpt-oss-'):
        options['reasoning_effort'] = 'low'
        options['max_completion_tokens'] = max(max_tokens, 700)
        options['extra_body'] = {'include_reasoning': False}
    if response_schema is not None:
        options['response_format'] = {
            'type': 'json_schema',
            'json_schema': response_schema,
        }
    elif json_object:
        options['response_format'] = {'type': 'json_object'}
    return options


def _supports_strict_json_schema(model: str) -> bool:
    return model in {'openai/gpt-oss-20b', 'openai/gpt-oss-120b'}


def _request_json_completion(
    *,
    client: Any,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    response_schema: dict[str, Any] | None = None,
) -> tuple[Any | None, dict[str, Any] | None, str]:
    attempts: list[tuple[str, dict[str, Any]]] = []
    if response_schema is not None and _supports_strict_json_schema(model):
        attempts.append(
            (
                'json_schema',
                _model_completion_options(
                    model,
                    max_tokens=max_tokens,
                    response_schema=response_schema,
                ),
            )
        )
    if response_schema is not None:
        attempts.append(
            (
                'json_object',
                _model_completion_options(
                    model,
                    max_tokens=max_tokens,
                    json_object=True,
                ),
            )
        )
    else:
        attempts.append(
            (
                'default',
                _model_completion_options(
                    model,
                    max_tokens=max_tokens,
                ),
            )
        )

    last_response: Any | None = None
    last_mode = attempts[-1][0]
    for mode, options in attempts:
        last_mode = mode
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                **options,
            )
        except Exception as exc:
            logger.warning('[PARSER] %s request failed for %s: %s', mode, model, exc)
            continue
        last_response = response
        parsed = _parse_json_response(response)
        if parsed is not None:
            return response, parsed, mode
        logger.warning('[PARSER] %s response for %s did not parse as JSON, retrying.', mode, model)
    return last_response, None, last_mode


def _system_prompt(asset: AssetProfile) -> str:
    return f"""You are a window-aware intraday trading signal analyst for XAUEX.

{asset.parser_brief}

Respond ONLY with valid JSON and no markdown fencing.
Use this schema:
{{
  "action": "BUY" | "SELL" | "HOLD",
  "confidence": 0.0 to 1.0,
  "reasoning": "one concise sentence",
  "stop_loss_distance": {asset.min_stop_loss_distance} to {asset.max_stop_loss_distance},
  "take_profit_distance": {round(asset.min_stop_loss_distance * asset.min_take_profit_rr, 2)} to {round(asset.max_stop_loss_distance * asset.max_take_profit_rr, 2)}
}}

Rules:
- Choose BUY or SELL by default.
- Use HOLD only for a hard execution blocker such as stale context, missing tradeable market information, market closure, or an explicit non-tradable condition.
- If evidence is mixed, still choose the dominant side and lower confidence rather than defaulting to HOLD.
- Distances must be expressed in {asset.distance_unit}.
- stop_loss_distance must stay inside [{asset.min_stop_loss_distance}, {asset.max_stop_loss_distance}].
- take_profit_distance should be between {asset.min_take_profit_rr}x and {asset.max_take_profit_rr}x the stop loss.
- Decide only for the current decision window. Do not assume this is the only trade window of the day.
- Focus on the expected net move for the current decision window, not a multi-day swing.
- Make the answer decisive and tradeable."""


def _validator_system_prompt(asset: AssetProfile) -> str:
    return f"""You are a risk-aware validator for an XAUEX {asset.symbol} trading signal.

Respond ONLY with valid JSON and no markdown fencing.
Use this schema:
{{
  "decision": "ALIGNED" | "DISAGREE" | "BLOCK",
  "confidence_adjustment": -0.30 to 0.10,
  "reasoning": "one concise sentence",
  "hard_blocker": true | false
}}

Rules:
- BLOCK only for hard blockers such as stale inputs during a live window, missing core tradeable data, or invalid trade geometry.
- If the thesis is mixed but still directional, return DISAGREE and reduce confidence instead of blocking.
- Use ALIGNED when the proposed trade is well supported by structured drivers and the narrative context.
- Keep the response concise and operational."""


def _parser_user_prompt(asset: AssetProfile, packet: dict[str, Any]) -> str:
    return (
        f'Asset: {asset.symbol}\n'
        f'Asset class: {asset.asset_class}\n'
        f'Current decision window: {packet["window_label"]}\n\n'
        f'Session profile:\n{json.dumps(packet.get("session_profile") or {}, indent=2, sort_keys=True)}\n\n'
        'Decision packet:\n'
        f'{json.dumps(packet, indent=2, sort_keys=True)}\n\n'
        f'Based on the current decision window, what is the best directional signal for {asset.symbol}?'
    )


def _validator_user_prompt(asset: AssetProfile, packet: dict[str, Any], signal: dict[str, Any]) -> str:
    return (
        f'Asset: {asset.symbol}\n'
        f'Proposed signal:\n{json.dumps(signal, indent=2, sort_keys=True)}\n\n'
        'Decision packet:\n'
        f'{json.dumps(packet, indent=2, sort_keys=True)}\n\n'
        'Review the proposed signal. Preserve directional bias unless there is a hard blocker.'
    )


def _signal_response_schema(asset: AssetProfile) -> dict[str, Any]:
    return {
        'name': 'xauex_signal_decision',
        'strict': True,
        'schema': {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'enum': ['BUY', 'SELL', 'HOLD'],
                },
                'confidence': {
                    'type': 'number',
                    'minimum': 0.0,
                    'maximum': 1.0,
                },
                'reasoning': {
                    'type': 'string',
                    'minLength': 1,
                    'maxLength': 500,
                },
                'stop_loss_distance': {
                    'type': 'number',
                    'minimum': asset.min_stop_loss_distance,
                    'maximum': asset.max_stop_loss_distance,
                },
                'take_profit_distance': {
                    'type': 'number',
                    'minimum': round(asset.min_stop_loss_distance * asset.min_take_profit_rr, 2),
                    'maximum': round(asset.max_stop_loss_distance * asset.max_take_profit_rr, 2),
                },
            },
            'required': [
                'action',
                'confidence',
                'reasoning',
                'stop_loss_distance',
                'take_profit_distance',
            ],
            'additionalProperties': False,
        },
    }


def _validator_response_schema() -> dict[str, Any]:
    return {
        'name': 'xauex_signal_validator',
        'strict': True,
        'schema': {
            'type': 'object',
            'properties': {
                'decision': {
                    'type': 'string',
                    'enum': ['ALIGNED', 'DISAGREE', 'BLOCK'],
                },
                'confidence_adjustment': {
                    'type': 'number',
                    'minimum': -0.30,
                    'maximum': 0.10,
                },
                'reasoning': {
                    'type': 'string',
                    'minLength': 1,
                    'maxLength': 300,
                },
                'hard_blocker': {
                    'type': 'boolean',
                },
            },
            'required': [
                'decision',
                'confidence_adjustment',
                'reasoning',
                'hard_blocker',
            ],
            'additionalProperties': False,
        },
    }


def _analyst_case_response_schema() -> dict[str, Any]:
    return {
        'name': 'xauex_analyst_case',
        'strict': True,
        'schema': {
            'type': 'object',
            'properties': {
                'stance': {
                    'type': 'string',
                    'enum': ['BULL', 'BEAR'],
                },
                'summary': {
                    'type': 'string',
                    'minLength': 1,
                    'maxLength': 280,
                },
                'key_points': {
                    'type': 'array',
                    'items': {'type': 'string', 'minLength': 1, 'maxLength': 180},
                    'minItems': 1,
                    'maxItems': 3,
                },
                'risk_flags': {
                    'type': 'array',
                    'items': {'type': 'string', 'minLength': 1, 'maxLength': 180},
                    'maxItems': 4,
                },
            },
            'required': ['stance', 'summary', 'key_points', 'risk_flags'],
            'additionalProperties': False,
        },
    }


def _build_analyst_debate(
    *,
    asset: AssetProfile,
    decision_packet: dict[str, Any],
    config: SignalConfig,
    client_factory: Any | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if client_factory is None:
        factory = create_chat_client
    else:
        factory = client_factory
    client = factory(
        api_key=config.parser_llm_api_key,
        base_url=config.parser_llm_base_url,
    )
    debate = {
        'mode': 'analyst_debate',
        'degraded': False,
        'summary': 'Bull and bear analyst cases were generated from the same XAUEX packet.',
        'bull_case': {'status': 'pending'},
        'bear_case': {'status': 'pending'},
    }
    usage_stages: list[dict[str, Any]] = []
    failures = 0
    for stance, stage_name in (('BULL', 'bull_case'), ('BEAR', 'bear_case')):
        response, parsed, response_mode = _request_json_completion(
            client=client,
            model=config.debate_analyst_model,
            messages=[
                {'role': 'system', 'content': _analyst_case_system_prompt(asset=asset, stance=stance)},
                {'role': 'user', 'content': _analyst_case_user_prompt(asset=asset, decision_packet=decision_packet, stance=stance)},
            ],
            temperature=0.1,
            max_tokens=220,
            response_schema=_analyst_case_response_schema(),
        )
        if parsed is None or response is None:
            debate[stage_name] = {
                'status': 'unavailable',
                'stance': stance,
            }
            failures += 1
            continue
        case = {
            'status': 'available',
            'stance': stance,
            'summary': str(parsed.get('summary') or '').strip()[:280],
            'key_points': [str(item).strip()[:180] for item in parsed.get('key_points') or [] if str(item).strip()][:3],
            'risk_flags': [str(item).strip()[:180] for item in parsed.get('risk_flags') or [] if str(item).strip()][:4],
        }
        debate[stage_name] = case
        stage_usage = _extract_usage(
            response,
            provider=config.parser_llm_base_url,
            model=config.debate_analyst_model,
            stage=stage_name,
        )
        stage_usage['response_mode'] = response_mode
        usage_stages.append(stage_usage)
    if failures:
        debate['degraded'] = True
        debate['summary'] = 'Analyst debate degraded; baseline decision packet remains authoritative.'
    return debate, usage_stages


def _enrich_decision_packet_with_debate(
    packet: dict[str, Any],
    debate: dict[str, Any] | None,
) -> dict[str, Any]:
    if not debate or bool(debate.get('degraded')):
        return dict(packet)
    enriched = dict(packet)
    enriched['debate'] = debate
    return enriched


def _analyst_case_system_prompt(*, asset: AssetProfile, stance: str) -> str:
    stance_line = 'bullish' if stance == 'BULL' else 'bearish'
    return f"""You are the {stance_line} analyst for an XAUEX {asset.symbol} decision window.

Respond ONLY with valid JSON and no markdown fencing.
Use this schema:
{{
  "stance": "{stance}",
  "summary": "one concise thesis sentence",
  "key_points": ["point 1", "point 2"],
  "risk_flags": ["risk 1"]
}}

Rules:
- Argue only the {stance_line} case from the provided decision packet.
- Be concise and operational.
- Do not mention being an AI or provide balanced conclusions.
- Keep key_points to at most 3 and risk_flags to at most 4."""


def _analyst_case_user_prompt(*, asset: AssetProfile, decision_packet: dict[str, Any], stance: str) -> str:
    return (
        f'Asset: {asset.symbol}\n'
        f'Required stance: {stance}\n\n'
        'Decision packet:\n'
        f'{json.dumps(decision_packet, indent=2, sort_keys=True)}\n\n'
        f'Build the strongest {stance} thesis from this packet only.'
    )


def _build_feature_snapshot(actions: list, report_markdown: str) -> str:
    fallback = _fallback_direction(actions, report_markdown, None)
    action_count = len(actions)
    last_agent = '?'
    last_action = '?'
    if actions:
        item = actions[-1]
        last_agent = str(item.get('agent_name', item.get('agent_id', '?')))
        last_action = str(item.get('action_type', item.get('type', '?')))
    return (
        f'- recent_actions_count: {action_count}\n'
        f'- directional_bias_score: {fallback["score"]}\n'
        f'- directional_bias: {fallback["action"]}\n'
        f'- directional_confidence_floor: {fallback["confidence"]:.2f}\n'
        f'- bullish_hits: {fallback["bullish_hits"]}\n'
        f'- bearish_hits: {fallback["bearish_hits"]}\n'
        f'- conflict_ratio: {fallback["conflict_ratio"]:.2f}\n'
        f'- latest_agent: {last_agent}\n'
        f'- latest_action_type: {last_action}'
    )


def _build_decision_packet(
    *,
    asset: AssetProfile,
    prediction_payload: dict[str, Any],
    actions: list,
    report_markdown: str,
    window_label: str,
    debate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    window = get_live_window(window_label=window_label)
    fallback = _fallback_direction(actions, report_markdown, None)
    top_context_items = []
    for item in prediction_payload.get('context_items', [])[:6]:
        top_context_items.append({
            'source_name': str(item.get('source_name', '') or ''),
            'tier': int(item.get('tier', 0) or 0),
            'freshness_score': float(item.get('freshness_score', 0.0) or 0.0),
            'title': str(item.get('title', '') or '')[:180],
            'summary': str(item.get('summary', '') or '')[:280],
        })
    packet = {
        'asset': asset.symbol,
        'asset_class': asset.asset_class,
        'window_label': window_label,
        'session_profile': (
            window.session_profile()
            if window is not None
            else {
                'slot': 'CURRENT',
                'window_label': window_label,
                'timezone': 'Europe/London',
                'description': 'General XAUEX context outside the named live windows.',
                'holding_horizon': 'Immediate decision support only.',
                'dominant_drivers': ['price action', 'macro context'],
            }
        ),
        'fallback_bias': fallback,
        'price_features': prediction_payload.get('price_features', {}),
        'memory_summary': prediction_payload.get('memory_summary', {}),
        'market_snapshot': prediction_payload.get('market_snapshot', {}),
        'event_flags': prediction_payload.get('event_flags', {}),
        'input_freshness': prediction_payload.get('input_freshness', {}),
        'top_context_items': top_context_items,
        'recent_actions': _summarize_actions(actions)[:2200],
        'report_excerpt': report_markdown[:6000],
    }
    return _enrich_decision_packet_with_debate(packet, debate)


def _apply_validator_result(
    *,
    asset: AssetProfile,
    signal: dict[str, Any],
    validator_result: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(signal)
    decision = str(validator_result.get('decision', 'ALIGNED') or 'ALIGNED').upper()
    reasoning = str(validator_result.get('reasoning', '') or '').strip()[:300]
    adjustment = _safe_float(validator_result.get('confidence_adjustment'), 0.0)
    hard_blocker = bool(validator_result.get('hard_blocker'))

    if hard_blocker or decision == 'BLOCK':
        blocked = _hold_signal(asset, reasoning or 'Validator blocked the trade.')
        blocked['validator_status'] = 'reviewed'
        blocked['validator_summary'] = reasoning or 'Validator blocked the trade.'
        blocked['consensus_state'] = 'blocked'
        return blocked

    if decision == 'DISAGREE':
        merged['confidence'] = round(max(0.0, min(1.0, float(merged.get('confidence', 0.0)) + adjustment)), 2)
        merged['consensus_state'] = 'disagreed'
    else:
        if adjustment:
            merged['confidence'] = round(max(0.0, min(1.0, float(merged.get('confidence', 0.0)) + adjustment)), 2)
        merged['consensus_state'] = 'aligned'
    merged['validator_status'] = 'reviewed'
    merged['validator_summary'] = reasoning or ('Validator aligned with the proposed signal.' if decision == 'ALIGNED' else 'Validator reviewed the proposed signal.')
    return merged


def _combine_usage(*, provider: str, stages: list[dict[str, Any]]) -> dict[str, Any]:
    prompt_tokens = sum(int(stage.get('prompt_tokens', 0) or 0) for stage in stages)
    completion_tokens = sum(int(stage.get('completion_tokens', 0) or 0) for stage in stages)
    total_tokens = sum(int(stage.get('total_tokens', 0) or 0) for stage in stages)
    estimated_total_cost_usd = round(
        sum(float(stage.get('estimated_cost_usd', 0.0) or 0.0) for stage in stages),
        8,
    )
    return {
        'provider': provider,
        'usage_available': bool(stages),
        'model': '+'.join(stage.get('model', '') for stage in stages if stage.get('model')),
        'prompt_tokens': prompt_tokens,
        'completion_tokens': completion_tokens,
        'total_tokens': total_tokens,
        'estimated_cost_usd': estimated_total_cost_usd,
        'estimated_total_cost_usd': estimated_total_cost_usd,
        'stages': {stage.get('stage', f'stage_{idx}'): stage for idx, stage in enumerate(stages)},
    }


def _parse_json_response(response: Any) -> dict[str, Any] | None:
    raw = (response.choices[0].message.content or '').strip()
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.error('[PARSER] Invalid JSON from model: %s', raw[:300])
        return None


def _reasoning_hard_blocker(reasoning: str) -> bool:
    text = reasoning.lower()
    blocker_terms = (
        'market closed',
        'stale',
        'no data',
        'missing data',
        'insufficient data',
        'not enough data',
        'non-tradable',
        'do not trade',
        'kill switch',
        'execution blocker',
    )
    return any(term in text for term in blocker_terms)


def _fallback_direction(actions: list, report_markdown: str, reasoning: Any) -> dict[str, Any]:
    combined = '\n'.join(
        part for part in [
            report_markdown[-6000:],
            _summarize_actions(actions),
            str(reasoning or ''),
        ]
        if part
    ).lower()
    normalized = re.sub(r'[^a-z0-9\s]+', ' ', combined)
    bullish_patterns = (
        'bullish',
        'buy',
        'upside',
        'higher',
        'weaker dollar',
        'lower yields',
        'dovish',
        'safe haven demand',
        'inflow',
        'support',
        'bid',
        'rebound',
        'breakout higher',
        'long',
    )
    bearish_patterns = (
        'bearish',
        'sell',
        'downside',
        'lower',
        'stronger dollar',
        'higher yields',
        'hawkish',
        'outflow',
        'resistance',
        'pressure',
        'capped',
        'selloff',
        'breakdown',
        'short',
    )
    bullish_hits = sum(normalized.count(term) for term in bullish_patterns)
    bearish_hits = sum(normalized.count(term) for term in bearish_patterns)
    score = bullish_hits - bearish_hits
    total_hits = bullish_hits + bearish_hits
    conflict_ratio = (min(bullish_hits, bearish_hits) / total_hits) if total_hits else 0.5
    margin = abs(score) / max(total_hits, 1)

    # Require real evidence before converting HOLD into a direction.
    # Pure keyword counts on news text are noisy; low total hits or a thin net
    # margin is not enough conviction to take risk. Ties return HOLD so the
    # downstream parser does not inherit a permanent long bias.
    min_total_hits = 4
    min_margin = 0.15
    if score == 0 or total_hits < min_total_hits or margin < min_margin:
        action = 'HOLD'
        confidence = 0.0
    else:
        action = 'BUY' if score > 0 else 'SELL'
        confidence = 0.51 + (0.17 / (1.0 + exp(-6.0 * (margin - 0.25))))
        confidence = max(0.51, min(0.68, confidence))

    summary = (
        f'Fallback bias {action} from action/report score {score} '
        f'(bullish_hits={bullish_hits}, bearish_hits={bearish_hits}, '
        f'conflict_ratio={conflict_ratio:.2f}, margin={margin:.2f}).'
    )
    return {
        'action': action,
        'confidence': round(confidence, 2),
        'score': score,
        'bullish_hits': bullish_hits,
        'bearish_hits': bearish_hits,
        'conflict_ratio': conflict_ratio,
        'summary': summary,
    }


def _summarize_actions(actions: list) -> str:
    if not actions:
        return 'No agent actions recorded.'
    lines = []
    for item in actions[-60:]:
        agent = item.get('agent_name', item.get('agent_id', '?'))
        action_type = item.get('action_type', item.get('type', '?'))
        content = item.get('content', item.get('text', ''))
        if isinstance(content, str) and len(content) > 220:
            content = content[:220] + '...'
        lines.append(f'- [{agent}] {action_type}: {content}')
    return '\n'.join(lines)


def _safe_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)
