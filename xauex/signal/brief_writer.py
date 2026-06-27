"""Generate a short human-readable oracle brief for the latest run."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from xauex.signal.assets import AssetProfile
from xauex.signal.config import SignalConfig
from xauex.signal.llm_models import completion_options, estimate_cost_usd, request_temperature_kwargs
from xauex.shared.llm_client import create_chat_client

logger = logging.getLogger(__name__)

BRIEF_MAX_TOKENS = 260
GEMINI_35_BRIEF_MAX_TOKENS = 1600
GEMINI_35_BRIEF_RETRY_MAX_TOKENS = 2400


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
        '- Keep key_points to at most 3 short bullets.\n'
        '\n'
        'Critical bias semantics (avoid common mistake):\n'
        "- 'gold_signal=BULLISH' on a series means that series move is supportive of gold going up. It does NOT mean the underlying went up.\n"
        "- A FALLING DXY means USD weakened — which is gold_signal=BULLISH (gold-supportive). Never describe a falling DXY as 'strengthening USD'.\n"
        "- A FALLING US10Y yield is gold_signal=BULLISH because it lowers the opportunity cost of holding gold.\n"
        "- A RISING breakeven inflation is gold_signal=BULLISH (gold as inflation hedge).\n"
        "- Use the per-row interpretation strings in the report (e.g. 'USD weakened — gold-supportive') verbatim where useful instead of inventing your own narrative."
    )

    parsed, responses = _request_brief_json(
        client=client,
        model=config.brief_llm_model,
        base_url=config.brief_llm_base_url,
        messages=[
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': prompt},
        ],
    )
    if parsed is None:
        parsed = _fallback_brief(signal)

    usage = _extract_usage(responses, model_hint=config.brief_llm_model)
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


def _request_brief_json(
    *,
    client: Any,
    model: str,
    base_url: str,
    messages: list[dict[str, str]],
) -> tuple[dict[str, Any] | None, list[Any]]:
    budgets = [_brief_max_tokens(model)]
    retry_budget = _brief_retry_max_tokens(model, budgets[0])
    if retry_budget > budgets[0]:
        budgets.append(retry_budget)

    responses: list[Any] = []
    for index, max_tokens in enumerate(budgets, start=1):
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            **request_temperature_kwargs(model, 0.1),
            **completion_options(
                model,
                max_tokens=max_tokens,
                base_url=base_url,
                json_object=True,
            ),
        )
        responses.append(response)
        raw = _completion_content(response)
        parsed = _parse_json_object(raw)
        if parsed is not None:
            if index > 1:
                logger.info("[BRIEF] JSON parsed after retry %d.", index)
            return parsed, responses

        finish_reason = _finish_reason(response)
        if index < len(budgets):
            logger.warning(
                "[BRIEF] Invalid JSON from model on attempt %d/%d "
                "(finish_reason=%s, content_len=%d); retrying with max_tokens=%d.",
                index,
                len(budgets),
                finish_reason or "-",
                len(raw),
                budgets[index],
            )
        else:
            logger.error(
                "[BRIEF] Invalid JSON from model after %d attempt(s) "
                "(finish_reason=%s): %s",
                len(budgets),
                finish_reason or "-",
                raw[:400],
            )
    return None, responses


def _brief_max_tokens(model: str) -> int:
    if _is_gemini_35_flash(model):
        return GEMINI_35_BRIEF_MAX_TOKENS
    return BRIEF_MAX_TOKENS


def _brief_retry_max_tokens(model: str, first_budget: int) -> int:
    if _is_gemini_35_flash(model):
        return max(first_budget, GEMINI_35_BRIEF_RETRY_MAX_TOKENS)
    return first_budget


def _is_gemini_35_flash(model: str) -> bool:
    return str(model or "") in {"gemini-3.5-flash", "google/gemini-3.5-flash"}


def _completion_content(response: Any) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if content is None:
        return ""
    raw = content if isinstance(content, str) else str(content)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return raw


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _finish_reason(response: Any) -> str:
    raw = getattr(response, "raw", None)
    if not isinstance(raw, dict):
        return ""
    choices = raw.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    return str(choices[0].get("finish_reason") or "")


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


def _extract_usage(responses: Any, *, model_hint: str = '') -> dict[str, Any]:
    if not isinstance(responses, list):
        responses = [responses]
    responses = [response for response in responses if response is not None]
    if not responses:
        return {}
    models: list[str] = []
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    estimated_cost = 0.0
    cost_available = False
    for response in responses:
        usage = getattr(response, 'usage', None)
        if usage is None:
            continue
        model = getattr(response, 'model', '') or model_hint
        if model and model not in models:
            models.append(model)
        prompt = int(getattr(usage, 'prompt_tokens', 0) or 0)
        completion = int(getattr(usage, 'completion_tokens', 0) or 0)
        prompt_tokens += prompt
        completion_tokens += completion
        total_tokens += int(getattr(usage, 'total_tokens', 0) or 0)
        attempt_cost = _estimate_cost_usd(model, prompt, completion)
        if attempt_cost is not None:
            estimated_cost += attempt_cost
            cost_available = True
    model = '+'.join(models) if models else model_hint
    return {
        'model': model,
        'prompt_tokens': prompt_tokens,
        'completion_tokens': completion_tokens,
        'total_tokens': total_tokens,
        'estimated_cost_usd': round(estimated_cost, 8) if cost_available else None,
    }


def _estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    return estimate_cost_usd(model, prompt_tokens, completion_tokens)
