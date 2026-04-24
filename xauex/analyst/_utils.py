"""Shared utilities for analyst scripts."""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import dotenv_values

from xauex.shared.llm_client import create_chat_client

XAUEX_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = XAUEX_ROOT.parent

logger = logging.getLogger(__name__)

OpenAI = None

STATE_FILE = os.environ.get("XAUEX_STATE_FILE", os.environ.get("STATE_FILE_PATH", "/var/lib/xauex/state.json"))
LOG_FILE = os.environ.get("XAUEX_ANALYST_LOG", "/var/log/xauex/analyst.log")


def _merged_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in (REPO_ROOT / ".env", XAUEX_ROOT / ".env"):
        if path.exists():
            for key, value in dotenv_values(path).items():
                if key and value is not None:
                    env[key] = value
    env.update({key: value for key, value in os.environ.items() if value})
    return env


def _first_env(*keys: str, default: str = "") -> str:
    env = _merged_env()
    for key in keys:
        value = str(env.get(key, "")).strip()
        if value:
            return value
    return default


def default_model() -> str:
    return _first_env(
        "XAUEX_ANALYST_MODEL",
        "XAUEX_SIGNAL_LLM_MODEL",
        "LLM_MODEL_NAME",
        "XAUEX_SIGNAL_PARSER_LLM_MODEL",
        default="llama-3.1-8b-instant",
    )


def _resolve_llm_settings(model: Optional[str] = None) -> tuple[str, str, str]:
    api_key = _first_env(
        "XAUEX_ANALYST_API_KEY",
        "XAUEX_SIGNAL_LLM_API_KEY",
        "LLM_API_KEY",
        "XAUEX_SIGNAL_PARSER_LLM_API_KEY",
    )
    if not api_key:
        raise RuntimeError(
            "No analyst API key configured. Set XAUEX_ANALYST_API_KEY or reuse the repo LLM API settings."
        )

    base_url = _first_env(
        "XAUEX_ANALYST_BASE_URL",
        "XAUEX_SIGNAL_LLM_BASE_URL",
        "LLM_BASE_URL",
        "XAUEX_SIGNAL_PARSER_LLM_BASE_URL",
        default="https://api.groq.com/openai/v1",
    )
    resolved_model = (model or "").strip() or default_model()
    return api_key, base_url, resolved_model


def read_json_file(path: str) -> Optional[dict]:
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        logger.warning("JSON parse error in %s: %s", path, e)
        return None


def is_state_stale(state: Optional[dict], max_age_seconds: int) -> bool:
    if state is None:
        return True
    try:
        ts_str = state["meta"]["last_updated_utc"]
        ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        return age > max_age_seconds
    except (KeyError, ValueError):
        return True


def atomic_write_json(path: str, data: Any) -> None:
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_to_json_list(path: str, entry: Any) -> None:
    existing = read_json_file(path)
    if not isinstance(existing, list):
        existing = []
    existing.append(entry)
    atomic_write_json(path, existing)


def load_cursor(path: str, default: Any) -> Any:
    data = read_json_file(path)
    if data is None:
        logger.debug("Cursor not found at %s, using default.", path)
        return default
    return data


def save_cursor(path: str, data: Any) -> None:
    atomic_write_json(path, data)


def call_llm(prompt: str, model: Optional[str] = None, timeout: int = 120) -> str:
    api_key, base_url, resolved_model = _resolve_llm_settings(model)
    if OpenAI is not None:
        client = OpenAI(api_key=api_key, base_url=base_url)
    else:
        client = create_chat_client(api_key=api_key, base_url=base_url, timeout=float(timeout))
    logger.info("[ANALYST] Calling model=%s via %s", resolved_model, base_url)
    response = client.chat.completions.create(
        model=resolved_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an analyst helper for an automated trading system. "
                    "Respond directly and do not wrap the response in code fences unless explicitly asked."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=900,
        timeout=timeout,
    )
    content = (response.choices[0].message.content or "").strip()
    if not content:
        raise RuntimeError(f"Analyst API returned empty content for model {resolved_model}")
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return content


call_claude = call_llm


def utcnow_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
