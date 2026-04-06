"""
Reconnection watchdog for the BotOrchestrator.

Monitors the cTrader API connection and automatically reconnects with
exponential backoff. After each successful reconnect it triggers a
reconcile() call to re-sync position state.

Usage:
    watchdog = Watchdog(orchestrator, max_backoff=300)
    asyncio.create_task(watchdog.run())
"""

import time
import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from bot.api.reconciler import reconcile_positions

if TYPE_CHECKING:
    from main import BotOrchestrator

logger = logging.getLogger(__name__)

_INITIAL_BACKOFF = 5.0      # seconds before first retry
_BACKOFF_FACTOR  = 2.0      # multiply delay on each failure
_MAX_BACKOFF     = 300.0    # cap at 5 minutes
_CHECK_INTERVAL  = 10.0     # seconds between connection health checks
_STALE_TICK_THRESHOLD = 180.0 # seconds (3 mins) without ticks triggers reconnect


class Watchdog:
    """
    Monitors ApiClient.is_connected() every _CHECK_INTERVAL seconds.
    On disconnect: reconnects with exponential backoff, then reconciles.
    """

    def __init__(self, orchestrator: "BotOrchestrator", max_backoff: float = _MAX_BACKOFF):
        self.orchestrator  = orchestrator
        self.max_backoff   = max_backoff
        self._running      = False
        self._reconnects   = 0

    async def _sleep(self, seconds: float) -> None:
        """Isolated sleep — override in tests to avoid blocking."""
        await asyncio.sleep(seconds)

    async def run(self) -> None:
        """Main watchdog loop. Runs until orchestrator.running is False."""
        self._running = True
        logger.info("[WATCHDOG] Started.")

        while self.orchestrator.running:
            await self._sleep(_CHECK_INTERVAL)

            api = self.orchestrator.api_client
            if api is None or not self.orchestrator.running:
                continue

            if api.is_connected():
                # Check for stale ticks (zombie connection)
                tick_age = time.monotonic() - self.orchestrator.last_tick_time
                if tick_age > _STALE_TICK_THRESHOLD:
                    if self._should_ignore_stale_ticks():
                        continue
                    logger.warning("[WATCHDOG] Zombie connection detected! No ticks for %.1fs. Forcing reconnect.", tick_age)
                    await self._reconnect_with_backoff()
                continue

            logger.warning("[WATCHDOG] Connection lost — starting reconnect sequence.")
            await self._reconnect_with_backoff()

        self._running = False
        logger.info("[WATCHDOG] Stopped.")

    def _should_ignore_stale_ticks(self) -> bool:
        """Avoid reconnect churn when the bot is outside session and flat."""
        try:
            now_utc = datetime.now(timezone.utc)
            session_ok, _ = self.orchestrator.session_filter.is_tradeable(now_utc)
            open_positions = (
                self.orchestrator.executor.position_manager.count()
                if self.orchestrator.executor else 0
            )
            return (not session_ok) and open_positions == 0
        except Exception:
            return False

    async def _reconnect_with_backoff(self) -> None:
        """Retry connect() with exponential backoff until success."""
        backoff = _INITIAL_BACKOFF
        attempt = 0

        while self.orchestrator.running:
            attempt += 1
            logger.info("[WATCHDOG] Reconnect attempt %d (backoff %.0fs).", attempt, backoff)

            try:
                api = self.orchestrator.api_client
                try:
                    await api.disconnect()
                except Exception:
                    pass

                await api.connect()
                self._reconnects += 1
                logger.info(
                    "[WATCHDOG] Reconnected successfully (attempt %d, total reconnects: %d).",
                    attempt, self._reconnects,
                )

                try:
                    await api.subscribe_ticks("XAUUSD", self.orchestrator.on_tick)
                except Exception as exc:
                    logger.error("[WATCHDOG] Failed to re-subscribe ticks: %s", exc)

                try:
                    await reconcile_positions(api, self.orchestrator.executor.position_manager)
                    logger.info("[WATCHDOG] Position reconciliation complete.")
                except Exception as exc:
                    logger.error("[WATCHDOG] Reconcile after reconnect failed: %s", exc)

                self.orchestrator.set_status(
                    "OBSERVE_ONLY" if self.orchestrator.config.observe_only else "RUNNING"
                )
                await self.orchestrator.write_state()
                return

            except Exception as exc:
                logger.error("[WATCHDOG] Reconnect attempt %d failed: %s", attempt, exc)
                self.orchestrator.set_status("RECONNECTING")
                try:
                    await self.orchestrator.write_state()
                except Exception:
                    pass

                await self._sleep(backoff)
                backoff = min(backoff * _BACKOFF_FACTOR, self.max_backoff)

    @property
    def reconnect_count(self) -> int:
        return self._reconnects
