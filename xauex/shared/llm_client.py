"""Minimal OpenAI-compatible chat client for provider endpoints like Groq."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx


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
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
        self.chat = _ChatNamespace(self)

    def _request(self, *, path: str, payload: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        response = self._http.post(
            f"{self._base_url}{path}",
            json=payload,
            timeout=timeout,
        )
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
