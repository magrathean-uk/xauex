"""Minimal OpenAI-compatible chat client for provider endpoints like Groq."""

from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace
from typing import Any

import httpx

try:  # pragma: no cover - exercised via monkeypatch when dependency is absent
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from google.oauth2 import service_account
except ImportError:  # pragma: no cover - host dependency/configuration dependent
    GoogleAuthRequest = None
    service_account = None


GOOGLE_SERVICE_ACCOUNT_SENTINEL = "google-service-account"
GOOGLE_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
MAX_REQUEST_ATTEMPTS = 4
MAX_RETRY_DELAY_SECONDS = 30.0


def create_chat_client(*, api_key: str, base_url: str, timeout: float = 120.0) -> Any:
    return _OpenAICompatibleClient(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
    )


class _OpenAICompatibleClient:
    def __init__(self, *, api_key: str, base_url: str, timeout: float) -> None:
        self._base_url = str(base_url or "").rstrip("/")
        if not self._base_url:
            raise ValueError("base_url is required")
        self._http = httpx.Client(
            headers={
                "Authorization": _authorization_header(api_key),
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
        self.chat = _ChatNamespace(self)

    def _request(self, *, path: str, payload: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        response: httpx.Response | Any | None = None
        for attempt in range(MAX_REQUEST_ATTEMPTS):
            try:
                response = self._http.post(
                    f"{self._base_url}{path}",
                    json=payload,
                    timeout=timeout,
                )
            except httpx.TransportError as exc:
                if attempt == MAX_REQUEST_ATTEMPTS - 1:
                    raise RuntimeError(f"LLM request transport error: {exc}") from exc
                time.sleep(_retry_delay(attempt=attempt))
                continue

            if _is_retryable_status(response.status_code) and attempt < MAX_REQUEST_ATTEMPTS - 1:
                time.sleep(_retry_delay(attempt=attempt, response=response))
                continue
            break

        if response is None:  # pragma: no cover - loop always returns or raises
            raise RuntimeError("LLM request failed without a response")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = _error_detail(response)
            raise RuntimeError(detail or f"LLM request failed with status {response.status_code}") from exc
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("LLM response was not a JSON object")
        return data

    def close(self) -> None:
        self._http.close()


class _ChatNamespace:
    def __init__(self, client: _OpenAICompatibleClient) -> None:
        self.completions = _CompletionsNamespace(client)


class _CompletionsNamespace:
    def __init__(self, client: _OpenAICompatibleClient) -> None:
        self._client = client

    def create(self, **kwargs: Any) -> Any:
        request_timeout = kwargs.pop("timeout", None)
        extra_body = kwargs.pop("extra_body", None)
        payload = {key: value for key, value in kwargs.items() if value is not None}
        if isinstance(extra_body, dict):
            payload.update({key: value for key, value in extra_body.items() if value is not None})
        raw = self._client._request(
            path="/chat/completions",
            payload=payload,
            timeout=request_timeout,
        )
        return _build_completion_response(raw)


def _build_completion_response(payload: dict[str, Any]) -> Any:
    usage_payload = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    usage = SimpleNamespace(
        prompt_tokens=int(usage_payload.get("prompt_tokens", 0) or 0),
        completion_tokens=int(usage_payload.get("completion_tokens", 0) or 0),
        total_tokens=int(usage_payload.get("total_tokens", 0) or 0),
    )
    choices = []
    for choice in payload.get("choices", []) or []:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        choices.append(
            SimpleNamespace(
                message=SimpleNamespace(content=message.get("content")),
            )
        )
    return SimpleNamespace(
        choices=choices,
        usage=usage,
        model=str(payload.get("model") or ""),
        raw=payload,
    )


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return response.text.strip()
    if not isinstance(payload, dict):
        return response.text.strip()
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or response.text).strip()
    return response.text.strip()


def _is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def _retry_delay(*, attempt: int, response: httpx.Response | Any | None = None) -> float:
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after is not None:
            try:
                parsed = float(retry_after)
            except (TypeError, ValueError):
                pass
            else:
                if parsed >= 0:
                    return min(parsed, MAX_RETRY_DELAY_SECONDS)
    return min(float(2**attempt), MAX_RETRY_DELAY_SECONDS)


def _authorization_header(api_key: str) -> str:
    if _is_google_service_account_key(api_key):
        return f"Bearer {_google_service_account_token(api_key)}"
    return f"Bearer {api_key}"


def _is_google_service_account_key(api_key: str) -> bool:
    raw = str(api_key or "").strip()
    return raw == GOOGLE_SERVICE_ACCOUNT_SENTINEL or raw.startswith(f"{GOOGLE_SERVICE_ACCOUNT_SENTINEL}:")


def _google_service_account_token(api_key: str) -> str:
    if service_account is None or GoogleAuthRequest is None:
        raise RuntimeError(
            "google-auth is required for google-service-account LLM auth. "
            "Install requirements.txt in the active virtualenv."
        )
    credential_path = _google_service_account_path(api_key)
    credentials = service_account.Credentials.from_service_account_file(
        credential_path,
        scopes=[GOOGLE_CLOUD_PLATFORM_SCOPE],
    )
    credentials.refresh(GoogleAuthRequest())
    token = str(getattr(credentials, "token", "") or "").strip()
    if not token:
        raise RuntimeError("Google service-account auth did not return an access token")
    return token


def _google_service_account_path(api_key: str) -> str:
    raw = str(api_key or "").strip()
    prefix = f"{GOOGLE_SERVICE_ACCOUNT_SENTINEL}:"
    if raw.startswith(prefix) and raw[len(prefix):].strip():
        return raw[len(prefix):].strip()
    credential_path = (
        os.getenv("XAUEX_GOOGLE_APPLICATION_CREDENTIALS")
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        or ""
    ).strip()
    if not credential_path:
        raise RuntimeError(
            "Set GOOGLE_APPLICATION_CREDENTIALS or XAUEX_GOOGLE_APPLICATION_CREDENTIALS "
            "when using google-service-account LLM auth."
        )
    return credential_path
