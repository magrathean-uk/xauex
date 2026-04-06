"""
XAUEX Trading Bot Main Entry Point

Starts the bot daemon with asyncio event loop.
Handles startup sequence, main loop, and graceful shutdown.

Entry point: asyncio.run(main()) drives the pure-asyncio event loop.
No Twisted dependency — transport is implemented in bot/api/proto_transport.py.
"""

import asyncio
import json
import time
import logging
import logging.handlers
import os
import queue
import signal
import sys
from collections import deque
from datetime import datetime, timezone
from typing import Optional, List, Dict, Tuple

from config import Config, load_config
from bot.api.client import ApiClient
from bot.levels.htf_levels import LevelManager
from bot.patterns.detector import PatternDetector, CandleWatcher, PatternType
from bot.filters.macro_regime import MacroRegime, MacroRegimeLoader
from bot.filters.trade_policy import TradePolicy, TradePolicyLoader
from bot.filters.session import SessionFilter
from bot.filters.news import NewsFilter
from bot.filters.trend import TrendFilter
from bot.risk.sizing import calculate_lot_size
from bot.risk.gates import RiskGates, RiskState
from bot.execution.executor import Executor, TrackedPosition
from bot.strategies.ema_pullback_h1 import EMAPullbackH1Strategy
from bot.strategies.scalp_v1 import M5ScalpStrategy
from bot.state.writer import StateWriter
from bot.state.risk_persistence import save_risk_state, load_risk_state
from bot.watchdog import Watchdog
from bot.health import HealthCheck

logger = logging.getLogger(__name__)

_EXECUTION_BAR_LOOKBACK = 500
_DAILY_BAR_LOOKBACK = 120
_SCALP_BAR_LOOKBACK = 2500
_EMA_PULLBACK_BAR_LOOKBACK = 260


