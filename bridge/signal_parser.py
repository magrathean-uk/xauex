"""Parse MiroFish output into a structured asset-aware trading signal."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from math import exp
from typing import Any

from bridge.assets import AssetProfile
from bridge.config import BridgeConfig

logger = logging.getLogger(__name__)

_TOKEN_PRICES_USD_PER_MILLION: dict[str, tuple[float, float]] = {
    'deepseek-chat': (0.28, 0.42),
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
    config: BridgeConfig,
) -> dict:
    from openai import OpenAI

    client = OpenAI(api_key=config.parser_llm_api_key, base_url=config.parser_llm_base_url)

    action_summary = _summarize_actions(actions)
    feature_snapshot = _build_feature_snapshot(actions, report_markdown)
    system_prompt = _system_prompt(asset)
    user_prompt = (
        f'Asset: {asset.symbol}\n'
        f'Asset class: {asset.asset_class}\n\n'
        'Trading objective:\n'
        '- One trade only around the London open.\n'
        '- Bias must target the move from London open through late morning / midday London time.\n'
        '- Prefer the stronger side rather than staying flat unless there is a hard execution blocker.\n\n'
        'Feature snapshot:\n\n'
        f'{feature_snapshot}\n\n'
        '---\n'
        'Simulation report:\n\n'
        f'{report_markdown[:12000]}\n\n'
        '---\n'
        'Recent agent actions:\n\n'
        f'{action_summary[:5000]}\n\n'
        f'Based on all of the above, what is the best directional signal for {asset.symbol}?'
    )

    logger.info(
        '[PARSER] Calling %s for %s (%d chars report, %d actions)',
        config.parser_llm_model,
        asset.symbol,
        len(report_markdown),
        len(actions),
    )

    response = client.chat.completions.create(
        model=config.parser_llm_model,
        messages=[
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ],
        temperature=0.1,
        max_tokens=350,
    )

    raw = (response.choices[0].message.content or '').strip()
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0].strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.error('[PARSER] Invalid JSON from model: %s', raw[:300])
        return _hold_signal(asset, 'LLM returned invalid JSON')

    fallback = _fallback_direction(actions, report_markdown, parsed.get('reasoning'))
    signal = _normalize_signal(asset, parsed, fallback=fallback)
    signal['llm_usage'] = _extract_usage(response, config)
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


def _extract_usage(response: Any, config: BridgeConfig) -> dict:
    usage = getattr(response, 'usage', None)
    if usage is None:
        return {
            'provider': config.parser_llm_base_url,
            'model': config.parser_llm_model,
            'usage_available': False,
        }

    prompt_tokens = int(getattr(usage, 'prompt_tokens', 0) or 0)
    completion_tokens = int(getattr(usage, 'completion_tokens', 0) or 0)
    total_tokens = int(getattr(usage, 'total_tokens', prompt_tokens + completion_tokens) or 0)
    estimated_cost_usd = _estimate_cost_usd(
        model=config.parser_llm_model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    if estimated_cost_usd is not None:
        logger.info(
            '[PARSER] Usage model=%s prompt=%d completion=%d total=%d est_cost_usd=%.6f',
            config.parser_llm_model,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            estimated_cost_usd,
        )
    else:
        logger.info(
            '[PARSER] Usage model=%s prompt=%d completion=%d total=%d',
            config.parser_llm_model,
            prompt_tokens,
            completion_tokens,
            total_tokens,
        )
    return {
        'provider': config.parser_llm_base_url,
        'model': config.parser_llm_model,
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


def _system_prompt(asset: AssetProfile) -> str:
    return f"""You are a London morning trading signal analyst.

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
- This signal is only for a single London morning trade, not a multi-day swing.
- Focus on the expected net move from London open through late morning / midday London time.
- Make the answer decisive and tradeable."""


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
    action = 'BUY' if score >= 0 else 'SELL'
    total_hits = bullish_hits + bearish_hits
    conflict_ratio = (min(bullish_hits, bearish_hits) / total_hits) if total_hits else 0.5
    margin = abs(score) / max(total_hits, 1)
    confidence = 0.51 + (0.17 / (1.0 + exp(-6.0 * (margin - 0.25))))
    confidence = max(0.51, min(0.68, confidence))
    summary = (
        f'Fallback bias {action} from action/report score {score} '
        f'(bullish_hits={bullish_hits}, bearish_hits={bearish_hits}, conflict_ratio={conflict_ratio:.2f}).'
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
