"""TradingAgents-inspired candidate decision graph for XAUEX signals."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from xauex.signal.assets import AssetProfile
from xauex.signal.config import SignalConfig
from xauex.signal.llm_models import (
    completion_options,
    estimate_cost_usd,
    request_temperature_kwargs,
    supports_strict_json_schema,
)
from xauex.signal.scratchpad import (
    record_final_decision,
    record_stage_error,
    record_stage_result,
    record_stage_start,
)
from xauex.shared.llm_client import create_chat_client

logger = logging.getLogger(__name__)

_STAGES = [
    "market_analyst",
    "bull_case",
    "bear_case",
    "trader_proposal",
    "risk_reviewer",
    "portfolio_decision",
]

def run_tradingagents_candidate(
    *,
    asset: AssetProfile,
    decision_packet: dict[str, Any],
    config: SignalConfig,
    client_factory: Any | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run a compact analyst/trader/risk/portfolio graph over a frozen packet."""
    scratchpad_path = _scratchpad_path(config=config, asset=asset)
    graph: dict[str, Any] = {
        "mode": "tradingagents_candidate",
        "degraded": False,
        "stages": list(_STAGES),
        "summary": "TradingAgents candidate graph completed from the same XAUEX packet.",
        "scratchpad_path": str(scratchpad_path),
        "final_decision": _hold_decision(asset, "Candidate graph has not produced a decision."),
        "stage_outputs": {},
    }
    usage_stages: list[dict[str, Any]] = []
    factory = client_factory or create_chat_client
    client = factory(
        api_key=config.parser_llm_api_key,
        base_url=config.parser_llm_base_url,
    )
    stage_outputs: dict[str, dict[str, Any]] = {}

    for stage in _STAGES:
        prompt = _stage_user_prompt(asset=asset, stage=stage, decision_packet=decision_packet, stage_outputs=stage_outputs)
        record_stage_start(scratchpad_path, stage=stage, prompt=prompt)
        try:
            response, parsed, response_mode, attempts = _request_json_completion(
                client=client,
                model=_model_for_stage(config, stage),
                base_url=config.parser_llm_base_url,
                messages=[
                    {"role": "system", "content": _stage_system_prompt(asset=asset, stage=stage)},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=_max_tokens_for_stage(stage),
                response_schema=_stage_response_schema(asset=asset, stage=stage),
            )
        except Exception as exc:  # pragma: no cover - provider/client dependent
            record_stage_error(scratchpad_path, stage=stage, error=str(exc), retries=0)
            logger.warning("[CANDIDATE] %s failed for %s: %s", stage, asset.symbol, exc)
            return _degraded_graph(asset=asset, scratchpad_path=scratchpad_path, stage_outputs=stage_outputs), usage_stages

        if parsed is None or response is None:
            record_stage_error(
                scratchpad_path,
                stage=stage,
                error="Invalid or missing JSON response.",
                response_mode=response_mode,
                retries=max(0, attempts - 1),
            )
            logger.warning("[CANDIDATE] %s returned invalid JSON for %s", stage, asset.symbol)
            return _degraded_graph(asset=asset, scratchpad_path=scratchpad_path, stage_outputs=stage_outputs), usage_stages

        parsed = _sanitize_stage_output(asset=asset, stage=stage, parsed=parsed)
        stage_outputs[stage] = parsed
        usage = _extract_usage(
            response,
            provider=config.parser_llm_base_url,
            model=_model_for_stage(config, stage),
            stage=stage,
        )
        usage["response_mode"] = response_mode
        usage_stages.append(usage)
        record_stage_result(
            scratchpad_path,
            stage=stage,
            parsed=parsed,
            response_mode=response_mode,
            usage=usage,
            retries=max(0, attempts - 1),
        )

    final_decision = _portfolio_decision(asset=asset, stage_outputs=stage_outputs)
    graph["final_decision"] = final_decision
    graph["stage_outputs"] = stage_outputs
    graph["summary"] = _candidate_summary(stage_outputs=stage_outputs, final_decision=final_decision)
    record_final_decision(scratchpad_path, decision=final_decision)
    return graph, usage_stages


def _scratchpad_path(*, config: SignalConfig, asset: AssetProfile) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex[:8]
    return Path(config.archive_dir) / "candidate_scratchpads" / f"{timestamp}_{asset.symbol.lower()}_{suffix}.jsonl"


def _degraded_graph(
    *,
    asset: AssetProfile,
    scratchpad_path: Path,
    stage_outputs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    final_decision = _hold_decision(asset, "Candidate graph degraded; baseline parser remains authoritative.")
    record_final_decision(scratchpad_path, decision=final_decision)
    return {
        "mode": "tradingagents_candidate",
        "degraded": True,
        "stages": list(_STAGES),
        "summary": "Candidate graph degraded; baseline parser remains authoritative.",
        "scratchpad_path": str(scratchpad_path),
        "final_decision": final_decision,
        "stage_outputs": stage_outputs,
    }


def _stage_system_prompt(*, asset: AssetProfile, stage: str) -> str:
    if stage == "market_analyst":
        role = "market analyst summarizing structure, freshness, and context"
    elif stage == "bull_case":
        role = "bull analyst arguing the strongest BUY thesis"
    elif stage == "bear_case":
        role = "bear analyst arguing the strongest SELL thesis"
    elif stage == "trader_proposal":
        role = "trader proposing one BUY, SELL, or HOLD decision"
    elif stage == "risk_reviewer":
        role = "risk reviewer checking freshness, geometry, and trade discipline"
    else:
        role = "portfolio manager producing the final schema-compatible decision"
    return (
        f"You are the XAUEX {role} for {asset.symbol}. "
        "Respond only with valid JSON matching the requested schema. "
        "Do not change XAUEX risk limits or execution policy."
    )


def _stage_user_prompt(
    *,
    asset: AssetProfile,
    stage: str,
    decision_packet: dict[str, Any],
    stage_outputs: dict[str, dict[str, Any]],
) -> str:
    prior = json.dumps(stage_outputs, indent=2, sort_keys=True)
    packet = json.dumps(decision_packet, indent=2, sort_keys=True)
    if stage == "market_analyst":
        task = "Summarize the current price structure, market snapshot freshness, and most relevant context items."
    elif stage == "bull_case":
        task = "Build the strongest BUY thesis from the packet and market analyst output only."
    elif stage == "bear_case":
        task = "Build the strongest SELL thesis from the packet and prior outputs only."
    elif stage == "trader_proposal":
        task = (
            "Choose one provisional BUY, SELL, or HOLD signal. "
            f"Distances must be in {asset.distance_unit} and stay inside XAUEX geometry limits."
        )
    elif stage == "risk_reviewer":
        task = "Review the trader proposal for hard blockers, stale inputs, overconfidence, or invalid geometry."
    else:
        task = "Produce the final schema-compatible XAUEX signal from the proposal and risk review."
    return (
        f"Asset: {asset.symbol}\n"
        f"Stage: {stage}\n"
        f"Task: {task}\n\n"
        f"Prior stage outputs:\n{prior}\n\n"
        f"Decision packet:\n{packet}"
    )


def _stage_response_schema(*, asset: AssetProfile, stage: str) -> dict[str, Any]:
    if stage == "market_analyst":
        schema = {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "minLength": 1, "maxLength": 300},
                "price_structure": {"type": "string", "minLength": 1, "maxLength": 240},
                "snapshot_freshness": {"type": "string", "minLength": 1, "maxLength": 120},
                "key_context": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 160},
                    "maxItems": 4,
                },
            },
            "required": ["summary", "price_structure", "snapshot_freshness", "key_context"],
            "additionalProperties": False,
        }
    elif stage in {"bull_case", "bear_case"}:
        schema = {
            "type": "object",
            "properties": {
                "thesis": {"type": "string", "minLength": 1, "maxLength": 280},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "key_points": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 160},
                    "minItems": 1,
                    "maxItems": 4,
                },
                "risks": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 160},
                    "maxItems": 4,
                },
            },
            "required": ["thesis", "confidence", "key_points", "risks"],
            "additionalProperties": False,
        }
    elif stage in {"trader_proposal", "portfolio_decision"}:
        schema = _decision_schema(asset)
    else:
        schema = {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": ["APPROVE", "REDUCE_CONFIDENCE", "BLOCK"]},
                "confidence_adjustment": {"type": "number", "minimum": -0.4, "maximum": 0.1},
                "reasoning": {"type": "string", "minLength": 1, "maxLength": 260},
                "hard_blocker": {"type": "boolean"},
            },
            "required": ["decision", "confidence_adjustment", "reasoning", "hard_blocker"],
            "additionalProperties": False,
        }
    return {
        "name": f"xauex_candidate_{stage}",
        "strict": True,
        "schema": schema,
    }


