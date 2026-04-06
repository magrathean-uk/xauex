"""Shared utilities for analyst scripts."""

import json
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

STATE_FILE = os.environ.get("XAUEX_STATE_FILE", os.environ.get("STATE_FILE_PATH", "/var/lib/xauex/state.json"))
LOG_FILE = os.environ.get("XAUEX_ANALYST_LOG", "/var/log/xauex/analyst.log")
_LLM_FALLBACKS = (
    os.environ.get("GEMINI_BIN", ""),
    "/usr/bin/gemini",
    "/usr/local/bin/gemini",
)
DEFAULT_MODEL = os.environ.get("XAUEX_ANALYST_MODEL", "gemini-3.1-pro-preview")


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


def find_llm_cli() -> Optional[str]:
    path = shutil.which("gemini")
    if path:
        return path
    for candidate in _LLM_FALLBACKS:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def call_llm(prompt: str, model: str = DEFAULT_MODEL, timeout: int = 120) -> str:
    binary = find_llm_cli()
    if binary is None:
        raise RuntimeError(
            "gemini binary not found on PATH. Ensure Gemini CLI is installed and accessible to the cron user."
        )
    try:
        result = subprocess.run(
            [binary, "-p", prompt, "-m", model],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"gemini timed out after {timeout}s")
    if result.returncode != 0:
        raise RuntimeError(
            f"gemini exited with code {result.returncode}: {result.stderr.strip()}"
        )
    return result.stdout.strip()


call_claude = call_llm


def utcnow_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
