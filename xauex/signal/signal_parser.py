"""Parse XAUEX signal-model output into a structured asset-aware trading signal."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from math import exp
from pathlib import Path
from typing import Any

from xauex.signal.assets import AssetProfile
from xauex.signal.candidate_graph import run_tradingagents_candidate
from xauex.signal.config import SignalConfig
from xauex.signal.llm_models import (
    completion_options,
    estimate_cost_usd,
    request_temperature_kwargs,
    supports_strict_json_schema,
)
from xauex.live_windows import get_live_window
from xauex.shared.llm_client import create_chat_client

logger = logging.getLogger(__name__)

# Refuse to trade when the structured macro snapshot is older than this. The
# market_snapshot freshness assessor only blocks when ≥3 individual series
# exceed 7 days; in practice that meant a 7.5-day-old DXY plus 2.5-day-old
# yields slipped through as "warning" and the parser produced live SELL
# signals against gold during a strong uptrend (the May 2026 incident).
HARD_STALE_MARKET_SNAPSHOT_SECONDS = 3 * 24 * 3600
HARD_STALE_MARKET_SNAPSHOT_BUSINESS_DAYS = 2
HARD_STALE_MACRO_BLOCK_REASON = 'HARD_STALE_MACRO_SNAPSHOT'
INPUT_FRESHNESS_BLOCK_REASON = 'INPUT_FRESHNESS_HARD_BLOCKER'
PRICE_CONFLICT_MIN_CONFIDENCE = 0.68


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

    logger.info(
        '[PARSER] Preparing %s decision for %s (%d chars report, %d actions)',
        decision_mode,
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
        signal['block_reason'] = INPUT_FRESHNESS_BLOCK_REASON
        signal['llm_usage'] = _combine_usage(provider=config.parser_llm_base_url, stages=[])
        signal['decision_packet'] = {
            'decision_mode': decision_mode,
            'window_label': decision_packet['window_label'],
            'block_reason': INPUT_FRESHNESS_BLOCK_REASON,
            'input_freshness': decision_packet['input_freshness'],
            'market_snapshot': decision_packet['market_snapshot'],
            'event_flags': decision_packet['event_flags'],
        }
        return signal

    # Hard-stale guard: the upstream `market_snapshot.py` freshness assessor
    # only flips to 'blocked' when *multiple* series exceed 7 days. That's too
    # permissive — a 5-day-old daily series plus 2-day-old yields slips
    # through as 'warning' and the LLM treats stale values as live. Refuse
    # to trade as a defensive layer.
    #
    # Critically, this guard only checks *daily-publishing* series. The
    # USD trade-weighted indexes (DTWEXBGS, DTWEXAFEGS) and WTI oil have a
    # natural ~7-day publication lag from FRED H.10; using the aggregate max
    # age would force HOLD on every signal under normal cadence.
    freshness = decision_packet.get('input_freshness') or {}
    daily_business_age_raw = freshness.get('daily_publishing_max_business_age_days')
    try:
        daily_business_age = float(daily_business_age_raw) if daily_business_age_raw is not None else None
    except (TypeError, ValueError):
        daily_business_age = None
    if daily_business_age is not None and daily_business_age > HARD_STALE_MARKET_SNAPSHOT_BUSINESS_DAYS:
        reason = (
            f'Daily-publishing macro series is hard-stale at {daily_business_age:.0f} business days — '
            f'refusing to trade until it refreshes within {HARD_STALE_MARKET_SNAPSHOT_BUSINESS_DAYS} business days.'
        )
        signal = _hold_signal(asset, reason)
        signal['decision_mode'] = decision_mode
        signal['validator_status'] = 'skipped'
        signal['validator_summary'] = reason
        signal['consensus_state'] = 'blocked'
        signal['block_reason'] = HARD_STALE_MACRO_BLOCK_REASON
        signal['llm_usage'] = _combine_usage(provider=config.parser_llm_base_url, stages=[])
        signal['decision_packet'] = {
            'decision_mode': decision_mode,
            'window_label': decision_packet['window_label'],
            'block_reason': HARD_STALE_MACRO_BLOCK_REASON,
            'input_freshness': decision_packet['input_freshness'],
            'market_snapshot': decision_packet['market_snapshot'],
            'event_flags': decision_packet['event_flags'],
        }
        logger.warning('[PARSER] Hard-stale macro snapshot for %s: %s', asset.symbol, reason)
        return signal

    daily_age_raw = freshness.get('daily_publishing_max_age_seconds')
    if daily_age_raw is None:
        # Backward-compatible fallback for callers that pre-date the
        # daily-age field — fall back to the aggregate max but only when
        # the freshness state already flags a problem.
        if str(freshness.get('market_snapshot_state', '')).lower() in {'blocked'}:
            daily_age_raw = freshness.get('market_snapshot_age_seconds')
    try:
        daily_age = float(daily_age_raw) if daily_age_raw is not None else None
    except (TypeError, ValueError):
        daily_age = None
    if (
        daily_business_age is None
        and daily_age is not None
        and daily_age > HARD_STALE_MARKET_SNAPSHOT_SECONDS
    ):
        days_old = daily_age / 86400.0
        reason = (
            f'Daily-publishing macro series is hard-stale at {daily_age:.0f}s (~{days_old:.1f} days) — '
            f'refusing to trade until it refreshes within {HARD_STALE_MARKET_SNAPSHOT_SECONDS}s.'
        )
        signal = _hold_signal(asset, reason)
        signal['decision_mode'] = decision_mode
        signal['validator_status'] = 'skipped'
        signal['validator_summary'] = reason
        signal['consensus_state'] = 'blocked'
        signal['block_reason'] = HARD_STALE_MACRO_BLOCK_REASON
        signal['llm_usage'] = _combine_usage(provider=config.parser_llm_base_url, stages=[])
        signal['decision_packet'] = {
            'decision_mode': decision_mode,
            'window_label': decision_packet['window_label'],
            'block_reason': HARD_STALE_MACRO_BLOCK_REASON,
            'input_freshness': decision_packet['input_freshness'],
            'market_snapshot': decision_packet['market_snapshot'],
            'event_flags': decision_packet['event_flags'],
        }
        logger.warning('[PARSER] Hard-stale macro snapshot for %s: %s', asset.symbol, reason)
        return signal

    debate_usage: list[dict[str, Any]] = []
    debate: dict[str, Any] | None = None
    candidate_usage: list[dict[str, Any]] = []
    candidate_graph: dict[str, Any] | None = None
    if decision_mode == 'analyst_debate':
        debate, debate_usage = _build_analyst_debate(
            asset=asset,
            decision_packet=decision_packet,
            config=config,
        )
        decision_packet = _enrich_decision_packet_with_debate(decision_packet, debate)
    elif decision_mode == 'tradingagents_candidate':
        try:
            candidate_graph, candidate_usage = run_tradingagents_candidate(
                asset=asset,
                decision_packet=decision_packet,
                config=config,
            )
        except Exception as exc:  # pragma: no cover - graph catches provider failures internally
            logger.warning('[CANDIDATE] Candidate graph unavailable for %s: %s', asset.symbol, exc)
            candidate_graph = {
                'mode': 'tradingagents_candidate',
                'degraded': True,
                'stages': [],
                'summary': 'Candidate graph unavailable; baseline parser remains authoritative.',
                'scratchpad_path': '',
                'final_decision': {'action': 'HOLD', 'confidence': 0.0, 'reasoning': str(exc), 'stop_loss_distance': 0.0, 'take_profit_distance': 0.0},
            }
            candidate_usage = []
        if candidate_graph is not None and not bool(candidate_graph.get('degraded')):
            decision_packet = _enrich_decision_packet_with_candidate(decision_packet, candidate_graph)

    parser_usage: dict[str, Any] | None = None
    response: Any | None = None
    parsed: dict[str, Any] | None = None
    if candidate_graph is not None and not bool(candidate_graph.get('degraded')):
        response = None
        parsed = dict(candidate_graph.get('final_decision') or {})
        parser_mode = 'candidate_graph'
    else:
        parser_client = create_chat_client(
            api_key=config.parser_llm_api_key,
            base_url=config.parser_llm_base_url,
        )
        logger.info('[PARSER] Calling %s for %s', config.parser_llm_model, asset.symbol)
        response, parsed, parser_mode = _request_json_completion(
            client=parser_client,
            model=config.parser_llm_model,
            base_url=config.parser_llm_base_url,
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
            signal = _hold_signal(asset, 'LLM returned invalid JSON')
            signal['decision_mode'] = decision_mode
            if candidate_graph is not None:
                signal['candidate_graph'] = candidate_graph
            return signal
        parser_usage = _extract_usage(
            response,
            provider=config.parser_llm_base_url,
            model=config.parser_llm_model,
            stage='parser',
        )
        parser_usage['response_mode'] = parser_mode

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
    signal = _normalize_signal(
        asset,
        parsed,
        fallback=fallback,
        allow_directional_fallback=parser_mode != 'candidate_graph',
    )
    signal['decision_mode'] = decision_mode
    if debate is not None:
        signal['debate'] = debate
    if candidate_graph is not None:
        signal['candidate_graph'] = candidate_graph

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
            base_url=config.validator_llm_base_url,
            messages=[
                {'role': 'system', 'content': _validator_system_prompt(asset)},
                {'role': 'user', 'content': _validator_user_prompt(asset, decision_packet, signal)},
            ],
            temperature=0.1,
            max_tokens=900,
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
    signal['decision_mode'] = decision_mode
    if debate is not None:
        signal['debate'] = debate
    if candidate_graph is not None:
        signal['candidate_graph'] = candidate_graph

    signal = _apply_price_conflict_guard(
        asset=asset,
        signal=signal,
        decision_packet=decision_packet,
    )

    signal = _apply_directional_persistence(
        asset=asset,
        signal=signal,
        decision_packet=decision_packet,
        config=config,
        window_label=window_label,
    )

    signal['llm_usage'] = _combine_usage(
        provider=config.parser_llm_base_url,
        stages=[stage for stage in [*debate_usage, *candidate_usage, parser_usage, validator_usage] if stage],
    )
    signal['decision_packet'] = {
        'decision_mode': decision_mode,
        'window_label': decision_packet['window_label'],
        'input_freshness': decision_packet['input_freshness'],
        'price_features': decision_packet['price_features'],
        'market_snapshot': decision_packet['market_snapshot'],
        'event_flags': decision_packet['event_flags'],
    }
    if debate is not None:
        signal['decision_packet']['debate'] = debate
    if candidate_graph is not None and not bool(candidate_graph.get('degraded')):
        signal['decision_packet']['candidate_graph'] = _compact_candidate_graph_for_packet(candidate_graph)
    if 'directional_persistence' in signal:
        signal['decision_packet']['directional_persistence'] = signal['directional_persistence']
    if 'price_conflict_guard' in signal:
        signal['decision_packet']['price_conflict_guard'] = signal['price_conflict_guard']
    return signal


def _apply_price_conflict_guard(
    *,
    asset: AssetProfile,
    signal: dict[str, Any],
    decision_packet: dict[str, Any],
    min_confidence: float = PRICE_CONFLICT_MIN_CONFIDENCE,
) -> dict[str, Any]:
    """Block mid-confidence trades that contradict structured price bias."""
    action = _direction_label(signal.get('action'))
    if action not in {'BUY', 'SELL'}:
        return signal

    raw_price_features = decision_packet.get('price_features')
    price_features: dict[str, Any] = raw_price_features if isinstance(raw_price_features, dict) else {}
    price_bias = _direction_label(price_features.get('price_bias'))
    if price_bias not in {'BUY', 'SELL'} or price_bias == action:
        return signal

    confidence = _safe_float(signal.get('confidence'), 0.0)
    if confidence >= min_confidence:
        return signal

    raw_market_snapshot = decision_packet.get('market_snapshot')
    market_snapshot: dict[str, Any] = raw_market_snapshot if isinstance(raw_market_snapshot, dict) else {}
    guard = {
        'policy': 'PRICE_BIAS_CONFLICT_LOW_CONFIDENCE',
        'reason': (
            f'Blocked {action} at confidence {confidence:.2f}: structured price bias is {price_bias} '
            f'and threshold is {min_confidence:.2f}.'
        ),
        'original_action': action,
        'original_confidence': round(confidence, 2),
        'price_bias': price_bias,
        'market_snapshot_overall_bias': _direction_label(market_snapshot.get('overall_bias')),
        'min_confidence': round(min_confidence, 2),
    }

    annotated = dict(signal)
    existing_reasoning = str(annotated.get('reasoning') or '').strip()
    annotated['reasoning'] = (f'{existing_reasoning} {guard["reason"]}'.strip())[:500]
    annotated['price_conflict_guard'] = guard
    return _add_trade_warning(annotated, 'PRICE_CONFLICT')


def _direction_label(value: Any) -> str:
    text = str(value or '').strip().upper()
    if text in {'BUY', 'BULL', 'BULLISH', 'LONG'}:
        return 'BUY'
    if text in {'SELL', 'BEAR', 'BEARISH', 'SHORT'}:
        return 'SELL'
    if text == 'HOLD':
        return 'HOLD'
    return text


def _add_trade_warning(signal: dict[str, Any], warning: str) -> dict[str, Any]:
    annotated = dict(signal)
    existing = annotated.get('trade_warnings')
    warnings = list(existing) if isinstance(existing, list) else []
    if warning not in warnings:
        warnings.append(warning)
    annotated['trade_warnings'] = warnings
    return annotated


def _apply_directional_persistence(
    *,
    asset: AssetProfile,
    signal: dict[str, Any],
    decision_packet: dict[str, Any],
    config: SignalConfig,
    window_label: str,
) -> dict[str, Any]:
    """Apply the cross-window persistence policy to the freshly-validated signal.

    Loads the persisted directional state for today, runs the policy with the
    proposed action/confidence and the current macro snapshot, then either
    leaves the signal unchanged, downgrades it to HOLD when the policy blocks
    a flip, or updates the persisted state when a new lock is set.
    """
    from xauex.signal.directional_persistence import (
        apply_directional_persistence,
        load_directional_state,
        macro_signature_from_market_snapshot,
        record_directional_state,
    )

    state_path_raw = getattr(config, 'directional_state_path', '') or ''
    if not state_path_raw:
        return signal

    london_date = _london_date_from_packet(decision_packet)
    if not london_date:
        return signal

    market_snapshot = decision_packet.get('market_snapshot') or {}
    proposed_signature = macro_signature_from_market_snapshot(market_snapshot)
    state_path = Path(state_path_raw)
    current_state = load_directional_state(state_path)

    decision = apply_directional_persistence(
        current_state=current_state,
        proposed_action=str(signal.get('action', 'HOLD')).upper(),
        proposed_confidence=float(signal.get('confidence', 0.0) or 0.0),
        proposed_macro_signature=proposed_signature,
        now_utc=datetime.now(timezone.utc),
        london_date=london_date,
        window_label=window_label,
    )

    annotated = dict(signal)
    annotated['directional_persistence'] = {
        'policy': decision.policy,
        'reason': decision.reason,
        'previous_state': current_state,
        'next_state': decision.next_state,
    }
    if decision.action != str(signal.get('action', 'HOLD')).upper() or decision.confidence != float(signal.get('confidence', 0.0) or 0.0):
        if decision.action == 'HOLD' and str(signal.get('action', 'HOLD')).upper() in {'BUY', 'SELL'}:
            existing_reasoning = str(annotated.get('reasoning') or '').strip()
            annotated['reasoning'] = (f'{existing_reasoning} {decision.reason}'.strip())[:500]
            annotated = _add_trade_warning(annotated, decision.policy)
        else:
            annotated['action'] = decision.action
            annotated['confidence'] = round(decision.confidence, 2)

    if decision.next_state is not None:
        try:
            record_directional_state(state_path, decision.next_state)
        except OSError as exc:
            logger.warning('[DIRECTIONAL_STATE] Failed to persist %s: %s', state_path, exc)

    return annotated


def _london_date_from_packet(decision_packet: dict[str, Any]) -> str:
    profile = decision_packet.get('session_profile') or {}
    timezone_name = str(profile.get('timezone') or 'Europe/London')
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = ZoneInfo('Europe/London')
    return datetime.now(tz).strftime('%Y-%m-%d')


def _normalize_signal(
    asset: AssetProfile,
    parsed: dict,
    *,
    fallback: dict[str, Any],
    allow_directional_fallback: bool = True,
) -> dict:
    action = str(parsed.get('action', 'HOLD')).upper()
    if action not in {'BUY', 'SELL', 'HOLD'}:
        action = fallback['action']

    try:
        confidence = float(parsed.get('confidence', 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    reasoning = str(parsed.get('reasoning', 'No reasoning provided'))[:500]

    if allow_directional_fallback and action == 'HOLD' and not _reasoning_hard_blocker(reasoning):
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
    return estimate_cost_usd(model, prompt_tokens, completion_tokens)


def _model_completion_options(
    model: str,
    *,
    max_tokens: int,
    base_url: str | None = None,
    response_schema: dict[str, Any] | None = None,
    json_object: bool = False,
) -> dict[str, Any]:
    return completion_options(
        model,
        max_tokens=max_tokens,
        base_url=base_url,
        response_schema=response_schema,
        json_object=json_object,
    )


def _supports_strict_json_schema(model: str) -> bool:
    return supports_strict_json_schema(model)


def _request_json_completion(
    *,
    client: Any,
    model: str,
    base_url: str | None = None,
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
                    base_url=base_url,
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
                    base_url=base_url,
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
                    base_url=base_url,
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
                **request_temperature_kwargs(model, temperature),
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
  "confidence_adjustment": -0.15 to 0.10,
  "reasoning": "one concise sentence",
  "hard_blocker": true | false
}}

Rules:
- BLOCK only for hard blockers such as stale inputs during a live window, missing core tradeable data, or invalid trade geometry.
- ALIGNED is the default. Markets are mixed by nature — uncertainty alone is not a reason to disagree.
- Use DISAGREE only when the proposed trade direction is actively contradicted by the data, not when it lacks corroboration.

Direct contradiction detection (return DISAGREE with adjustment up to -0.15 only when ANY of these apply):
- The trade direction contradicts 2+ macro drivers simultaneously (e.g. proposes BUY gold while BOTH the DXY and the 10Y yield are clearly rising on the day, with no offsetting safe-haven catalyst).
- The decision_packet's `memory_summary.consecutive_loss_direction` field matches the proposed direction (e.g. consecutive_loss_direction="SELL_3" and the proposed action is SELL) AND no fresh macro driver or regime change has been cited.
- The proposed action and the price-features `regime_filter` disagree — for example, proposing SELL when `regime_filter`="TREND_ALIGNED_UPPER_THIRD_KEPT_BUY".
- Trade geometry is unsafe (e.g. a 2.5x risk-reward target proposed inside a flat range with ATR < take-profit/3).

Hedged or uncertain reasoning is NOT by itself a direct contradiction — prefer ALIGNED with adjustment 0. Reserve DISAGREE for direct contradictions only."""


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
                    'minimum': -0.15,
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


