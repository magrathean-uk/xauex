"""Model metadata and request-shaping helpers for XAUEX LLM calls."""

from __future__ import annotations

from typing import Any


MODEL_PRICES_USD_PER_MILLION: dict[str, tuple[float, float]] = {
    "llama-3.1-8b-instant": (0.05, 0.08),
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "openai/gpt-oss-20b": (0.075, 0.30),
    "openai/gpt-oss-120b": (0.15, 0.60),
    "anthropic/claude-opus-4.7": (5.00, 25.00),
    "anthropic/claude-opus-4.7-fast": (30.00, 150.00),
    "anthropic/claude-sonnet-4.6": (3.00, 15.00),
    "openai/gpt-5.5": (5.00, 30.00),
    "openai/gpt-5.4": (2.50, 15.00),
    "openai/gpt-5.4-mini": (0.75, 4.50),
    "openai/gpt-5.4-nano": (0.20, 1.25),
    "google/gemini-3.1-pro-preview": (2.00, 12.00),
    "google/gemini-3.1-flash-lite": (0.25, 1.50),
    "moonshotai/kimi-k2.6": (0.73, 3.49),
    "qwen/qwen3.6-max-preview": (1.04, 6.24),
    "deepseek/deepseek-v4-flash": (0.112, 0.224),
    "z-ai/glm-5.1": (0.98, 3.08),
}

STRICT_JSON_SCHEMA_MODELS: set[str] = {
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
    "anthropic/claude-sonnet-4.6",
    "openai/gpt-5.5",
    "openai/gpt-5.4",
    "openai/gpt-5.4-mini",
    "openai/gpt-5.4-nano",
    "google/gemini-3.1-pro-preview",
    "google/gemini-3.1-flash-lite",
    "moonshotai/kimi-k2.6",
    "qwen/qwen3.6-max-preview",
    "deepseek/deepseek-v4-flash",
    "z-ai/glm-5.1",
}

NO_TEMPERATURE_MODELS: set[str] = {
    "anthropic/claude-opus-4.7",
    "anthropic/claude-opus-4.7-fast",
    "openai/gpt-5.5",
    "openai/gpt-5.4",
    "openai/gpt-5.4-mini",
    "openai/gpt-5.4-nano",
}


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    prices = MODEL_PRICES_USD_PER_MILLION.get(model) or MODEL_PRICES_USD_PER_MILLION.get(_price_key(model))
    if prices is None:
        return None
    input_price, output_price = prices
    return round((prompt_tokens / 1_000_000 * input_price) + (completion_tokens / 1_000_000 * output_price), 8)


def supports_strict_json_schema(model: str) -> bool:
    return model in STRICT_JSON_SCHEMA_MODELS


def supports_temperature(model: str) -> bool:
    return model not in NO_TEMPERATURE_MODELS


def completion_options(
    model: str,
    *,
    max_tokens: int,
    base_url: str | None = None,
    response_schema: dict[str, Any] | None = None,
    json_object: bool = False,
) -> dict[str, Any]:
    visible_tokens = max(max_tokens, 700) if model.startswith("openai/gpt-oss-") else max_tokens
    options: dict[str, Any] = {_token_limit_parameter(model): visible_tokens}

    if model.startswith("openai/gpt-oss-"):
        options["reasoning_effort"] = "low"
        options["extra_body"] = {"include_reasoning": False}
    elif model.startswith("openai/gpt-5."):
        options["reasoning"] = {"effort": "minimal", "exclude": True}

    if response_schema is not None:
        options["response_format"] = {
            "type": "json_schema",
            "json_schema": response_schema,
        }
    elif json_object:
        options["response_format"] = {"type": "json_object"}

    if _is_openrouter_url(base_url) and "response_format" in options:
        options["provider"] = {"require_parameters": True}

    return options


def request_temperature_kwargs(model: str, temperature: float) -> dict[str, float]:
    if supports_temperature(model):
        return {"temperature": temperature}
    return {}


def _token_limit_parameter(model: str) -> str:
    if model.startswith(("anthropic/", "deepseek/", "google/", "moonshotai/", "qwen/", "z-ai/")):
        return "max_tokens"
    return "max_completion_tokens"


def _is_openrouter_url(base_url: str | None) -> bool:
    return "openrouter.ai" in str(base_url or "").lower()


def _price_key(model: str) -> str:
    if model.startswith("google/gemini-3.1-flash-lite-"):
        return "google/gemini-3.1-flash-lite"
    if model.startswith("google/gemini-3.1-pro-preview-"):
        return "google/gemini-3.1-pro-preview"
    return model