def _decision_schema(asset: AssetProfile) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "reasoning": {"type": "string", "minLength": 1, "maxLength": 500},
            "stop_loss_distance": {
                "type": "number",
                "minimum": asset.min_stop_loss_distance,
                "maximum": asset.max_stop_loss_distance,
            },
            "take_profit_distance": {
                "type": "number",
                "minimum": round(asset.min_stop_loss_distance * asset.min_take_profit_rr, 2),
                "maximum": round(asset.max_stop_loss_distance * asset.max_take_profit_rr, 2),
            },
        },
        "required": ["action", "confidence", "reasoning", "stop_loss_distance", "take_profit_distance"],
        "additionalProperties": False,
    }


def _sanitize_stage_output(*, asset: AssetProfile, stage: str, parsed: dict[str, Any]) -> dict[str, Any]:
    if stage in {"trader_proposal", "portfolio_decision"}:
        return _sanitize_decision(asset=asset, parsed=parsed)
    if stage == "risk_reviewer":
        decision = str(parsed.get("decision", "APPROVE") or "APPROVE").upper()
        if decision not in {"APPROVE", "REDUCE_CONFIDENCE", "BLOCK"}:
            decision = "REDUCE_CONFIDENCE"
        return {
            "decision": decision,
            "confidence_adjustment": round(max(-0.4, min(0.1, _safe_float(parsed.get("confidence_adjustment"), 0.0))), 2),
            "reasoning": str(parsed.get("reasoning") or "Risk review completed.")[:260],
            "hard_blocker": bool(parsed.get("hard_blocker")),
        }
    if stage in {"bull_case", "bear_case"}:
        return {
            "thesis": str(parsed.get("thesis") or "")[:280],
            "confidence": round(max(0.0, min(1.0, _safe_float(parsed.get("confidence"), 0.0))), 2),
            "key_points": _string_list(parsed.get("key_points"), max_items=4, max_len=160),
            "risks": _string_list(parsed.get("risks"), max_items=4, max_len=160),
        }
    return {
        "summary": str(parsed.get("summary") or "")[:300],
        "price_structure": str(parsed.get("price_structure") or "")[:240],
        "snapshot_freshness": str(parsed.get("snapshot_freshness") or "")[:120],
        "key_context": _string_list(parsed.get("key_context"), max_items=4, max_len=160),
    }


