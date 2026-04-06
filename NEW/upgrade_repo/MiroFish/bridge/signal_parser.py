"""Parse MiroFish output into a structured asset-aware trading signal."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from openai import OpenAI

from bridge.assets import AssetProfile
from bridge.config import BridgeConfig

logger = logging.getLogger(__name__)


def parse_signal(
    *,
    asset: AssetProfile,
    actions: list,
    report_markdown: str,
    config: BridgeConfig,
) -> dict:
    client = OpenAI(api_key=config.deepseek_api_key, base_url=config.deepseek_base_url)

    action_summary = _summarize_actions(actions)
    system_prompt = _system_prompt(asset)
    user_prompt = (
        f'Asset: {asset.symbol}\n'
        f'Asset class: {asset.asset_class}\n\n'
        'Simulation report:\n\n'
        f'{report_markdown[:12000]}\n\n'
        '---\n'
        'Recent agent actions:\n\n'
        f'{action_summary[:5000]}\n\n'
        f'Based on all of the above, what is the best directional signal for {asset.symbol}?'
    )

    logger.info(
        '[PARSER] Calling DeepSeek for %s (%d chars report, %d actions)',
        asset.symbol,
        len(report_markdown),
        len(actions),
    )

    response = client.chat.completions.create(
        model=config.deepseek_model,
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

    return _normalize_signal(asset, parsed)


def _normalize_signal(asset: AssetProfile, parsed: dict) -> dict:
    action = str(parsed.get('action', 'HOLD')).upper()
    if action not in {'BUY', 'SELL', 'HOLD'}:
        action = 'HOLD'

    try:
        confidence = float(parsed.get('confidence', 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    if confidence < 0.6:
        action = 'HOLD'

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
        'reasoning': str(parsed.get('reasoning', 'No reasoning provided'))[:500],
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


def _system_prompt(asset: AssetProfile) -> str:
    return f"""You are a macro trading signal analyst.

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
- If confidence < 0.6, action MUST be HOLD.
- Distances must be expressed in {asset.distance_unit}.
- stop_loss_distance must stay inside [{asset.min_stop_loss_distance}, {asset.max_stop_loss_distance}].
- take_profit_distance should be between {asset.min_take_profit_rr}x and {asset.max_take_profit_rr}x the stop loss.
- If the simulation is mixed or internally conflicted, prefer HOLD."""


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
