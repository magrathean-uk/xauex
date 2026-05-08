"""
Health check HTTP endpoint for XAUEX.

Provides a lightweight GET /health endpoint that returns JSON status.
Designed to integrate with systemd's sd_notify / external monitoring.

Requires: aiohttp (already in requirements.txt)

Usage (from BotOrchestrator.startup):
    health = HealthCheck(orchestrator, port=config.health_check_port)
    asyncio.create_task(health.run())

Response format:
    {
        "status": "ok" | "degraded" | "disconnected",
        "bot_status": "RUNNING" | "HALTED_..." | ...,
        "uptime_seconds": 3600,
        "open_positions": 1,
        "last_tick_age_seconds": 5,
        "reconnect_count": 0,
        "timestamp_utc": "2026-03-10T18:00:00Z"
    }

HTTP status codes:
    200 — connected and running
    503 — disconnected or internal error
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from aiohttp import web

if TYPE_CHECKING:
    from main import BotOrchestrator

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 8051
_DEFAULT_HOST = "127.0.0.1"


class HealthCheck:
    """Runs a tiny aiohttp server for health / liveness probing."""

    def __init__(
        self,
        orchestrator: "BotOrchestrator",
        host: str = _DEFAULT_HOST,
        port: int = _DEFAULT_PORT,
        watchdog=None,
    ):
        self.orchestrator = orchestrator
        self.host         = host
        self.port         = port
        self.watchdog     = watchdog
        self._start_time  = time.monotonic()
        self._last_tick_time: Optional[float] = None
        self._runner      = None

    def record_tick(self) -> None:
        """Record receipt of a market tick for liveness reporting."""
        self._last_tick_time = time.monotonic()

    async def run(self) -> None:
        """Start the aiohttp server. Runs until cancelled."""
        app = web.Application()
        app.router.add_get("/health", self._handle_health)
        app.router.add_get("/state", self._handle_state)
        app.router.add_get("/", self._handle_health)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        logger.info("[HEALTH] Listening on http://%s:%d/health", self.host, self.port)

        # Keep running until cancelled
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass
        finally:
            await self._runner.cleanup()

    async def _handle_health(self, request) -> "web.Response":
        now_mono = time.monotonic()
        uptime   = int(now_mono - self._start_time)
        last_tick_age = None
        if self._last_tick_time is not None:
            last_tick_age = int(now_mono - self._last_tick_time)

        api = self.orchestrator.api_client
        connected = api.is_connected() if api else False
        pos_count = (
            self.orchestrator.executor.position_manager.count()
            if self.orchestrator.executor else 0
        )
        tradeable, _ = self.orchestrator.session_filter.is_tradeable(datetime.now(timezone.utc))

        strategy_ready = True
        strategy_reason = None
        strategy_health = getattr(self.orchestrator, "current_strategy_health", None)
        if callable(strategy_health):
            try:
                result = strategy_health(datetime.now(timezone.utc))
            except TypeError:
                result = strategy_health()
            if isinstance(result, tuple) and len(result) == 2:
                strategy_ready, strategy_reason = result

        # Classify status
        if not connected:
            status = "disconnected"
        elif last_tick_age is not None and last_tick_age > 60:
            if pos_count == 0 and not tradeable:
                status = "ok"
            else:
                status = "degraded"   # connected but no ticks for > 60s
        elif pos_count == 0 and tradeable and not strategy_ready:
            status = "degraded"
        else:
            status = "ok"

        body = {
            "status":               status,
            "bot_status":           self.orchestrator.bot_status,
            "uptime_seconds":       uptime,
            "open_positions":       pos_count,
            "last_tick_age_seconds": last_tick_age,
            "reconnect_count":      self.watchdog.reconnect_count if self.watchdog else 0,
            "strategy_ready":       strategy_ready,
            "strategy_reason":      strategy_reason,
            "timestamp_utc":        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        http_status = 200 if status == "ok" else 503
        return web.json_response(body, status=http_status)

    async def _handle_state(self, request) -> "web.Response":
        state_path = self.orchestrator.config.state_file_path
        try:
            state = await asyncio.to_thread(self._load_state_file, state_path)
        except FileNotFoundError:
            return web.json_response(
                {
                    "error": "state file unavailable",
                    "path": state_path,
                    "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                status=503,
            )
        except json.JSONDecodeError as exc:
            return web.json_response(
                {
                    "error": f"state file invalid: {exc}",
                    "path": state_path,
                    "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                status=503,
            )
        except OSError as exc:
            return web.json_response(
                {
                    "error": f"state file read failed: {exc}",
                    "path": state_path,
                    "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                status=503,
            )

        return web.json_response(state, status=200)

    @staticmethod
    def _load_state_file(path: str) -> dict:
        with open(path, "r") as f:
            return json.load(f)