class BotOrchestrator:
    """
    Coordinates all modules: startup, main event loop, graceful shutdown.
    The tick handler drives all logic; no internal timers or threads.
    """

    def __init__(self, config: Config):
        self.config = config
        self.running = False
        self.active_strategy_mode = config.strategy_mode
        self.shadow_strategy_mode = (
            config.shadow_strategy_mode if config.shadow_strategy_mode != "NONE" else None
        )
        self.execution_timeframe = self._strategy_timeframe(self.active_strategy_mode)

        self.api_client: Optional[ApiClient] = None
        self.level_manager: Optional[LevelManager] = None
        self._watchers: Dict[str, CandleWatcher] = {}
        self._watchers[self.execution_timeframe] = CandleWatcher(self.execution_timeframe)
        if self.shadow_strategy_mode:
            shadow_tf = self._strategy_timeframe(self.shadow_strategy_mode)
            self._watchers.setdefault(shadow_tf, CandleWatcher(shadow_tf))
        self.pattern_detector: Optional[PatternDetector] = None
        self.session_filter = SessionFilter()
        self.news_filter: Optional[NewsFilter] = None
        self.trend_filter = TrendFilter()
        self.ema_pullback_strategy = EMAPullbackH1Strategy(config, self.trend_filter)
        self.scalp_strategy = M5ScalpStrategy(config)
        self.macro_regime_loader = MacroRegimeLoader(
            config.macro_regime_path,
            config.macro_regime_max_age_minutes,
        )
        self.trade_policy_loader = TradePolicyLoader(
            config.trade_policy_path,
            config.trade_policy_max_age_minutes,
        )
        self._macro_regime: Optional[MacroRegime] = None
        self._trade_policy: Optional[TradePolicy] = None
        self.risk_state = RiskState()
        self.risk_gates: Optional[RiskGates] = None
        self.executor: Optional[Executor] = None
        self.state_writer: Optional[StateWriter] = None
        self.watchdog: Optional[Watchdog] = None
        self.health_check: Optional[HealthCheck] = None

        self.symbol_spec = None
        self.account: Dict = {}
        self.bot_status = "INITIALIZING"
        self.last_error: Optional[str] = None
        self.kill_switch_active = False
        self._last_mirofish_signal_id: Optional[str] = None
        self.last_tick_time = time.monotonic()

        self._recent_h1_closes: deque = deque(maxlen=20)
        self._trade_entries_on_chart: List[Dict] = []
        self._last_signal: Optional[Dict] = None
        self._signal_history: deque = deque(maxlen=12)
        self._trend_snapshot: Optional[Dict] = None
        self._candle_index = 0
        self._recent_trade_levels: deque = deque(maxlen=20)
        self._shadow_last_signal: Optional[Dict] = None
        self._shadow_signal_history: deque = deque(maxlen=12)
        self._strategy_entries: Dict[str, deque] = {}
        self._strategy_trade_counts: Dict[str, int] = {}
        self._strategy_trade_dates: Dict[str, str] = {}
        self._strategy_data_status: Dict[str, Dict] = {}
        self._timeframe_candle_indices: Dict[str, int] = {timeframe: 0 for timeframe in self._watchers}
        for mode in filter(None, {self.active_strategy_mode, self.shadow_strategy_mode}):
            self._strategy_entries[mode] = deque(maxlen=20)
            self._strategy_trade_counts[mode] = 0
            self._strategy_trade_dates[mode] = self._today_utc()

    # ──────────────────────────────────────────────────────────────
    # Startup
    # ──────────────────────────────────────────────────────────────

    async def startup(self) -> None:
        """Run startup sequence. Exits process on any failure in steps 1-10."""
        logger.info("[STARTUP] XAUEX starting up.")

        # Step 1: Config already loaded and validated by caller.

        # Step 2: Token refresh check
        try:
            await self._check_token_refresh()
        except Exception as exc:
            logger.critical("[STARTUP] Token refresh failed: %s", exc)
            sys.exit(1)

        # Step 3+4: Connect and authenticate
        try:
            self.api_client = ApiClient(self.config)
            await self.api_client.connect()
        except Exception as exc:
            logger.critical("[STARTUP] API connection failed: %s", exc)
            sys.exit(1)

        # Step 5: Fetch account state
        try:
            account_data = await self.api_client.get_account()
            self.account = {
                "balance": account_data.balance,
                "equity": account_data.equity,
                "currency": account_data.currency,
                "open_pnl": account_data.open_pnl,
            }
            logger.info("[STARTUP] Account: %s %.2f", account_data.currency, account_data.balance)
        except Exception as exc:
            logger.critical("[STARTUP] Failed to fetch account: %s", exc)
            sys.exit(1)

        # Step 6: Symbol spec
        try:
            self.symbol_spec = await self.api_client.get_symbol_spec("XAUUSD")
            logger.info("[STARTUP] Symbol spec loaded: lot_size=%.0f", self.symbol_spec.lot_size)
        except Exception as exc:
            logger.critical("[STARTUP] Failed to fetch symbol spec: %s", exc)
            sys.exit(1)

        # Step 7+8: execution-timeframe bars and HTF levels
        try:
            self.level_manager = LevelManager(self.config, self.api_client)
            await self.level_manager.refresh()
            await self._prime_recent_context()
            logger.info("[STARTUP] HTF levels computed.")
        except Exception as exc:
            logger.critical("[STARTUP] Level manager init failed: %s", exc)
            sys.exit(1)

        # Initialize dependent modules
        self.pattern_detector = PatternDetector(self.config)
        self.news_filter = NewsFilter(self.config)
        self.risk_gates = RiskGates(self.config, self.risk_state)
        self.state_writer = StateWriter(self.config)
        self.executor = Executor(
            config=self.config,
            api_client=self.api_client,
            level_manager=self.level_manager,
            risk_gates=self.risk_gates,
            state_writer=self.state_writer,
        )
        self.api_client.set_execution_callback(self._on_execution_event)

        # Step 9: Restore risk gate state
        await self._restore_risk_state()
        self.risk_gates.ensure_period_baselines(self.account.get("balance", 0.0))

        # Step 10: Fetch economic calendar
        try:
            await self.news_filter.refresh_if_needed()
            if self.news_filter.feed_available:
                logger.info("[STARTUP] Economic calendar cached.")
            else:
                logger.warning("[STARTUP] News feed unavailable. Bot will stay up but block entries until refresh succeeds.")
        except Exception as exc:
            logger.warning("[STARTUP] Failed to refresh news calendar: %s", exc)

        self._macro_regime = self._load_macro_regime()
        self._trade_policy = self._load_trade_policy()

        # Step 11: Restore open positions from API
        try:
            open_positions = await self.api_client.get_open_positions()
            for pos in open_positions:
                self.executor.position_manager.add_position(pos)
                logger.info("[STARTUP] Restored position %s %s", pos.position_id, pos.direction)
            self.risk_gates.set_open_position_count(self.executor.position_manager.count())
        except Exception as exc:
            logger.critical("[STARTUP] Failed to restore positions: %s", exc)
            sys.exit(1)

        # Write initial state
        await self.write_state()
        logger.info("[STARTUP] Initial state written.")

        # Subscribe to ticks
        await self.api_client.subscribe_ticks("XAUUSD", self.on_tick)
        logger.info("[STARTUP] Tick stream subscribed.")

        self.running = True
        self.bot_status = "OBSERVE_ONLY" if self.config.observe_only else "RUNNING"

        # Background: poll kill switch, watchdog, health check
        asyncio.create_task(self._poll_kill_switch())

        if self.config.mirofish_mode:
            asyncio.create_task(self._poll_mirofish_signal())
            logger.info("[STARTUP] MiroFish mode: internal strategies DISABLED, polling for signals.")

        self.watchdog = Watchdog(self)
        asyncio.create_task(self.watchdog.run())

        self.health_check = HealthCheck(
            self,
            port=getattr(self.config, "health_check_port", 8051),
            watchdog=self.watchdog,
        )
        asyncio.create_task(self.health_check.run())
        asyncio.create_task(self._poll_account_snapshot())
        await self.write_state()
        logger.info("[STARTUP] Bot online. Status: %s", self.bot_status)

    # ──────────────────────────────────────────────────────────────
    # Main event loop
    # ──────────────────────────────────────────────────────────────

    async def main_loop(self) -> None:
        """Keep the event loop alive — tick callbacks drive all logic."""
        while self.running:
            await asyncio.sleep(1)

    # ──────────────────────────────────────────────────────────────
    # Tick handler
    # ──────────────────────────────────────────────────────────────

    async def on_tick(self, price: float, timestamp: datetime) -> None:
        """Process an incoming price tick."""
        self.last_tick_time = time.monotonic()
        if self.health_check:
            self.health_check.record_tick()

        # Update trailing stops on every tick (independent of candle close)
        if self.executor and self.symbol_spec and self.executor.position_manager.count() > 0:
            await self.executor.update_trailing_stops(
                current_price=price,
                account_balance=self.account.get("balance", 0.0),
                symbol_spec=self.symbol_spec,
            )

        closed_timeframes: set[str] = set()
        for timeframe, watcher in self._watchers.items():
            if watcher.on_tick(timestamp):
                closed_timeframes.add(timeframe)
                self._timeframe_candle_indices[timeframe] += 1

        if self.execution_timeframe in closed_timeframes:
            self._candle_index = self._timeframe_candle_indices[self.execution_timeframe]
            await self._process_candle_close(price, timestamp)

        if self.shadow_strategy_mode:
            shadow_tf = self._strategy_timeframe(self.shadow_strategy_mode)
            if shadow_tf in closed_timeframes:
                await self._process_shadow_strategy_close(price, timestamp)

    async def _process_candle_close(self, price: float, timestamp: datetime) -> None:
        """Handle all logic triggered by the active strategy candle closing."""
        if self.config.mirofish_mode:
            # In MiroFish mode, internal strategies are disabled.
            # Trades are placed by _poll_mirofish_signal instead.
            await self._finalize_candle()
            return
        if self.active_strategy_mode == "EMA_PULLBACK_H1":
            await self._process_ema_pullback_candle_close(
                price=price,
                timestamp=timestamp,
                store="live",
                apply_risk_gates=True,
            )
            return
        if self.active_strategy_mode == "SCALP_V1":
            await self._process_scalp_v1_candle_close(
                price=price,
                timestamp=timestamp,
                store="live",
                apply_risk_gates=True,
            )
            return
        await self._process_legacy_candle_close(price, timestamp)

    async def _process_shadow_strategy_close(self, price: float, timestamp: datetime) -> None:
        """Evaluate the configured shadow strategy without placing live orders."""
        if not self.shadow_strategy_mode or self.shadow_strategy_mode == self.active_strategy_mode:
            return
        if self.shadow_strategy_mode == "EMA_PULLBACK_H1":
            await self._process_ema_pullback_candle_close(
                price=price,
                timestamp=timestamp,
                store="shadow",
                apply_risk_gates=False,
            )
            return
        if self.shadow_strategy_mode == "SCALP_V1":
            await self._process_scalp_v1_candle_close(
                price=price,
                timestamp=timestamp,
                store="shadow",
                apply_risk_gates=False,
            )

    async def _process_legacy_candle_close(self, price: float, timestamp: datetime) -> None:
        """Run the existing level-reaction strategy on the configured execution timeframe."""
        logger.debug("[TICK] %s legacy candle closed at %s", self.execution_timeframe, timestamp)
        bars, signal_index = await self._fetch_execution_bars(
            self.execution_timeframe,
            count=self._strategy_execution_lookback(self.active_strategy_mode),
        )
        if bars is None or signal_index is None:
            self._update_strategy_data_status(
                self.active_strategy_mode,
                execution_timeframe=self.execution_timeframe,
                execution_bars=bars,
                signal_index=signal_index,
            )
            await self.write_state()
            return

        self._recent_h1_closes = deque([bar["close"] for bar in bars[-20:]], maxlen=20)

        try:
            await self.level_manager.refresh_if_needed()
        except Exception as exc:
            logger.error("[TICK] Level refresh error: %s", exc)

        gate_result = await self._environment_gate(apply_risk_gates=True)
        if gate_result is not None:
            self._record_signal(None, None, gate_result, "SKIP", strategy_mode=self.active_strategy_mode)
            await self._finalize_candle()
            return

        prev_bar = bars[signal_index - 1]
        signal_bar = bars[signal_index]
        from bot.patterns.detector import Candle

        prev_candle = Candle(
            open=prev_bar["open"], high=prev_bar["high"],
            low=prev_bar["low"], close=prev_bar["close"],
            open_time=prev_bar["open_time"],
        )
        signal_candle = Candle(
            open=signal_bar["open"], high=signal_bar["high"],
            low=signal_bar["low"], close=signal_bar["close"],
            open_time=signal_bar["open_time"],
        )

        execution_closes = [bar["close"] for bar in bars[: signal_index + 1]]
        daily_closes = await self._refresh_trend_snapshot(execution_closes, self.execution_timeframe)
        self._update_strategy_data_status(
            self.active_strategy_mode,
            execution_timeframe=self.execution_timeframe,
            execution_bars=bars,
            signal_index=signal_index,
            daily_closes=daily_closes,
        )

        level, pattern_result = self._match_level_and_pattern(prev_candle, signal_candle)
        if level is None:
            self._record_signal(None, None, "NO_LEVEL", "SKIP", strategy_mode=self.active_strategy_mode)
            await self._finalize_candle()
            return

        try:
            current_spread = self.api_client.get_current_spread()
        except Exception:
            current_spread = 0.0

        if pattern_result is None or pattern_result.pattern == PatternType.NONE:
            self._record_signal(
                None,
                level,
                "NO_PATTERN",
                "SKIP",
                entry_source="HTF_LEVEL",
                strategy_mode=self.active_strategy_mode,
            )
            await self._finalize_candle()
            return

        if daily_closes is None:
            self._record_signal(
                pattern_result.pattern,
                level,
                "EMA_DATA_UNAVAILABLE",
                "SKIP",
                entry_source="HTF_LEVEL",
                strategy_mode=self.active_strategy_mode,
            )
            await self._finalize_candle()
            return

        if pattern_result.pattern == PatternType.INSIDE_BAR:
            direction, trend_reason, _ = self.trend_filter.aligned_bias(
                daily_closes=daily_closes,
                execution_closes=execution_closes,
            )
            if direction is None:
                self._record_signal(
                    pattern_result.pattern,
                    level,
                    trend_reason,
                    "SKIP",
                    entry_source="HTF_LEVEL",
                    strategy_mode=self.active_strategy_mode,
                )
                await self._finalize_candle()
                return
        else:
            direction = pattern_result.direction
            trend_ok, trend_reason, _ = self.trend_filter.evaluate(
                direction=direction,
                daily_closes=daily_closes,
                execution_closes=execution_closes,
            )
            if not trend_ok:
                self._record_signal(
                    pattern_result.pattern,
                    level,
                    trend_reason,
                    "SKIP",
                    entry_source="HTF_LEVEL",
                    strategy_mode=self.active_strategy_mode,
                )
                await self._finalize_candle()
                return

        cooldown_reason = self._same_level_cooldown_reason(level, direction)
        if cooldown_reason is not None:
            self._record_signal(
                pattern_result.pattern,
                level,
                cooldown_reason,
                "SKIP",
                entry_source="HTF_LEVEL",
                strategy_mode=self.active_strategy_mode,
            )
            await self._finalize_candle()
            return

        sl_price = level - self.config.sl_offset_dollars if direction > 0 else level + self.config.sl_offset_dollars
        lot = calculate_lot_size(
            account_balance=self.account.get("balance", 0.0),
            entry_price=level,
            stop_loss_price=sl_price,
            symbol_spec=self.symbol_spec,
            current_spread_usd=current_spread,
            config=self.config,
        )
        if lot is None:
            self._record_signal(
                pattern_result.pattern,
                level,
                "LOT_SKIP",
                "SKIP",
                entry_source="HTF_LEVEL",
                strategy_mode=self.active_strategy_mode,
            )
            await self._finalize_candle()
            return

        tp_price = self.level_manager.next_level_from(level, direction)
        if tp_price is None:
            sl_dist = abs(level - sl_price)
            tp_price = level + direction * 2 * sl_dist

        action = "SKIP"
        if pattern_result.pattern == PatternType.INSIDE_BAR:
            buy_id, sell_id = await self.executor.place_inside_bar_orders(
                mother_bar_high=pattern_result.mother_bar_high,
                mother_bar_low=pattern_result.mother_bar_low,
                lot_size=lot,
                level=level,
                current_candle_index=self._candle_index,
                allowed_direction=direction,
            )
            if buy_id or sell_id:
                action = "EXECUTED"
                self._record_trade_level(level, direction)
            else:
                action = "OBSERVE_ONLY" if self.config.observe_only else "FAILED"
        else:
            pos_id = await self.executor.place_market_order(
                direction=direction,
                lot_size=lot,
                stop_loss_price=sl_price,
                take_profit_price=tp_price,
                pattern=pattern_result.pattern,
                level=level,
            )
            if pos_id:
                action = "EXECUTED"
                self._record_trade_level(level, direction)
            else:
                action = "OBSERVE_ONLY" if self.config.observe_only else "FAILED"

            if pos_id and len(self._recent_h1_closes) > 0:
                self._trade_entries_on_chart.append({
                    "bar_index": len(self._recent_h1_closes) - 1,
                    "direction": "LONG" if direction > 0 else "SHORT",
                    "price": level,
                })

        self._record_signal(
            pattern_result.pattern,
            level,
            "OK",
            action,
            entry_source="HTF_LEVEL",
            strategy_mode=self.active_strategy_mode,
        )
        await self._finalize_candle()

    async def _process_ema_pullback_candle_close(
        self,
        *,
        price: float,
        timestamp: datetime,
        store: str,
        apply_risk_gates: bool,
    ) -> None:
        """Run the H1 EMA-pullback strategy in live or shadow mode."""
        timeframe = self._strategy_timeframe("EMA_PULLBACK_H1")
        logger.debug("[TICK] %s EMA pullback candle closed at %s (%s)", timeframe, timestamp, store)

        bars, signal_index = await self._fetch_execution_bars(
            timeframe,
            count=self._strategy_execution_lookback("EMA_PULLBACK_H1"),
        )
        if bars is None or signal_index is None:
            self._update_strategy_data_status(
                "EMA_PULLBACK_H1",
                execution_timeframe=timeframe,
                execution_bars=bars,
                signal_index=signal_index,
            )
            if store == "shadow":
                await self.write_state()
            else:
                await self._finalize_candle()
            return

        if store == "live":
            self._recent_h1_closes = deque([bar["close"] for bar in bars[-20:]], maxlen=20)

        gate_result = await self._environment_gate(apply_risk_gates=apply_risk_gates)
        if gate_result is not None:
            self._record_signal(
                None,
                None,
                gate_result,
                "SKIP" if store == "live" else "SHADOW_SKIP",
                strategy_mode="EMA_PULLBACK_H1",
                setup_stage="ENVIRONMENT",
                store=store,
            )
            if store == "shadow":
                await self.write_state()
            else:
                await self._finalize_candle()
            return

        daily_closes = await self._fetch_daily_closes()
        self._update_strategy_data_status(
            "EMA_PULLBACK_H1",
            execution_timeframe=timeframe,
            execution_bars=bars,
            signal_index=signal_index,
            daily_closes=daily_closes,
        )
        decision = self.ema_pullback_strategy.evaluate(
            bars=bars,
            signal_index=signal_index,
            daily_closes=daily_closes,
            next_open_price=price,
            current_spread=self._safe_current_spread(),
        )

        execution_closes = [bar["close"] for bar in bars[: signal_index + 1]]
        decision_snapshot = self._trend_state(daily_closes, execution_closes, timeframe)
        if store == "live":
            self._trend_snapshot = decision_snapshot

        action = "SKIP" if store == "live" else "SHADOW_SKIP"
        if decision.is_ready:
            strategy_reason = self._strategy_trade_limit_reason(
                "EMA_PULLBACK_H1",
                decision.reference_level,
                decision.direction,
            )
            if strategy_reason is not None:
                decision.gate_result = strategy_reason
            else:
                if store == "shadow":
                    self._record_strategy_entry("EMA_PULLBACK_H1", decision.reference_level, decision.direction)
                    action = "SHADOW_READY"
                else:
                    lot = calculate_lot_size(
                        account_balance=self.account.get("balance", 0.0),
                        entry_price=decision.entry_price,
                        stop_loss_price=decision.stop_loss_price,
                        symbol_spec=self.symbol_spec,
                        current_spread_usd=self._safe_current_spread(),
                        config=self.config,
                    )
                    if lot is None:
                        decision.gate_result = "LOT_SKIP"
                    else:
                        pos_id = await self.executor.place_market_order(
                            direction=decision.direction,
                            lot_size=lot,
                            stop_loss_price=decision.stop_loss_price,
                            take_profit_price=decision.take_profit_price,
                            pattern=decision.pattern or PatternType.NONE,
                            level=decision.reference_level or decision.entry_price,
                        )
                        if pos_id:
                            self._record_strategy_entry("EMA_PULLBACK_H1", decision.reference_level, decision.direction)
                            self._trade_entries_on_chart.append({
                                "bar_index": len(self._recent_h1_closes) - 1,
                                "direction": "LONG" if decision.direction > 0 else "SHORT",
                                "price": decision.entry_price,
                            })
                            action = "EXECUTED"
                        else:
                            action = "OBSERVE_ONLY" if self.config.observe_only else "FAILED"

        self._record_signal(
            decision.pattern,
            decision.reference_level,
            decision.gate_result,
            action,
            entry_source=decision.entry_source,
            strategy_mode="EMA_PULLBACK_H1",
            setup_stage=decision.setup_stage,
            details=decision.metadata,
            trend_snapshot=decision_snapshot,
            store=store,
        )
        if store == "shadow":
            await self.write_state()
        else:
            await self._finalize_candle()

    async def _process_scalp_v1_candle_close(
        self,
        *,
        price: float,
        timestamp: datetime,
        store: str,
        apply_risk_gates: bool,
    ) -> None:
        """Run the aggressive M5 scalper in live or shadow mode."""
        timeframe = self._strategy_timeframe("SCALP_V1")
        logger.debug("[TICK] %s scalp candle closed at %s (%s)", timeframe, timestamp, store)

        bars, signal_index = await self._fetch_execution_bars(
            timeframe,
            count=self._strategy_execution_lookback("SCALP_V1"),
        )
        if bars is None or signal_index is None:
            self._update_strategy_data_status(
                "SCALP_V1",
                execution_timeframe=timeframe,
                execution_bars=bars,
                signal_index=signal_index,
            )
            if store == "shadow":
                await self.write_state()
            else:
                await self._finalize_candle()
            return

        if store == "live":
            self._recent_h1_closes = deque([bar["close"] for bar in bars[-20:]], maxlen=20)

        gate_result = await self._environment_gate(apply_risk_gates=apply_risk_gates)
        if gate_result is not None:
            self._record_signal(
                None,
                None,
                gate_result,
                "SKIP" if store == "live" else "SHADOW_SKIP",
                strategy_mode="SCALP_V1",
                setup_stage="ENVIRONMENT",
                store=store,
            )
            if store == "shadow":
                await self.write_state()
            else:
                await self._finalize_candle()
            return

        daily_closes = await self._fetch_daily_closes()
        h1_closes = self._aggregate_h1_closes_from_m5(bars)
        macro_regime = self._load_macro_regime()
        trade_policy = self._load_trade_policy()
        self._update_strategy_data_status(
            "SCALP_V1",
            execution_timeframe=timeframe,
            execution_bars=bars,
            signal_index=signal_index,
            daily_closes=daily_closes,
            h1_closes=h1_closes,
        )
        decision = self.scalp_strategy.evaluate(
            m5_bars=bars,
            signal_index=signal_index,
            daily_closes=daily_closes or [],
            h1_closes=h1_closes or [],
            next_open_price=price,
            current_spread=self._safe_current_spread(),
            macro_regime=macro_regime,
            trade_policy=trade_policy.to_state_dict() if trade_policy is not None else None,
        )
        if store == "live":
            self._macro_regime = macro_regime
            self._trade_policy = trade_policy

        if daily_closes is not None and h1_closes is not None:
            decision_snapshot = self.scalp_strategy.state_trend(
                daily_closes=daily_closes,
                h1_closes=h1_closes,
                macro_regime=macro_regime,
                trade_policy=trade_policy.to_state_dict() if trade_policy is not None else None,
            )
        else:
            decision_snapshot = {"alignment": "UNKNOWN", "reason": "EMA_DATA_UNAVAILABLE", "execution_timeframe": timeframe}
        if store == "live":
            self._trend_snapshot = decision_snapshot

        action = "SKIP" if store == "live" else "SHADOW_SKIP"
        if decision.is_ready:
            strategy_reason = self._strategy_trade_limit_reason(
                "SCALP_V1",
                decision.reference_level,
                decision.direction,
            )
            if strategy_reason is not None:
                decision.gate_result = strategy_reason
            else:
                macro_ok, macro_reason = self.macro_regime_loader.gate_direction(
                    macro_regime,
                    direction=decision.direction,
                    now_utc=datetime.now(timezone.utc),
                    confidence_threshold=self.config.macro_regime_confidence_threshold,
                )
                if not macro_ok:
                    decision.gate_result = macro_reason or "MACRO_DIRECTION_BLOCK"
                elif store == "shadow":
                    self._record_strategy_entry("SCALP_V1", decision.reference_level, decision.direction)
                    action = "SHADOW_READY"
                else:
                    lot = calculate_lot_size(
                        account_balance=self.account.get("balance", 0.0),
                        entry_price=decision.entry_price,
                        stop_loss_price=decision.stop_loss_price,
                        symbol_spec=self.symbol_spec,
                        current_spread_usd=self._safe_current_spread(),
                        config=self.config,
                        min_sl_distance=self.config.scalp_sl_min_dollars,
                        max_sl_distance=self.config.scalp_sl_max_dollars,
                    )
                    if lot is None:
                        decision.gate_result = "LOT_SKIP"
                    else:
                        pos_id = await self.executor.place_market_order(
                            direction=decision.direction,
                            lot_size=lot,
                            stop_loss_price=decision.stop_loss_price,
                            take_profit_price=decision.take_profit_price,
                            pattern=decision.pattern or PatternType.NONE,
                            level=decision.reference_level or decision.entry_price,
                        )
                        if pos_id:
                            self._record_strategy_entry("SCALP_V1", decision.reference_level, decision.direction)
                            self._trade_entries_on_chart.append(
                                {
                                    "bar_index": len(self._recent_h1_closes) - 1,
                                    "direction": "LONG" if decision.direction > 0 else "SHORT",
                                    "price": decision.entry_price,
                                }
                            )
                            action = "EXECUTED"
                        else:
                            action = "OBSERVE_ONLY" if self.config.observe_only else "FAILED"

        self._record_signal(
            decision.pattern,
            decision.reference_level,
            decision.gate_result,
            action,
            entry_source=decision.entry_source,
            strategy_mode="SCALP_V1",
            setup_stage=decision.setup_stage,
            details=decision.metadata,
            trend_snapshot=decision_snapshot,
            store=store,
        )
        if store == "shadow":
            await self.write_state()
        else:
            await self._finalize_candle()

    async def _finalize_candle(self) -> None:
        """Called at end of every execution-timeframe close cycle."""
        self.risk_gates.set_open_position_count(self.executor.position_manager.count())
        await self.executor.check_pending_inside_bar_orders(self._candle_index)
        await self.write_state()

    def _make_signal_record(
        self,
        pattern,
        level,
        gate_result,
        action,
        entry_source: Optional[str] = None,
        strategy_mode: Optional[str] = None,
        setup_stage: Optional[str] = None,
        details: Optional[Dict] = None,
        trend_snapshot: Optional[Dict] = None,
    ) -> Dict:
        record = {
            "time_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pattern": pattern.name if pattern else None,
            "level_checked": level,
            "gate_result": gate_result,
            "action": action,
        }
        if entry_source is not None:
            record["entry_source"] = entry_source
        if strategy_mode is not None:
            record["strategy_mode"] = strategy_mode
        if setup_stage is not None:
            record["setup_stage"] = setup_stage
        if details:
            record["details"] = details
        if trend_snapshot is not None:
            record["trend"] = trend_snapshot
        elif self._trend_snapshot is not None:
            record["trend"] = self._trend_snapshot
        return record

    def _record_signal(
        self,
        pattern,
        level,
        gate_result,
        action,
        entry_source: Optional[str] = None,
        strategy_mode: Optional[str] = None,
        setup_stage: Optional[str] = None,
        details: Optional[Dict] = None,
        trend_snapshot: Optional[Dict] = None,
        store: str = "live",
    ) -> None:
        record = self._make_signal_record(
            pattern,
            level,
            gate_result,
            action,
            entry_source=entry_source,
            strategy_mode=strategy_mode,
            setup_stage=setup_stage,
            details=details,
            trend_snapshot=trend_snapshot,
        )
        if store == "shadow":
            self._shadow_last_signal = record
            self._shadow_signal_history.appendleft(record)
        else:
            self._last_signal = record
            self._signal_history.appendleft(record)
        logger.info(
            "[SIGNAL][%s] %s %s | pattern=%s level=%s trend=%s",
            store.upper(),
            action,
            gate_result,
            record["pattern"] or "NONE",
            f"{level:.2f}" if isinstance(level, (int, float)) else "NONE",
            record.get("trend", {}).get("alignment", "UNKNOWN"),
        )

    async def _prime_recent_context(self) -> None:
        """Seed chart/indicator context from recent execution-timeframe history at startup."""
        bar_count = self._strategy_execution_lookback(self.active_strategy_mode)
        try:
            bars = await self.api_client.get_trendbar(self.execution_timeframe, bar_count)
        except Exception as exc:
            logger.warning("[STARTUP] Failed to seed %s context: %s", self.execution_timeframe, exc)
            self._update_strategy_data_status(
                self.active_strategy_mode,
                execution_timeframe=self.execution_timeframe,
                execution_bars=None,
                signal_index=None,
            )
            return

        self._recent_h1_closes = deque(
            [bar["close"] for bar in bars[-20:]],
            maxlen=20,
        )
        if self.active_strategy_mode == "SCALP_V1":
            daily_closes = await self._fetch_daily_closes()
            h1_closes = self._aggregate_h1_closes_from_m5(bars)
            self._macro_regime = self._load_macro_regime()
            self._trade_policy = self._load_trade_policy()
            self._update_strategy_data_status(
                self.active_strategy_mode,
                execution_timeframe=self.execution_timeframe,
                execution_bars=bars,
                signal_index=len(bars) - 2 if len(bars) >= 2 else None,
                daily_closes=daily_closes,
                h1_closes=h1_closes,
            )
            if daily_closes is not None and h1_closes is not None:
                self._trend_snapshot = self.scalp_strategy.state_trend(
                    daily_closes=daily_closes,
                    h1_closes=h1_closes,
                    macro_regime=self._macro_regime,
                )
            else:
                self._trend_snapshot = {
                    "alignment": "UNKNOWN",
                    "reason": "EMA_DATA_UNAVAILABLE",
                    "execution_timeframe": self.execution_timeframe,
                }
            return

        execution_closes = [bar["close"] for bar in bars]
        daily_closes = await self._refresh_trend_snapshot(execution_closes, self.execution_timeframe)
        self._update_strategy_data_status(
            self.active_strategy_mode,
            execution_timeframe=self.execution_timeframe,
            execution_bars=bars,
            signal_index=len(bars) - 2 if len(bars) >= 2 else None,
            daily_closes=daily_closes,
        )

    async def _fetch_daily_closes(self) -> Optional[List[float]]:
        """Fetch closed D1 bars for bias calculation."""
        try:
            daily_bars = await self.api_client.get_trendbar("D1", _DAILY_BAR_LOOKBACK)
        except Exception as exc:
            logger.error("[TREND] Failed to fetch D1 bars: %s", exc)
            return None
        return [bar["close"] for bar in daily_bars]

    async def _fetch_h1_closes(self) -> Optional[List[float]]:
        """Fetch closed H1 bars for higher-timeframe confirmation."""
        last_exc = None
        for attempt in range(2):
            try:
                h1_bars = await self.api_client.get_trendbar("H1", 260)
                return [bar["close"] for bar in h1_bars]
            except Exception as exc:
                last_exc = exc
                if attempt == 0:
                    await asyncio.sleep(0.5)
                    continue
        logger.error("[TREND] Failed to fetch H1 bars: %s", last_exc)
        return None

    def _load_macro_regime(self) -> Optional[MacroRegime]:
        return self.macro_regime_loader.load(datetime.now(timezone.utc))

    def _load_trade_policy(self) -> Optional[TradePolicy]:
        return self.trade_policy_loader.load(datetime.now(timezone.utc))

    async def _refresh_trend_snapshot(
        self,
        execution_closes: List[float],
        execution_timeframe: str,
    ) -> Optional[List[float]]:
        """Fetch D1 closes and refresh the active trend snapshot."""
        daily_closes = await self._fetch_daily_closes()
        if daily_closes is None:
            self._trend_snapshot = {
                "alignment": "UNKNOWN",
                "reason": "EMA_DATA_UNAVAILABLE",
                "execution_timeframe": execution_timeframe,
            }
            return None
        self._trend_snapshot = self._trend_state(daily_closes, execution_closes, execution_timeframe)
        return daily_closes

    async def _fetch_execution_bars(
        self,
        timeframe: str,
        count: int = _EXECUTION_BAR_LOOKBACK,
    ) -> tuple[Optional[List[Dict]], Optional[int]]:
        """Fetch closed bars and choose the latest confirmed signal index for a timeframe."""
        try:
            bars = await self.api_client.get_trendbar(timeframe, count)
        except Exception as exc:
            logger.error("[TICK] Failed to fetch %s bars: %s", timeframe, exc)
            return None, None

        if len(bars) < 3:
            logger.warning("[TICK] Not enough %s bars (%d).", timeframe, len(bars))
            return None, None

        signal_index = self._select_signal_index(bars, self._watchers[timeframe])
        if signal_index is None:
            logger.warning("[TICK] Could not determine signal bar for %s close.", timeframe)
            return None, None
        return bars, signal_index

    @staticmethod
    def _aggregate_h1_closes_from_m5(bars: List[Dict]) -> Optional[List[float]]:
        """Roll up M5 bars into H1 closes for the H1 50/200 confirmation."""
        if not bars:
            return None
        closes: List[float] = []
        current_bucket = None
        last_close = None
        for bar in bars:
            bucket = bar["open_time"].replace(minute=0, second=0, microsecond=0)
            if current_bucket is None:
                current_bucket = bucket
            elif bucket != current_bucket:
                if last_close is not None:
                    closes.append(last_close)
                current_bucket = bucket
            last_close = bar["close"]
        if last_close is not None:
            closes.append(last_close)
        return closes if len(closes) >= 200 else None

    def _match_level_and_pattern(self, prev_candle, signal_candle):
        """
        Find the best HTF level touched by the recent price action and evaluate patterns there.

        Candidate levels are drawn from the combined range of the previous and
        signal candles, expanded by the configured proximity, so wick/body
        reactions are not discarded just because the close moved away.
        """
        if self.level_manager is None or self.pattern_detector is None:
            return None, None

        range_low = min(prev_candle.low, signal_candle.low)
        range_high = max(prev_candle.high, signal_candle.high)
        candidates = self.level_manager.levels_near_range(range_low, range_high)
        candidates = sorted(candidates, key=lambda lvl: abs(signal_candle.close - lvl))
        if not candidates:
            return None, None

        rank = {
            PatternType.BULLISH_ENGULFING: 3,
            PatternType.BEARISH_ENGULFING: 3,
            PatternType.BULLISH_PIN_BAR: 2,
            PatternType.BEARISH_PIN_BAR: 2,
            PatternType.INSIDE_BAR: 1,
            PatternType.NONE: 0,
        }

        best_level = candidates[0]
        best_result = None
        best_rank = -1
        best_distance = float("inf")

        for level in candidates:
            result = self.pattern_detector.detect(prev_candle, signal_candle, level)
            if result.pattern == PatternType.NONE:
                continue
            current_rank = rank[result.pattern]
            distance = abs(signal_candle.close - level)
            if current_rank > best_rank or (current_rank == best_rank and distance < best_distance):
                best_rank = current_rank
                best_distance = distance
                best_level = level
                best_result = result

        return best_level, best_result

    def _select_signal_index(self, bars: List[Dict], watcher: CandleWatcher) -> Optional[int]:
        """Choose the latest fully closed signal bar using returned open_time values."""
        if len(bars) < 3:
            return None

        latest = bars[-1]
        if watcher.current_candle_open and latest["open_time"] == watcher.current_candle_open:
            return len(bars) - 2
        return len(bars) - 1

    def _record_trade_level(self, level: float, direction: int) -> None:
        self._recent_trade_levels.appendleft(
            {"level": level, "direction": direction, "candle_index": self._candle_index}
        )

    def _same_level_cooldown_reason(self, level: float, direction: int) -> Optional[str]:
        cooldown = getattr(self.config, "same_level_cooldown_candles", 0)
        if cooldown <= 0:
            return None
        for item in self._recent_trade_levels:
            if item["direction"] != direction:
                continue
            if abs(item["level"] - level) > self.config.level_proximity_dollars:
                continue
            if self._candle_index - item["candle_index"] <= cooldown:
                return "SAME_LEVEL_COOLDOWN"
        return None

    def _record_strategy_entry(self, mode: str, level: Optional[float], direction: Optional[int]) -> None:
        self._reset_strategy_day(mode)
        candle_index = self._timeframe_candle_indices.get(self._strategy_timeframe(mode), 0)
        self._strategy_trade_counts[mode] += 1
        self._strategy_entries[mode].appendleft(
            {
                "level": level or 0.0,
                "direction": direction or 0,
                "candle_index": candle_index,
            }
        )

    def _strategy_trade_limit_reason(
        self,
        mode: str,
        level: Optional[float],
        direction: Optional[int],
    ) -> Optional[str]:
        self._reset_strategy_day(mode)
        trade_cap = (
            self.config.scalp_max_trades_per_day
            if mode == "SCALP_V1"
            else self.config.strategy_max_trades_per_day
        )
        if self._strategy_trade_counts[mode] >= trade_cap:
            return "STRATEGY_DAILY_CAP"
        if level is None or direction is None:
            return None
        cooldown = (
            self.config.scalp_reentry_cooldown_bars
            if mode == "SCALP_V1"
            else self.config.strategy_reentry_cooldown_candles
        )
        level_proximity = (
            self.config.scalp_touch_proximity_dollars
            if mode == "SCALP_V1"
            else self.config.ema_pullback_proximity_dollars
        )
        candle_index = self._timeframe_candle_indices.get(self._strategy_timeframe(mode), 0)
        for item in self._strategy_entries[mode]:
            if item["direction"] != direction:
                continue
            if abs(item["level"] - level) > level_proximity:
                continue
            if candle_index - item["candle_index"] <= cooldown:
                return "SCALP_COOLDOWN" if mode == "SCALP_V1" else "EMA_PULLBACK_COOLDOWN"
        return None

    def _reset_strategy_day(self, mode: str) -> None:
        today = self._today_utc()
        if self._strategy_trade_dates.get(mode) != today:
            self._strategy_trade_dates[mode] = today
            self._strategy_trade_counts[mode] = 0

    def _strategy_timeframe(self, mode: str) -> str:
        if mode == "EMA_PULLBACK_H1":
            return "H1"
        if mode == "SCALP_V1":
            return "M5"
        return self.config.execution_timeframe

    def _strategy_execution_lookback(self, mode: str) -> int:
        if mode == "SCALP_V1":
            return _SCALP_BAR_LOOKBACK
        if mode == "EMA_PULLBACK_H1":
            return _EMA_PULLBACK_BAR_LOOKBACK
        return _EXECUTION_BAR_LOOKBACK

    def _strategy_data_requirements(self, mode: str) -> Dict[str, int]:
        requirements = {
            "daily_closes_min": 21,
            "signal_index_min": 1,
        }
        if mode == "SCALP_V1":
            requirements.update(
                {
                    "execution_bars_min": max(
                        self.config.scalp_slow_ema_period,
                        self.config.scalp_atr_period + 1,
                        self.config.scalp_pullback_lookback_bars,
                    ),
                    "h1_closes_min": 200,
                    "execution_fetch_bars": self._strategy_execution_lookback(mode),
                }
            )
            return requirements
        requirements.update(
            {
                "execution_bars_min": 200,
                "h1_closes_min": 200,
                "execution_fetch_bars": self._strategy_execution_lookback(mode),
            }
        )
        return requirements

    def _update_strategy_data_status(
        self,
        mode: str,
        *,
        execution_timeframe: str,
        execution_bars: Optional[List[Dict]],
        signal_index: Optional[int],
        daily_closes: Optional[List[float]] = None,
        h1_closes: Optional[List[float]] = None,
    ) -> None:
        requirements = self._strategy_data_requirements(mode)
        execution_count = len(execution_bars) if execution_bars else 0
        daily_count = len(daily_closes) if daily_closes else 0
        h1_count = len(h1_closes) if h1_closes else 0
        if mode != "SCALP_V1":
            h1_count = execution_count

        reason = "OK"
        data_ready = True
        if execution_count < requirements["execution_bars_min"]:
            reason = "EXECUTION_BARS_UNAVAILABLE"
            data_ready = False
        elif signal_index is None or signal_index < requirements["signal_index_min"]:
            reason = "SIGNAL_BAR_UNAVAILABLE"
            data_ready = False
        elif daily_closes is None or daily_count < requirements["daily_closes_min"]:
            reason = "DAILY_BARS_UNAVAILABLE"
            data_ready = False
        elif mode == "SCALP_V1" and (h1_closes is None or h1_count < requirements["h1_closes_min"]):
            reason = "H1_CONFIRMATION_UNAVAILABLE"
            data_ready = False

        self._strategy_data_status[mode] = {
            "mode": mode,
            "execution_timeframe": execution_timeframe,
            "data_ready": data_ready,
            "reason": reason,
            "execution_bars": execution_count,
            "execution_bars_min": requirements["execution_bars_min"],
            "execution_fetch_bars": requirements["execution_fetch_bars"],
            "signal_index": signal_index,
            "daily_closes": daily_count,
            "daily_closes_min": requirements["daily_closes_min"],
            "h1_closes": h1_count,
            "h1_closes_min": requirements["h1_closes_min"],
            "updated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def current_strategy_health(self, now_utc: Optional[datetime] = None) -> Tuple[bool, Optional[str]]:
        del now_utc
        diagnostics = self._strategy_data_status.get(self.active_strategy_mode)
        if diagnostics is None:
            return False, "NO_STRATEGY_DIAGNOSTICS"
        return bool(diagnostics.get("data_ready")), diagnostics.get("reason")

    def _safe_current_spread(self) -> float:
        try:
            return self.api_client.get_current_spread()
        except Exception:
            return 0.0

    async def _environment_gate(self, apply_risk_gates: bool) -> Optional[str]:
        """Return the reason code for blocking a setup, or None when environment is tradeable."""
        now_utc = datetime.now(timezone.utc)

        tradeable, reason = self.session_filter.is_tradeable(now_utc)
        if not tradeable:
            return reason

        if apply_risk_gates:
            can_trade, reason = self.risk_gates.can_trade(self.account.get("balance", 0.0))
            if not can_trade:
                self.bot_status = f"HALTED_{reason}"
                return reason

        try:
            await self.news_filter.refresh_if_needed()
        except Exception as exc:
            logger.warning("[NEWS] Refresh attempt failed during candle processing: %s", exc)

        news_clear, reason = self.news_filter.is_clear(now_utc)
        if not news_clear:
            return reason

        if self.kill_switch_active:
            return "KILL_SWITCH"
        return None

    def _trend_state(self, daily_closes: List[float], execution_closes: List[float], execution_timeframe: str) -> Dict:
        bias, reason, snapshot = self.trend_filter.aligned_bias(
            daily_closes=daily_closes,
            execution_closes=execution_closes,
        )
        state = {
            "alignment": (
                "BULLISH" if bias == 1 else
                "BEARISH" if bias == -1 else
                "UNKNOWN" if snapshot is None else
                "MIXED"
            ),
            "reason": reason,
            "execution_timeframe": execution_timeframe,
        }
        if snapshot is not None:
            state.update({
                "daily_ema_8": round(snapshot.daily_ema_8, 2),
                "daily_ema_21": round(snapshot.daily_ema_21, 2),
                "exec_ema_50": round(snapshot.exec_ema_50, 2),
                "exec_ema_200": round(snapshot.exec_ema_200, 2),
            })
        return state

    @staticmethod
    def _today_utc() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ──────────────────────────────────────────────────────────────
    # Shutdown
    # ──────────────────────────────────────────────────────────────

    async def shutdown(self) -> None:
        """Graceful shutdown: write SHUTDOWN status, close API, exit."""
        logger.info("[SHUTDOWN] Shutting down at %s UTC", datetime.now(timezone.utc).isoformat())
        self.running = False
        self.bot_status = "SHUTDOWN"
        try:
            await self.write_state()
        except Exception:
            pass
        if self.news_filter:
            try:
                await self.news_filter.close()
            except Exception:
                pass
        if self.api_client:
            try:
                await self.api_client.disconnect()
            except Exception:
                pass
        logger.info("[SHUTDOWN] Complete.")

    # ──────────────────────────────────────────────────────────────
    # State writing
    # ──────────────────────────────────────────────────────────────

    def set_status(self, status: str) -> None:
        self.bot_status = status

    async def write_state(self) -> None:
        if self.state_writer is None:
            return
        levels = self.level_manager._raw if self.level_manager else None
        open_positions = self.executor.position_manager.get_open_positions() if self.executor else []
        closed_trades = self.executor._closed_trades_today if self.executor else []
        await self.state_writer.write(
            bot_status=self.bot_status,
            account=self.account,
            risk_state=self.risk_state,
            levels=levels,
            open_positions=open_positions,
            closed_trades=closed_trades,
            recent_h1_closes=list(self._recent_h1_closes),
            trade_entries_on_chart=self._trade_entries_on_chart,
            last_signal=self._last_signal,
            signal_history=list(self._signal_history),
            strategy={
                "active_mode": self.active_strategy_mode,
                "shadow_mode": self.shadow_strategy_mode,
            },
            shadow_last_signal=self._shadow_last_signal,
            shadow_signal_history=list(self._shadow_signal_history),
            macro_regime=(
                self._macro_regime.to_state_dict()
                if self._macro_regime is not None
                else None
            ),
            trade_policy=(
                self._trade_policy.to_state_dict()
                if self._trade_policy is not None
                else None
            ),
            trend=self._trend_snapshot,
            runtime={
                "strategy_mode": self.active_strategy_mode,
                "shadow_strategy_mode": self.shadow_strategy_mode,
                "execution_timeframe": self.execution_timeframe,
                "reconnect_count": self.watchdog.reconnect_count if self.watchdog else 0,
                "news_feed_available": self.news_filter.feed_available if self.news_filter else None,
                "kill_switch_active": self.kill_switch_active,
                "candle_index": self._candle_index,
                "pending_inside_bar_pairs": len(self.executor._pending_pairs) if self.executor else 0,
                "recent_trade_levels": len(self._recent_trade_levels),
                "strategy_trades_today": self._strategy_trade_counts.get(self.active_strategy_mode, 0),
                "shadow_strategy_trades_today": (
                    self._strategy_trade_counts.get(self.shadow_strategy_mode, 0)
                    if self.shadow_strategy_mode else 0
                ),
                "strategy_data_status": self._strategy_data_status.get(self.active_strategy_mode),
                "shadow_strategy_data_status": (
                    self._strategy_data_status.get(self.shadow_strategy_mode)
                    if self.shadow_strategy_mode else None
                ),
            },
            last_error=self.last_error,
        )
        # Persist risk state to its own file on every write
        await save_risk_state(self.risk_state, self.config.state_file_path)

    # ──────────────────────────────────────────────────────────────
    # Background tasks
    # ──────────────────────────────────────────────────────────────

    async def _poll_kill_switch(self) -> None:
        while self.running:
            await asyncio.sleep(10)
            try:
                with open(self.config.cmd_file_path, "r") as f:
                    cmd = json.load(f)
                if cmd.get("kill_switch") is True and not self.kill_switch_active:
                    logger.warning("[KILL SWITCH] Activated. Halting new trade entry.")
                    self.kill_switch_active = True
                    self.set_status("HALTED_KILL_SWITCH")
                    await self.write_state()
                elif cmd.get("kill_switch") is False and self.kill_switch_active:
                    logger.info("[KILL SWITCH] Deactivated. Resuming trading.")
                    self.kill_switch_active = False
                    self.set_status("OBSERVE_ONLY" if self.config.observe_only else "RUNNING")
                    await self.write_state()
            except FileNotFoundError:
                pass
            except Exception as exc:
                logger.warning("[KILL SWITCH] cmd.json read error: %s", exc)

    async def _poll_mirofish_signal(self) -> None:
        """Poll cmd.json for MiroFish trading signals (runs only in MIROFISH_MODE)."""
        _DIRECTION_MAP = {"BUY": 1, "SELL": -1}
        while self.running:
            await asyncio.sleep(10)
            if self.kill_switch_active:
                continue
            try:
                with open(self.config.mirofish_signal_path, "r") as f:
                    cmd = json.load(f)
            except FileNotFoundError:
                continue
            except Exception as exc:
                logger.warning("[MIROFISH] Signal file read error: %s", exc)
                continue

            sig = cmd.get("mirofish_signal")
            if sig is None:
                continue

            signal_id = sig.get("timestamp_utc", "")
            if signal_id == self._last_mirofish_signal_id:
                continue

            action = str(sig.get("action", "HOLD")).upper()
            direction = _DIRECTION_MAP.get(action)
            if direction is None:
                logger.info("[MIROFISH] Signal action=%s - no trade (HOLD or unknown)", action)
                self._last_mirofish_signal_id = signal_id
                continue

            try:
                confidence = float(sig.get("confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            if confidence < 0.6:
                logger.info("[MIROFISH] Signal confidence %.2f below threshold 0.6 - skipping", confidence)
                self._last_mirofish_signal_id = signal_id
                continue

            try:
                ts = datetime.fromisoformat(signal_id.replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - ts).total_seconds()
                if age > self.config.mirofish_signal_max_age_seconds:
                    logger.info(
                        "[MIROFISH] Signal is %.0fs old (max %ds) - stale, skipping",
                        age,
                        self.config.mirofish_signal_max_age_seconds,
                    )
                    self._last_mirofish_signal_id = signal_id
                    continue
            except (ValueError, TypeError):
                logger.warning("[MIROFISH] Cannot parse timestamp '%s' - executing anyway", signal_id)

            if self.symbol_spec is None:
                logger.warning("[MIROFISH] Symbol spec not loaded yet - skipping")
                continue

            signal_symbol = str(sig.get("symbol") or "").upper()
            live_symbol = str(getattr(self.symbol_spec, "symbol", "") or "").upper()
            if signal_symbol and live_symbol and signal_symbol != live_symbol:
                logger.info(
                    "[MIROFISH] Signal symbol %s does not match configured symbol %s - skipping",
                    signal_symbol,
                    live_symbol,
                )
                self._last_mirofish_signal_id = signal_id
                continue

            distance_unit = str(sig.get("distance_unit", "usd") or "usd").lower()
            if distance_unit not in ("usd", "dollars", "price"):
                logger.info(
                    "[MIROFISH] Signal distance_unit=%s is not executable by the current XAUEX runtime - skipping",
                    distance_unit,
                )
                self._last_mirofish_signal_id = signal_id
                continue

            gate_result = await self._environment_gate(apply_risk_gates=True)
            if gate_result is not None:
                logger.info("[MIROFISH] Blocked by gate: %s", gate_result)
                continue

            bid = self.api_client._last_bid
            ask = self.api_client._last_ask
            if bid is None or ask is None:
                logger.warning("[MIROFISH] No bid/ask available yet - skipping")
                continue

            try:
                sl_distance = float(sig.get("stop_loss_usd", sig.get("stop_loss_distance", 12.0)))
                tp_distance = float(sig.get("take_profit_usd", sig.get("take_profit_distance", 24.0)))
            except (TypeError, ValueError):
                logger.warning("[MIROFISH] Invalid stop or take-profit values in signal - skipping")
                self._last_mirofish_signal_id = signal_id
                continue

            if sl_distance <= 0 or tp_distance <= 0:
                logger.info("[MIROFISH] Non-positive stop or take-profit distance - skipping")
                self._last_mirofish_signal_id = signal_id
                continue

            if direction == 1:
                current_price = ask
                stop_loss_price = current_price - sl_distance
                take_profit_price = current_price + tp_distance
            else:
                current_price = bid
                stop_loss_price = current_price + sl_distance
                take_profit_price = current_price - tp_distance

            lot = calculate_lot_size(
                account_balance=self.account.get("balance", 0.0),
                entry_price=current_price,
                stop_loss_price=stop_loss_price,
                symbol_spec=self.symbol_spec,
                current_spread_usd=self._safe_current_spread(),
                config=self.config,
            )
            if lot is None:
                logger.info("[MIROFISH] Lot size calculation returned None - skipping")
                self._last_mirofish_signal_id = signal_id
                continue

            reasoning = sig.get("reasoning", "MiroFish signal")
            dir_label = "LONG" if direction == 1 else "SHORT"
            logger.info(
                "[MIROFISH] Executing %s %s | Lot:%.2f SL:%.2f TP:%.2f | Confidence:%.2f | %s",
                signal_symbol or live_symbol or "XAUUSD",
                dir_label,
                lot,
                stop_loss_price,
                take_profit_price,
                confidence,
                reasoning,
            )

            pos_id = await self.executor.place_market_order(
                direction=direction,
                lot_size=lot,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                pattern=PatternType.NONE,
                level=current_price,
            )

            if pos_id:
                logger.info("[MIROFISH] Order placed: position_id=%s", pos_id)
            else:
                if self.config.observe_only:
                    logger.info("[MIROFISH] OBSERVE_ONLY - order logged but not placed")
                else:
                    logger.warning("[MIROFISH] Order placement returned None")

            self._last_mirofish_signal_id = signal_id
            await self.write_state()

    async def _poll_account_snapshot(self) -> None:
        """Refresh account balance/equity periodically so the dashboard stays live."""
        while self.running:
            await asyncio.sleep(15)
            if self.api_client is None:
                continue
            try:
                await self._refresh_account_snapshot()
                await self.write_state()
            except Exception as exc:
                logger.warning("[ACCOUNT] Refresh failed: %s", exc)

    async def _check_token_refresh(self) -> None:
        expiry = getattr(self.config, "ctrader_token_expiry", 0)
        now_ts = datetime.now(timezone.utc).timestamp()
        if expiry and (expiry - now_ts) < 300:
            logger.info("[TOKEN] Refreshing OAuth token...")
            from auth import refresh_token
            await refresh_token(self.config)

    async def _restore_risk_state(self) -> None:
        """Restore RiskState from dedicated risk_state.json (survives restarts)."""
        restored = await load_risk_state(self.config.state_file_path)
        if restored is not None:
            self.risk_state = restored
            self.risk_gates = RiskGates(self.config, self.risk_state)
            logger.info("[STARTUP] Risk state restored from risk_state.json.")

    async def _on_execution_event(self, event) -> None:
        """Synchronize local state from unsolicited broker execution events."""
        if self.executor is None or self.risk_gates is None or self.symbol_spec is None:
            return

        try:
            if getattr(event, "executionType", None) != 3:  # ORDER_FILLED
                return

            should_refresh_account = False
            if event.HasField("deal") and event.deal.HasField("closePositionDetail"):
                close_detail = event.deal.closePositionDetail
                money_digits = getattr(close_detail, "moneyDigits", 2) or 2
                pnl = close_detail.grossProfit / (10 ** money_digits)
                await self.executor.on_position_closed(
                    position_id=str(event.deal.positionId),
                    close_price=event.deal.executionPrice,
                    pnl=pnl,
                )
                should_refresh_account = True
            elif event.HasField("position"):
                position = event.position
                position_id = str(position.positionId)
                order_id = str(event.order.orderId) if event.HasField("order") else None
                pending_market = self.executor.consume_pending_market_order(order_id)
                tracked = self.executor.position_manager.get_position(position_id)

                if tracked is None:
                    direction = (pending_market or {}).get("direction") or ("LONG" if position.tradeData.tradeSide == 1 else "SHORT")
                    lot_size = (pending_market or {}).get("lot_size")
                    if lot_size is None:
                        lot_size = position.tradeData.volume / (self.symbol_spec.lot_size * 100)
                    tracked = TrackedPosition(
                        position_id=position_id,
                        direction=direction,
                        entry_price=position.price,
                        stop_loss=(pending_market or {}).get("stop_loss", position.stopLoss if position.HasField("stopLoss") else 0.0),
                        take_profit=(pending_market or {}).get("take_profit", position.takeProfit if position.HasField("takeProfit") else 0.0),
                        lot_size=lot_size,
                        open_time_utc=datetime.fromtimestamp(
                            position.tradeData.openTimestamp / 1000,
                            tz=timezone.utc,
                        ),
                        pattern=(pending_market or {}).get("pattern", PatternType.NONE),
                        level=(pending_market or {}).get("level", position.price),
                    )
                    self.executor.position_manager.add(tracked)
                else:
                    tracked.entry_price = position.price
                    if position.HasField("stopLoss"):
                        tracked.stop_loss = position.stopLoss
                    elif pending_market is not None:
                        tracked.stop_loss = pending_market.get("stop_loss", tracked.stop_loss)
                    if position.HasField("takeProfit"):
                        tracked.take_profit = position.takeProfit
                    elif pending_market is not None:
                        tracked.take_profit = pending_market.get("take_profit", tracked.take_profit)
                    if pending_market is not None:
                        tracked.pattern = pending_market.get("pattern", tracked.pattern)
                        tracked.level = pending_market.get("level", tracked.level)

                if order_id is not None:
                    await self.executor.on_order_filled(
                        order_id=order_id,
                        position_id=position_id,
                        entry_price=tracked.entry_price,
                    )
                should_refresh_account = True
            else:
                return

            if should_refresh_account:
                await self._refresh_account_snapshot()

            self.risk_gates.set_open_position_count(self.executor.position_manager.count())
            await self.write_state()
        except Exception as exc:
            logger.error("[EXECUTION EVENT] Failed to process broker event: %s", exc)

    async def _refresh_account_snapshot(self) -> None:
        """Refresh balance/equity/currency from broker after realised P&L events."""
        if self.api_client is None:
            return
        account_data = await self.api_client.get_account()
        self.account = {
            "balance": account_data.balance,
            "equity": account_data.equity,
            "currency": account_data.currency,
            "open_pnl": account_data.open_pnl,
        }


# ──────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────

async def main() -> None:
    config = load_config()

    # File logging — rotating, 10 MB × 5 backups, written from a background
    # thread via QueueHandler so log calls never block the asyncio event loop.
    log_dir = os.path.dirname(config.log_file_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        config.log_file_path, maxBytes=10 * 1024 * 1024, backupCount=5,
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))

    log_queue: queue.SimpleQueue = queue.SimpleQueue()
    queue_handler = logging.handlers.QueueHandler(log_queue)
    queue_listener = logging.handlers.QueueListener(
        log_queue, file_handler, respect_handler_level=True
    )
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(queue_handler)
    queue_listener.start()

    orchestrator = BotOrchestrator(config)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig,
            lambda: asyncio.create_task(orchestrator.shutdown()),
        )

    try:
        await orchestrator.startup()
        await orchestrator.main_loop()
    except SystemExit:
        raise
    except Exception:
        logger.exception("[FATAL] Unhandled exception — exiting for systemd restart.")
        sys.exit(1)
    finally:
        queue_listener.stop()


if __name__ == "__main__":
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
        )
    asyncio.run(main())
