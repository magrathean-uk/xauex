"""Generate a short human-readable oracle brief for the latest run."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from xauex.signal.assets import AssetProfile
from xauex.signal.config import SignalConfig
from xauex.shared.llm_client import create_chat_client

logger = logging.getLogger(__name__)

_TOKEN_PRICES_USD_PER_MILLION: dict[str, tuple[float, float]] = {
    'llama-3.1-8b-instant': (0.05, 0.08),
    'llama-3.3-70b-versatile': (0.59, 0.79),
    'openai/gpt-oss-20b': (0.075, 0.30),
    'openai/gpt-oss-120b': (0.15, 0.60),
}


def write_brief(
    *,
    asset: AssetProfile,
    signal: dict[str, Any],
    actions: list[dict[str, Any]],
    report_markdown: str,
    simulation_id: str | None,
    report_id: str | None,
    output_path: str,
    config: SignalConfig,
) -> dict[str, Any]:
    client = create_chat_client(
        api_key=config.brief_llm_api_key,
        base_url=config.brief_llm_base_url,
    )
    action_lines = _action_lines(actions)
    prompt = (
        f'Asset: {asset.symbol}\n'
        f'Signal: {signal.get("action", "HOLD")}\n'
        f'Confidence: {signal.get("confidence", 0.0)}\n'
        f'Reasoning: {signal.get("reasoning", "")}\n'
        f'Simulation ID: {simulation_id or ""}\n'
        f'Report ID: {report_id or ""}\n\n'
        'Recent agent actions:\n'
        f'{action_lines}\n\n'
        'Report excerpt:\n'
        f'{report_markdown[:8000]}\n'
    )
    system = (
        'You write a very short human trading brief for an operator dashboard.\n'
        'Return ONLY valid JSON with this schema:\n'
        '{\n'
        '  "title": "short title",\n'
        '  "summary_markdown": "markdown under 140 words",\n'
        '  "key_points": ["point 1", "point 2", "point 3"]\n'
        '}\n'
        'Rules:\n'
        '- Keep it under half an A4 page.\n'
        '- Use plain English.\n'
        '- First sentence must clearly say BUY, SELL, or HOLD and why.\n'
        '- Explain the causal chain simply, like what happened and why it matters for gold.\n'
        '- Do not mention being an AI.\n'
        '- Do not include code fences.\n'
        '- Keep key_points to at most 3 short bullets.'
    )

    response = client.chat.completions.create(
        model=config.brief_llm_model,
        messages=[
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': prompt},
        ],
        temperature=0.1,
        max_tokens=260,
    )
    raw = (response.choices[0].message.content or '').strip()
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.error('[BRIEF] Invalid JSON from model: %s', raw[:400])
        parsed = _fallback_brief(signal)

    usage = _extract_usage(response)
    doc = _render_markdown(
        title=str(parsed.get('title') or f'{asset.symbol} {signal.get("action", "HOLD")} Brief'),
        summary_markdown=str(parsed.get('summary_markdown') or _fallback_brief(signal)['summary_markdown']),
        key_points=parsed.get('key_points') or [],
        signal=signal,
        simulation_id=simulation_id,
        report_id=report_id,
        usage=usage,
    )
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(doc, encoding='utf-8')
    meta = {
        'title': str(parsed.get('title') or f'{asset.symbol} {signal.get("action", "HOLD")} Brief'),
        'updated_at_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'path': str(target),
        'report_id': report_id,
        'simulation_id': simulation_id,
        'usage': usage,
    }
    target.with_suffix('.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
    logger.info('[BRIEF] Brief written to %s', target)
    return meta


def _action_lines(actions: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in actions[-12:]:
        agent = str(item.get('agent_name') or item.get('agent_id') or 'agent')
        action = str(item.get('action_type') or item.get('type') or 'action')
        content = str(item.get('content') or item.get('action_args') or '')
        compact = ' '.join(content.split())[:180]
        lines.append(f'- {agent}: {action} {compact}'.strip())
    return '\n'.join(lines) or '- No recent actions available.'


def _fallback_brief(signal: dict[str, Any]) -> dict[str, Any]:
    action = str(signal.get('action') or 'HOLD').upper()
    reasoning = str(signal.get('reasoning') or 'No detailed rationale was available.').strip()
    return {
        'title': f'{signal.get("symbol", "XAUUSD")} {action} Brief',
        'summary_markdown': f'**{action}** because the latest oracle run leaned that way, with the main rationale: {reasoning}',
        'key_points': [
            f'Action: {action}',
            f'Confidence: {signal.get("confidence", 0.0)}',
        ],
    }


def _render_markdown(
    *,
    title: str,
    summary_markdown: str,
    key_points: list[Any],
    signal: dict[str, Any],
    simulation_id: str | None,
    report_id: str | None,
    usage: dict[str, Any],
) -> str:
    points = [str(point).strip() for point in key_points[:3] if str(point).strip()]
    bullet_block = '\n'.join(f'- {point}' for point in points) or '- No extra highlights.'
    return (
        f'# {title}\n\n'
        f'{summary_markdown.strip()}\n\n'
        '## Key Points\n\n'
        f'{bullet_block}\n\n'
        '## Run Metadata\n\n'
        f'- Signal: {signal.get("action", "HOLD")}\n'
        f'- Confidence: {signal.get("confidence", 0.0)}\n'
        f'- Symbol: {signal.get("symbol", "XAUUSD")}\n'
        f'- Simulation ID: {simulation_id or "-"}\n'
        f'- Report ID: {report_id or "-"}\n'
        f'- Generated: {datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}\n'
        f'- Model: {usage.get("model", "-")}\n'
        f'- Prompt tokens: {usage.get("prompt_tokens", 0)}\n'
        f'- Completion tokens: {usage.get("completion_tokens", 0)}\n'
        f'- Estimated cost (USD): {usage.get("estimated_cost_usd", 0)}\n'
    )


def _extract_usage(response: Any) -> dict[str, Any]:
    usage = getattr(response, 'usage', None)
    if usage is None:
        return {}
    model = getattr(response, 'model', '')
    prompt_tokens = int(getattr(usage, 'prompt_tokens', 0) or 0)
    completion_tokens = int(getattr(usage, 'completion_tokens', 0) or 0)
    return {
        'model': model,
        'prompt_tokens': prompt_tokens,
        'completion_tokens': completion_tokens,
        'total_tokens': int(getattr(usage, 'total_tokens', 0) or 0),
        'estimated_cost_usd': _estimate_cost_usd(model, prompt_tokens, completion_tokens),
    }


def _estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    prices = _TOKEN_PRICES_USD_PER_MILLION.get(model)
    if prices is None:
        return None
    input_price, output_price = prices
    return round((prompt_tokens / 1_000_000 * input_price) + (completion_tokens / 1_000_000 * output_price), 8)