def _portfolio_decision(*, asset: AssetProfile, stage_outputs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    final = _sanitize_decision(asset=asset, parsed=stage_outputs.get("portfolio_decision") or {})
    risk = stage_outputs.get("risk_reviewer") or {}
    risk_decision = str(risk.get("decision") or "").upper()
    if bool(risk.get("hard_blocker")) or risk_decision == "BLOCK":
        return _hold_decision(asset, str(risk.get("reasoning") or "Candidate risk reviewer blocked the trade."))
    if risk_decision == "REDUCE_CONFIDENCE":
        final["confidence"] = round(max(0.0, min(1.0, final["confidence"] + _safe_float(risk.get("confidence_adjustment"), 0.0))), 2)
        if final["confidence"] <= 0.0:
            return _hold_decision(asset, str(risk.get("reasoning") or "Candidate confidence was reduced to zero."))
    return final


def _sanitize_decision(*, asset: AssetProfile, parsed: dict[str, Any]) -> dict[str, Any]:
    action = str(parsed.get("action", "HOLD") or "HOLD").upper()
    if action not in {"BUY", "SELL", "HOLD"}:
        action = "HOLD"
    confidence = round(max(0.0, min(1.0, _safe_float(parsed.get("confidence"), 0.0))), 2)
    reasoning = str(parsed.get("reasoning") or "Candidate decision generated.")[:500]
    sl = max(asset.min_stop_loss_distance, min(asset.max_stop_loss_distance, _safe_float(parsed.get("stop_loss_distance"), asset.default_stop_loss_distance)))
    tp = _safe_float(parsed.get("take_profit_distance"), sl * 2.0)
    tp = max(sl * asset.min_take_profit_rr, min(sl * asset.max_take_profit_rr, tp))
    if action == "HOLD":
        sl = 0.0
        tp = 0.0
        confidence = 0.0
    precision = 2 if asset.distance_unit == "usd" else 1
    return {
        "action": action,
        "confidence": confidence,
        "reasoning": reasoning,
        "stop_loss_distance": round(sl, precision),
        "take_profit_distance": round(tp, precision),
    }


def _hold_decision(asset: AssetProfile, reason: str) -> dict[str, Any]:
    return {
        "action": "HOLD",
        "confidence": 0.0,
        "reasoning": str(reason)[:500],
        "stop_loss_distance": 0.0,
        "take_profit_distance": 0.0,
    }


def _candidate_summary(*, stage_outputs: dict[str, dict[str, Any]], final_decision: dict[str, Any]) -> str:
    analyst = stage_outputs.get("market_analyst") or {}
    risk = stage_outputs.get("risk_reviewer") or {}
    return (
        f"Candidate {final_decision.get('action')} at confidence {final_decision.get('confidence')}: "
        f"{analyst.get('summary', 'market analysis complete')} "
        f"Risk review: {risk.get('decision', 'APPROVE')}."
    )[:500]


def _model_for_stage(config: SignalConfig, stage: str) -> str:
    if stage in {"market_analyst", "bull_case", "bear_case"}:
        return config.debate_analyst_model
    return config.parser_llm_model


def _max_tokens_for_stage(stage: str) -> int:
    if stage in {"trader_proposal", "portfolio_decision"}:
        return 300
    return 240


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
    response_schema: dict[str, Any] | None,
) -> tuple[Any | None, dict[str, Any] | None, str, int]:
    attempts: list[tuple[str, dict[str, Any]]] = []
    if response_schema is not None and _supports_strict_json_schema(model):
        attempts.append(
            (
                "json_schema",
                _model_completion_options(
                    model,
                    max_tokens=max_tokens,
                    base_url=base_url,
                    response_schema=response_schema,
                ),
            )
        )
    attempts.append(
        (
            "json_object",
            _model_completion_options(
                model,
                max_tokens=max_tokens,
                base_url=base_url,
                json_object=True,
            ),
        )
    )
    last_response: Any | None = None
    last_mode = attempts[-1][0]
    for idx, (mode, options) in enumerate(attempts, start=1):
        last_mode = mode
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            **request_temperature_kwargs(model, temperature),
            **options,
        )
        last_response = response
        parsed = _parse_json_response(response)
        if parsed is not None:
            return response, parsed, mode, idx
    return last_response, None, last_mode, len(attempts)


def _parse_json_response(response: Any) -> dict[str, Any] | None:
    raw = (response.choices[0].message.content or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_usage(response: Any, *, provider: str, model: str, stage: str) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {
            "provider": provider,
            "model": model,
            "stage": stage,
            "usage_available": False,
        }
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    total_tokens = int(getattr(usage, "total_tokens", prompt_tokens + completion_tokens) or 0)
    return {
        "provider": provider,
        "model": model,
        "stage": stage,
        "usage_available": True,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "estimated_cost_usd": _estimate_cost_usd(model, prompt_tokens, completion_tokens),
    }


def _estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    return estimate_cost_usd(model, prompt_tokens, completion_tokens)


def _string_list(value: Any, *, max_items: int, max_len: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:max_len] for item in value if str(item).strip()][:max_items]


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)