def _enrich_decision_packet_with_candidate(
    packet: dict[str, Any],
    candidate_graph: dict[str, Any] | None,
) -> dict[str, Any]:
    if not candidate_graph or bool(candidate_graph.get('degraded')):
        return dict(packet)
    enriched = dict(packet)
    enriched['candidate_graph'] = _compact_candidate_graph_for_packet(candidate_graph)
    return enriched


def _compact_candidate_graph_for_packet(candidate_graph: dict[str, Any]) -> dict[str, Any]:
    return {
        'mode': candidate_graph.get('mode'),
        'degraded': bool(candidate_graph.get('degraded')),
        'stages': list(candidate_graph.get('stages') or []),
        'summary': candidate_graph.get('summary'),
        'scratchpad_path': candidate_graph.get('scratchpad_path'),
        'final_decision': candidate_graph.get('final_decision'),
    }


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
        merged['validator_status'] = 'reviewed'
        merged['validator_summary'] = reasoning or 'Validator blocked the trade.'
        merged['validator_hard_blocker'] = True
        merged['consensus_state'] = 'blocked'
        return _add_trade_warning(merged, 'VALIDATOR_HARD_BLOCKER')

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
    choices = getattr(response, 'choices', None)
    if not choices:
        logger.warning('[PARSER] Model response contained empty choices.')
        return None
    message = getattr(choices[0], 'message', None)
    raw_content = getattr(message, 'content', None)
    if raw_content is None:
        logger.warning('[PARSER] Model response choice contained no message content.')
        return None
    raw = str(raw_content).strip()
    if not raw:
        logger.warning('[PARSER] Model response content was empty.')
        return None
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
