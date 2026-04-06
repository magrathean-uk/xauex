"""Order execution and position management."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, List

from config import Config
from bot.patterns.detector import PatternType

logger = logging.getLogger(__name__)

_API_ERROR_TRADING_DISABLED = "TRADING_DISABLED"
_API_ERROR_NOT_ENOUGH_MONEY = "NOT_ENOUGH_MONEY"
_API_ERROR_MARKET_CLOSED = "MARKET_CLOSED"
_API_ERROR_POSITION_NOT_FOUND = "POSITION_NOT_FOUND"


@dataclass
class TrackedPosition:
    """Tracked open position."""
    position_id: str
    direction: str        # "LONG" or "SHORT"
    entry_price: float
    stop_loss: float
    take_profit: float
    lot_size: float
    open_time_utc: datetime
    pattern: PatternType
    level: float


@dataclass
class _InsideBarPair:
    """Pending inside bar stop order pair."""
    buy_stop_id: Optional[str]
    sell_stop_id: Optional[str]
    created_at_candle: int
    level: float


class PositionManager:
    """Track and manage open positions."""

    def __init__(self):
        self._positions: Dict[str, TrackedPosition] = {}

    def add(self, position: TrackedPosition) -> None:
        self._positions[position.position_id] = position

    def remove(self, position_id: str) -> None:
        self._positions.pop(position_id, None)

    def get_open_positions(self) -> List[TrackedPosition]:
        return list(self._positions.values())

    def count(self) -> int:
        return len(self._positions)

    # ── Reconciler-compatible aliases ────────────────────────────────────────

    def get_position_ids(self) -> List[str]:
        return list(self._positions.keys())

    def get_position(self, position_id: str) -> Optional[TrackedPosition]:
        return self._positions.get(position_id)

    def add_position(self, position) -> None:
        """Add from a bot.api.models.Position (returned by reconcile)."""
        tracked = TrackedPosition(
            position_id=position.position_id,
            direction=position.direction,
            entry_price=position.entry_price,
            stop_loss=position.stop_loss,
            take_profit=position.take_profit,
            lot_size=position.volume,
            open_time_utc=position.open_time,
            pattern=PatternType.NONE,
            level=position.entry_price,
        )
        self._positions[tracked.position_id] = tracked

    def remove_position(self, position_id: str) -> None:
        self._positions.pop(position_id, None)


class Executor:
    """
    Execute orders via cTrader Open API.

    Pre-conditions are verified by the orchestrator. The executor performs a
    secondary validation, places the order, and logs all outcomes. In
    OBSERVE_ONLY mode no real orders are submitted.
    """

    HALTED_AUTH_FAILURE = False   # set True on TRADING_DISABLED error

    def __init__(self, config: Config, api_client, level_manager, risk_gates=None, state_writer=None):
        self.config = config
        self.api_client = api_client
        self.level_manager = level_manager
        self.risk_gates = risk_gates
        self.state_writer = state_writer
        self.position_manager = PositionManager()
        self._pending_pairs: List[_InsideBarPair] = []
        self._order_to_pair: Dict[str, _InsideBarPair] = {}
        self._pending_market_orders: Dict[str, dict] = {}
        self._closed_trades_today: List[dict] = []

    # ──────────────────────────────────────────────────────────────
    # Market order placement
    # ──────────────────────────────────────────────────────────────

    async def place_market_order(
        self,
        direction: int,
        lot_size: float,
        stop_loss_price: float,
        take_profit_price: float,
        pattern: PatternType,
        level: float,
    ) -> Optional[str]:
        """Place market order. Return position ID or None on failure."""
        # Pre-placement validation
        if lot_size <= 0:
            logger.info("[EXECUTOR] Skipped. Reason: INVALID_LOT_SIZE")
            return None

        # Get current spread (sync — uses cached bid/ask from tick stream)
        try:
            current_spread = self.api_client.get_current_spread()
        except Exception:
            current_spread = 0.0

        # We can't know the exact entry until filled, but validate the SL distance
        # that was computed by the orchestrator using the symbol spec
        direction_label = "LONG" if direction > 0 else "SHORT"
        tp_label = f"{take_profit_price:.2f}"
        sl_label = f"{stop_loss_price:.2f}"

        logger.info(
            "[EXECUTOR] Placing %s | Lot:%.2f SL:%s TP:%s | Pattern:%s Level:%.2f",
            direction_label, lot_size, sl_label, tp_label, pattern.name, level,
        )

        if self.config.observe_only:
            logger.info(
                "[EXECUTOR] OBSERVE_ONLY — would have placed %s %.2f lots. SL:%s TP:%s",
                direction_label, lot_size, sl_label, tp_label,
            )
            return None

        try:
            result = await self.api_client.place_market_order(
                direction="BUY" if direction > 0 else "SELL",
                lot_size=lot_size,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
            )
        except Exception as exc:
            self._handle_api_error(exc)
            return None

        if result is None:
            return None

        if isinstance(result, str):
            position_id = str(result)
            logger.info("[EXECUTOR] Order placed. Position ID: %s", position_id)

            tracked = TrackedPosition(
                position_id=position_id,
                direction=direction_label,
                entry_price=level,
                stop_loss=stop_loss_price,
                take_profit=take_profit_price,
                lot_size=lot_size,
                open_time_utc=datetime.now(timezone.utc),
                pattern=pattern,
                level=level,
            )
            self.position_manager.add(tracked)
            return position_id

        status = result.get("status") if isinstance(result, dict) else None
        if status == "filled":
            position_id = str(result["position_id"])
            logger.info("[EXECUTOR] Order placed. Position ID: %s", position_id)

            tracked = TrackedPosition(
                position_id=position_id,
                direction=direction_label,
                entry_price=level,
                stop_loss=stop_loss_price,
                take_profit=take_profit_price,
                lot_size=lot_size,
                open_time_utc=datetime.now(timezone.utc),
                pattern=pattern,
                level=level,
            )
            self.position_manager.add(tracked)
            return position_id

        if status == "accepted":
            order_id = str(result["order_id"])
            self._pending_market_orders[order_id] = {
                "direction": direction_label,
                "stop_loss": stop_loss_price,
                "take_profit": take_profit_price,
                "lot_size": lot_size,
                "pattern": pattern,
                "level": level,
                "created_at": datetime.now(timezone.utc),
            }
            logger.info("[EXECUTOR] Order accepted, awaiting fill. Order ID: %s", order_id)
            return f"order:{order_id}"

        return None

    # ──────────────────────────────────────────────────────────────
    # Inside bar stop orders
    # ──────────────────────────────────────────────────────────────

    async def place_inside_bar_orders(
        self,
        mother_bar_high: float,
        mother_bar_low: float,
        lot_size: float,
        level: float,
        current_candle_index: int,
        spread_buffer: float = 1.0,
        allowed_direction: Optional[int] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Place BUY_STOP + SELL_STOP for inside bar. Return (buy_id, sell_id)."""
        buy_stop_price = mother_bar_high + spread_buffer
        sell_stop_price = mother_bar_low - spread_buffer

        sl_for_buy = mother_bar_low - self.config.sl_min_dollars
        sl_for_sell = mother_bar_high + self.config.sl_min_dollars

        tp_for_buy = self.level_manager.next_level_from(buy_stop_price, direction=1)
        if tp_for_buy is None:
            sl_dist = buy_stop_price - sl_for_buy
            tp_for_buy = buy_stop_price + 2 * sl_dist

        tp_for_sell = self.level_manager.next_level_from(sell_stop_price, direction=-1)
        if tp_for_sell is None:
            sl_dist = sl_for_sell - sell_stop_price
            tp_for_sell = sell_stop_price - 2 * sl_dist

        logger.info(
            "[EXECUTOR] Placing INSIDE_BAR pair | Buy:%.2f Sell:%.2f | Level:%.2f",
            buy_stop_price, sell_stop_price, level,
        )

        if self.config.observe_only:
            logger.info(
                "[EXECUTOR] OBSERVE_ONLY — would have placed INSIDE_BAR stop orders at %.2f / %.2f",
                buy_stop_price, sell_stop_price,
            )
            return None, None

        buy_id: Optional[str] = None
        sell_id: Optional[str] = None

        if allowed_direction is None or allowed_direction > 0:
            try:
                result = await self.api_client.place_stop_order(
                    order_type="BUY_STOP",
                    trigger_price=buy_stop_price,
                    lot_size=lot_size,
                    stop_loss_price=sl_for_buy,
                    take_profit_price=tp_for_buy,
                )
                buy_id = str(result) if result is not None else None
            except Exception as exc:
                self._handle_api_error(exc)

        if allowed_direction is None or allowed_direction < 0:
            try:
                result = await self.api_client.place_stop_order(
                    order_type="SELL_STOP",
                    trigger_price=sell_stop_price,
                    lot_size=lot_size,
                    stop_loss_price=sl_for_sell,
                    take_profit_price=tp_for_sell,
                )
                sell_id = str(result) if result is not None else None
            except Exception as exc:
                self._handle_api_error(exc)

        pair = _InsideBarPair(
            buy_stop_id=buy_id,
            sell_stop_id=sell_id,
            created_at_candle=current_candle_index,
            level=level,
        )
        self._pending_pairs.append(pair)
        if buy_id:
            self._order_to_pair[buy_id] = pair
        if sell_id:
            self._order_to_pair[sell_id] = pair

        return buy_id, sell_id

    # ──────────────────────────────────────────────────────────────
    # Inside bar lifecycle
    # ──────────────────────────────────────────────────────────────

    async def check_pending_inside_bar_orders(self, current_candle_index: int) -> None:
        """Cancel inside bar stop orders once their configured candle lifetime expires."""
        expiry_candles = getattr(self.config, "inside_bar_expiry_candles", 2)
        expired = [
            p for p in self._pending_pairs
            if current_candle_index - p.created_at_candle >= expiry_candles
        ]
        for pair in expired:
            await self._cancel_order(pair.buy_stop_id)
            await self._cancel_order(pair.sell_stop_id)
            self._pending_pairs.remove(pair)
            logger.info("[EXECUTOR] Inside bar orders expired after %d candles. Cancelled.", expiry_candles)

    def consume_pending_market_order(self, order_id: Optional[str]) -> Optional[dict]:
        if order_id is None:
            return None
        return self._pending_market_orders.pop(order_id, None)

    async def on_order_filled(self, order_id: str, position_id: str, entry_price: float) -> None:
        """Handle order fill. Cancel companion inside bar order if applicable."""
        tracked = self.position_manager.get_position(position_id)
        if tracked is not None and entry_price > 0:
            tracked.entry_price = entry_price

        pair = self._order_to_pair.pop(order_id, None)
        if pair is not None:
            if pair.buy_stop_id == order_id:
                companion = pair.sell_stop_id
            else:
                companion = pair.buy_stop_id
            await self._cancel_order(companion)
            if pair in self._pending_pairs:
                self._pending_pairs.remove(pair)

    # ──────────────────────────────────────────────────────────────
    # Position close handler
    # ──────────────────────────────────────────────────────────────

    async def on_position_closed(self, position_id: str, close_price: float, pnl: float) -> None:
        """Handle position close event from API stream."""
        position = self.position_manager._positions.get(position_id)
        if self.risk_gates:
            self.risk_gates.record_trade_closed(pnl)

        if position is None:
            logger.warning("[EXECUTOR] Closed position %s not found in tracker.", position_id)
            self._closed_trades_today.append({
                "position_id": position_id,
                "direction": "UNKNOWN",
                "entry_price": 0.0,
                "close_price": close_price,
                "stop_loss": 0.0,
                "take_profit": 0.0,
                "lot_size": 0.0,
                "pnl": pnl,
                "pattern": PatternType.NONE.name,
                "level": 0.0,
                "close_time_utc": datetime.now(timezone.utc).isoformat(),
            })
            if self.state_writer:
                await self.state_writer.write(
                    open_positions=self.position_manager.get_open_positions(),
                    closed_trades=self._closed_trades_today,
                )
            return

        self.position_manager.remove(position_id)

        pnl_sign = "+" if pnl >= 0 else "-"
        logger.info(
            "[TRADE CLOSED] ID:%s %s | Entry:%.2f SL:%.2f TP:%.2f | Close:%.2f | P&L:%s£%.2f | Pattern:%s | Level:%.2f",
            position_id, position.direction,
            position.entry_price, position.stop_loss, position.take_profit,
            close_price, pnl_sign, abs(pnl), position.pattern.name, position.level,
        )

        self._closed_trades_today.append({
            "position_id": position_id,
            "direction": position.direction,
            "entry_price": position.entry_price,
            "close_price": close_price,
            "stop_loss": position.stop_loss,
            "take_profit": position.take_profit,
            "lot_size": position.lot_size,
            "pnl": pnl,
            "pattern": position.pattern.name,
            "level": position.level,
            "close_time_utc": datetime.now(timezone.utc).isoformat(),
        })

        if self.state_writer:
            await self.state_writer.write(
                open_positions=self.position_manager.get_open_positions(),
                closed_trades=self._closed_trades_today,
            )

    # ──────────────────────────────────────────────────────────────
    # SL modification guard
    # ──────────────────────────────────────────────────────────────

    def validate_sl_modification(self, position: TrackedPosition, new_sl: float) -> bool:
        """Return False and log if the new SL would move further from entry."""
        if position.direction == "LONG" and new_sl < position.stop_loss:
            logger.warning("[EXECUTOR] SL modification rejected: moving SL further from entry.")
            return False
        if position.direction == "SHORT" and new_sl > position.stop_loss:
            logger.warning("[EXECUTOR] SL modification rejected: moving SL further from entry.")
            return False
        return True

    def _get_trailing_reference_price(
        self,
        position: TrackedPosition,
        fallback_price: float,
    ) -> float:
        """
        Use the executable side of market for trailing decisions.

        Long positions are exited on bid, short positions on ask. Using mid-price
        makes short trailing especially prone to generating invalid broker stops.
        """
        get_current_quote = getattr(self.api_client, "get_current_quote", None)
        if not callable(get_current_quote):
            return fallback_price

        bid, ask = get_current_quote()
        if position.direction == "LONG" and bid is not None:
            return bid
        if position.direction == "SHORT" and ask is not None:
            return ask
        return fallback_price

    def validate_sl_against_market(
        self,
        position: TrackedPosition,
        new_sl: float,
        symbol_spec,
    ) -> bool:
        """
        Reject stops that are still on the wrong side of the live market.

        cTrader requires long SL <= current bid and short SL >= current ask.
        Keep a one-tick buffer to avoid repeated rejections on rounding/latency.
        """
        get_current_quote = getattr(self.api_client, "get_current_quote", None)
        if not callable(get_current_quote):
            return True

        bid, ask = get_current_quote()
        tick_size = 10 ** (-getattr(symbol_spec, "digits", 2))

        if position.direction == "LONG" and bid is not None:
            max_stop = round(bid - tick_size, symbol_spec.digits)
            if new_sl > max_stop:
                logger.warning(
                    "[EXECUTOR] SL modification rejected: LONG stop %.2f exceeds bid-safe level %.2f (bid %.2f).",
                    new_sl,
                    max_stop,
                    bid,
                )
                return False

        if position.direction == "SHORT" and ask is not None:
            min_stop = round(ask + tick_size, symbol_spec.digits)
            if new_sl < min_stop:
                logger.warning(
                    "[EXECUTOR] SL modification rejected: SHORT stop %.2f is below ask-safe level %.2f (ask %.2f).",
                    new_sl,
                    min_stop,
                    ask,
                )
                return False

        return True

    # ──────────────────────────────────────────────────────────────
    # Trailing stop management
    # ──────────────────────────────────────────────────────────────

    async def update_trailing_stops(
        self,
        current_price: float,
        account_balance: float,
        symbol_spec,
    ) -> None:
        """
        Evaluate and apply trailing stops for all open positions.

        Called on every tick from the orchestrator. For each open position:
        1. Compute the risk amount from lot_size and original SL distance.
        2. Ask evaluate_trailing_stop() for a new SL (or None).
        3. Validate the new SL doesn't move further from entry (hard rule).
        4. Send amend_position_sltp() to broker.
        5. Update local TrackedPosition.stop_loss.

        In OBSERVE_ONLY mode, only logs — never sends to broker.
        """
        from bot.risk.trailing_stop import evaluate_trailing_stop

        positions = self.position_manager.get_open_positions()
        for pos in positions:
            try:
                # Reconstruct risk_amount from original SL distance
                sl_dist = abs(pos.entry_price - pos.stop_loss)
                risk_amount = sl_dist * pos.lot_size * symbol_spec.lot_size
                if risk_amount <= 0:
                    continue

                trailing_price = self._get_trailing_reference_price(
                    position=pos,
                    fallback_price=current_price,
                )

                result = evaluate_trailing_stop(
                    direction=pos.direction,
                    entry_price=pos.entry_price,
                    current_price=trailing_price,
                    current_sl=pos.stop_loss,
                    volume_lots=pos.lot_size,
                    contract_size_oz=symbol_spec.lot_size,
                    risk_amount=risk_amount,
                )

                if result is None:
                    continue

                # Hard rule: validate SL is not moving further from entry
                if not self.validate_sl_modification(pos, result.new_sl):
                    continue
                if not self.validate_sl_against_market(pos, result.new_sl, symbol_spec):
                    continue

                logger.info(
                    "[TRAILING] Position %s %s: %s SL %.2f → %.2f",
                    pos.position_id, pos.direction, result.reason,
                    pos.stop_loss, result.new_sl,
                )

                if self.config.observe_only:
                    continue

                ok = await self.api_client.amend_position_sltp(
                    position_id=pos.position_id,
                    stop_loss=result.new_sl,
                )
                if ok:
                    pos.stop_loss = result.new_sl

            except Exception as exc:
                logger.error("[TRAILING] Error processing position %s: %s",
                             pos.position_id, exc)

    # ──────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────

    async def _cancel_order(self, order_id: Optional[str]) -> None:
        if order_id is None:
            return
        try:
            await self.api_client.cancel_order(order_id)
        except Exception as exc:
            self._handle_api_error(exc)

    def _handle_api_error(self, exc: Exception) -> None:
        msg = str(exc)
        if _API_ERROR_TRADING_DISABLED in msg:
            logger.critical("[EXECUTOR] TRADING_DISABLED — halting all trade attempts.")
            Executor.HALTED_AUTH_FAILURE = True
        elif _API_ERROR_NOT_ENOUGH_MONEY in msg:
            logger.error("[EXECUTOR] NOT_ENOUGH_MONEY — skipping trade.")
        elif _API_ERROR_MARKET_CLOSED in msg:
            logger.warning("[EXECUTOR] MARKET_CLOSED — skipping trade.")
        elif _API_ERROR_POSITION_NOT_FOUND in msg:
            logger.warning("[EXECUTOR] POSITION_NOT_FOUND — removing from tracker if present.")
        else:
            logger.error("[EXECUTOR] API error: %s", msg)
