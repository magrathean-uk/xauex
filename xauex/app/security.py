"""Dashboard security headers and local manual endpoint throttling."""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque

from flask import Flask, Request, Response, jsonify, request


@dataclass(frozen=True)
class SecurityHeadersConfig:
    csp: str = (
        "default-src 'self'; "
        "img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )
    json_cache_control: str = "no-store"
    page_cache_control: str = "private, max-age=15"
    max_json_body_bytes: int = 16 * 1024


class ProcessLocalRateLimiter:
    """Simple fixed-window limiter for a single-host dashboard."""

    def __init__(self, *, limit: int = 12, window_seconds: int = 60):
        self.limit = max(1, int(limit))
        self.window_seconds = max(1, int(window_seconds))
        self._events: dict[str, Deque[float]] = defaultdict(deque)

    def allow(self, key: str, *, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        bucket = self._events[key]
        cutoff = now - self.window_seconds
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= self.limit:
            return False
        bucket.append(now)
        return True

    def clear(self) -> None:
        self._events.clear()


def _request_key(req: Request) -> str:
    auth = req.headers.get("Authorization", "")
    digest = hashlib.sha256(auth.encode("utf-8")).hexdigest()[:16] if auth else "noauth"
    return f"{req.remote_addr or 'local'}:{digest}"


def install_dashboard_security(
    app: Flask,
    *,
    config: SecurityHeadersConfig | None = None,
    limiter: ProcessLocalRateLimiter | None = None,
) -> None:
    cfg = config or SecurityHeadersConfig()
    manual_limiter = limiter or ProcessLocalRateLimiter()

    @app.before_request
    def _limit_manual_payloads():
        if request.path in {"/api/manual-trade", "/api/manual-close"}:
            if request.content_length is not None and request.content_length > cfg.max_json_body_bytes:
                return jsonify({"success": False, "error": "Request body too large", "reply": "Request body too large."}), 413
            if not manual_limiter.allow(_request_key(request)):
                return jsonify({"success": False, "error": "Rate limit exceeded", "reply": "Manual controls are temporarily throttled."}), 429
        return None

    @app.after_request
    def _security_headers(response: Response) -> Response:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Content-Security-Policy", cfg.csp)
        response.headers.setdefault("X-Frame-Options", "DENY")
        if request.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", cfg.json_cache_control)
        else:
            response.headers.setdefault("Cache-Control", cfg.page_cache_control)
        return response
