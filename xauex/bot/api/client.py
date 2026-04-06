"""
cTrader Open API client.

Uses a pure-asyncio TCP transport (proto_transport.py) — no Twisted dependency.
All public methods are async coroutines compatible with asyncio.run().

Message handling notes (from official docs):
- Use direct protobuf class instantiation: ProtoOANewOrderReq() — NOT Protobuf.get()
- Responses to trading requests arrive as ProtoOAExecutionEvent (payloadType=2126)
- Spot price events (payloadType=2131) may carry partial bid/ask updates; cache last value
- Volume in protocol = lot_size_in_std_lots * contract_size_oz * 100
  e.g. 1.0 lot XAUUSD = 1.0 * 100 * 100 = 10000 in protocol
- ProtoOAReconcileReq/Res: use on startup to get all open positions + pending orders
"""

import asyncio
import logging
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, List, Optional

from dotenv import dotenv_values

from config import Config
from bot.api.models import Account, Position, SymbolSpec
from bot.api.proto_transport import CTraderTransport

logger = logging.getLogger(__name__)

# ─── cTrader enum constants ────────────────────────────────────────────────────
# ProtoOATrendbarPeriod
_PERIOD_MAP = {
    "M1": 1, "M2": 2, "M3": 3, "M4": 4, "M5": 5,
    "M10": 6, "M15": 7, "M30": 8,
    "H1": 9, "H4": 10, "H12": 11,
    "D1": 12, "WEEKLY": 13, "MONTHLY": 14,
}

# ProtoOATradeSide
_SIDE_BUY  = 1
_SIDE_SELL = 2

# ProtoOAOrderType
_ORDER_MARKET = 1
_ORDER_STOP   = 3

# ProtoOAExecutionType
_EXEC_ORDER_ACCEPTED     = 2
_EXEC_ORDER_FILLED       = 3
_EXEC_ORDER_REJECTED     = 7
_EXEC_ORDER_PARTIAL_FILL = 11

# ProtoOAPayloadType for inbound events
_PT_EXECUTION_EVENT    = 2126
_PT_SPOT_EVENT         = 2131
_PT_ERROR_RES          = 2142
_PT_ORDER_ERROR        = 2151
_PT_ACCOUNT_DISCONNECT = 2147


def _ts_to_utc(ts_ms: int) -> datetime:
    """Convert millisecond timestamp to UTC datetime."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)


def _proto_volume(lot_size_std: float, contract_size_oz: float) -> int:
    """Convert standard lots to cTrader protocol volume units.

    Protocol: volume in 0.01 of a unit. For XAUUSD:
        1 std lot = 100 oz (contract_size_oz) = 100 units = 10000 in protocol.
    """
    return int(lot_size_std * contract_size_oz * 100)


def _lots_from_proto(proto_volume: int, contract_size_oz: float) -> float:
    """Convert protocol volume units back to standard lots."""
    return proto_volume / (contract_size_oz * 100)


def _atomic_update_env(path: str, updates: dict[str, str]) -> None:
    """Atomically update selected keys in a dotenv file."""
    current = dotenv_values(path)
    current.update(updates)
    dirpath = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=dirpath, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            for key, value in current.items():
                if value is None:
                    value = ""
                f.write(f"{key}={value}\n")
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class ApiClient:
    """
    cTrader Open API client (pure asyncio, no Twisted).

    Uses CTraderTransport for TCP/SSL with Int32 framing.

    Lifecycle:
        connect() → get_symbol_spec() → subscribe_to_ticks() → [trading] → disconnect()

    Token refresh:
        - On startup: refresh if within 5 min of expiry
        - Before every order: refresh if within 5 min of expiry
        - On refresh failure: propagate exception so caller can halt

    Volume units:
        All public API methods accept/return volumes in **standard lots**.
        Internal conversion to protocol units (0.01 of a unit) is handled here.
    """

    def __init__(self, config: Config):
        self.config = config
        self._transport: Optional[CTraderTransport] = None
        self._symbol_id: Optional[int] = None
        self._symbol_spec: Optional[SymbolSpec] = None
        self._connected = False

        # Pending request/response futures keyed by clientMsgId
        self._message_futures: dict[str, asyncio.Future] = {}

        # Queues for unsolicited events
        self._tick_event_queue: asyncio.Queue = asyncio.Queue()
        self._execution_event_queue: asyncio.Queue = asyncio.Queue()

        # Last known bid/ask (spot events may carry partial updates)
        self._last_bid: Optional[float] = None
        self._last_ask: Optional[float] = None

        # Callbacks set by orchestrator
        self._tick_callback: Optional[Callable] = None
        self._execution_callback: Optional[Callable] = None

    # ──────────────────────────────────────────────────────────────────────────
    # Connection lifecycle
    # ──────────────────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Connect to cTrader, run startup auth, subscribe to account."""
        self._message_futures = {}
        self._transport = CTraderTransport(
            self.config.ctrader_host,
            self.config.ctrader_port,
        )
        self._transport.set_message_callback(self._route_message)
        self._transport.set_disconnect_callback(self._on_disconnect)

        await self._transport.connect(timeout=30)
        self._connected = True

        await self._app_auth()
        await self._account_auth()
        logger.info("[API] Connected and authenticated to account %s",
                    self.config.ctrader_account_id)

    async def disconnect(self) -> None:
        """Close the transport."""
        if self._transport:
            await self._transport.close()
        self._connected = False
        logger.info("[API] Disconnected.")

    def _on_disconnect(self, reason: str) -> None:
        self._connected = False
        logger.warning("[API] Disconnected: %s", reason)

    def is_connected(self) -> bool:
        return self._connected

    # ──────────────────────────────────────────────────────────────────────────
    # Message routing
    # ──────────────────────────────────────────────────────────────────────────

    def _route_message(self, envelope) -> None:
        """Called by transport on every inbound ProtoMessage envelope."""
        from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoMessage

        # Correlate request/response via clientMsgId
        msg_id = getattr(envelope, 'clientMsgId', None)
        if msg_id and str(msg_id) in self._message_futures:
            fut = self._message_futures.pop(str(msg_id))
            inner = self._extract(envelope)
            if not fut.done():
        # schedule set_result on the event loop (transport recv runs in same asyncio loop)
                asyncio.get_running_loop().call_soon(fut.set_result, inner)
            return

        # Route unsolicited events
        pt = getattr(envelope, 'payloadType', None)
        if pt == _PT_SPOT_EVENT:
            try:
                self._tick_event_queue.put_nowait(envelope)
            except asyncio.QueueFull:
                pass
        elif pt == _PT_EXECUTION_EVENT:
            try:
                self._execution_event_queue.put_nowait(envelope)
            except asyncio.QueueFull:
                pass
        elif pt == _PT_ACCOUNT_DISCONNECT:
            logger.warning("[API] Account disconnected by server — will re-authenticate")
        elif pt == _PT_ERROR_RES:
            inner = self._extract(envelope)
            logger.error("[API] Server error: %s — %s",
                         getattr(inner, 'errorCode', '?'),
                         getattr(inner, 'description', ''))

    @staticmethod
    def _extract(envelope):
        """Deserialize the payload inside a ProtoMessage envelope."""
        from ctrader_open_api import Protobuf
        return Protobuf.extract(envelope)

    # ──────────────────────────────────────────────────────────────────────────
    # Request/response helper
    # ──────────────────────────────────────────────────────────────────────────

    async def _send_and_wait(self, message, timeout: float = 10.0):
        """Send a message and await the deserialized response."""
        loop = asyncio.get_running_loop()
        msg_id = str(uuid.uuid4())[:8]
        fut: asyncio.Future = loop.create_future()
        self._message_futures[msg_id] = fut

        await self._transport.send(message, client_msg_id=msg_id)
        return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)

    # ──────────────────────────────────────────────────────────────────────────
    # Authentication
    # ──────────────────────────────────────────────────────────────────────────

    async def _app_auth(self) -> None:
        """Authenticate the application (client ID + secret)."""
        from ctrader_open_api.messages.OpenApiMessages_pb2 import (
            ProtoOAApplicationAuthReq,
        )
        req = ProtoOAApplicationAuthReq()
        req.clientId     = self.config.ctrader_client_id
        req.clientSecret = self.config.ctrader_client_secret
        await self._send_and_wait(req)

    async def _account_auth(self) -> None:
        """Authenticate the trading account (access token)."""
        from ctrader_open_api.messages.OpenApiMessages_pb2 import (
            ProtoOAAccountAuthReq,
        )
        req = ProtoOAAccountAuthReq()
        req.ctidTraderAccountId = int(self.config.ctrader_account_id)
        req.accessToken         = self.config.ctrader_access_token
        await self._send_and_wait(req)

    # ──────────────────────────────────────────────────────────────────────────
    # Token refresh
    # ──────────────────────────────────────────────────────────────────────────

    async def refresh_token_if_needed(self) -> bool:
        """Refresh OAuth token if within 5 minutes of expiry. Returns True if refreshed."""
        now = int(time.time())
        if self.config.ctrader_token_expiry - now > 300:
            return False

        logger.info("[API] Token near expiry — refreshing.")
        try:
            import aiohttp as _aiohttp
            timeout = _aiohttp.ClientTimeout(total=15)
            async with _aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    "https://connect.spotware.com/apps/token",
                    data={
                        "grant_type":    "refresh_token",
                        "refresh_token": self.config.ctrader_refresh_token,
                        "client_id":     self.config.ctrader_client_id,
                        "client_secret": self.config.ctrader_client_secret,
                    },
                ) as resp:
                    resp.raise_for_status()
                    result = await resp.json()

            access_token  = result["access_token"]
            refresh_token = result["refresh_token"]
            expiry        = int(time.time()) + result["expires_in"]

            env_path = ".env"
            _atomic_update_env(
                env_path,
                {
                    "CTRADER_ACCESS_TOKEN": access_token,
                    "CTRADER_REFRESH_TOKEN": refresh_token,
                    "CTRADER_TOKEN_EXPIRY": str(expiry),
                },
            )

            self.config.ctrader_access_token  = access_token
            self.config.ctrader_refresh_token = refresh_token
            self.config.ctrader_token_expiry  = expiry

            logger.info("[API] Token refreshed successfully.")
            return True
        except Exception as exc:
            logger.critical("[API] Token refresh failed: %s", exc)
            raise

    # ──────────────────────────────────────────────────────────────────────────
    # Account and symbol data
    # ──────────────────────────────────────────────────────────────────────────

    async def get_account(self) -> Account:
        """Fetch current account balance and currency."""
        from ctrader_open_api.messages.OpenApiMessages_pb2 import (
            ProtoOATraderReq,
            ProtoOAAssetListReq,
        )
        req = ProtoOATraderReq()
        req.ctidTraderAccountId = int(self.config.ctrader_account_id)
        res = await self._send_and_wait(req)

        trader    = res.trader
        money_dig = getattr(trader, 'moneyDigits', 2)
        divisor   = 10 ** money_dig
        balance   = trader.balance / divisor

        # Resolve depositAssetId → currency name (e.g. asset 6 = GBP)
        currency = "USD"
        try:
            asset_req = ProtoOAAssetListReq()
            asset_req.ctidTraderAccountId = int(self.config.ctrader_account_id)
            asset_res = await self._send_and_wait(asset_req, timeout=10)
            for asset in asset_res.asset:
                if asset.assetId == trader.depositAssetId:
                    currency = asset.name
                    break
        except Exception:
            pass  # non-critical; use default

        return Account(
            balance=balance,
            equity=balance,
            currency=currency,
            margin_available=0.0,
        )

    async def get_symbol_spec(self, symbol: str = "XAUUSD") -> SymbolSpec:
        """Fetch XAUUSD contract spec and cache it on self._symbol_spec."""
        from ctrader_open_api.messages.OpenApiMessages_pb2 import (
            ProtoOASymbolsListReq,
            ProtoOASymbolByIdReq,
        )

        # 1. Find symbol ID from the account's symbol list
        list_req = ProtoOASymbolsListReq()
        list_req.ctidTraderAccountId = int(self.config.ctrader_account_id)
        list_res = await self._send_and_wait(list_req)

        symbol_id = None
        for sym in list_res.symbol:
            if sym.symbolName == symbol:
                symbol_id = sym.symbolId
                break

        if symbol_id is None:
            raise ValueError(f"Symbol {symbol} not found in account symbol list")

        self._symbol_id = symbol_id

        # 2. Fetch full symbol specification
        spec_req = ProtoOASymbolByIdReq()
        spec_req.ctidTraderAccountId = int(self.config.ctrader_account_id)
        spec_req.symbolId.append(symbol_id)
        spec_res = await self._send_and_wait(spec_req)

        sym_data = spec_res.symbol[0]

        # In cTrader Open API, lotSize is stored in centunits (×100 scale):
        #   lotSize = contract_size_oz × 100
        # For XAUUSD: lotSize=10000 → contract_size = 10000/100 = 100 oz per std lot
        # minVolume/stepVolume are in protocol units (0.01 of a lot):
        #   100 protocol units = 1.00 std lot
        # Conversion: std_lots = proto_vol / (contract_size_oz × 100)
        contract_size = float(sym_data.lotSize) / 100.0  # oz per standard lot (e.g. 100)
        factor        = contract_size * 100.0             # protocol units per std lot

        spec = SymbolSpec(
            symbol=symbol,
            lot_size=contract_size,
            volume_min=sym_data.minVolume  / factor,
            volume_max=sym_data.maxVolume  / factor,
            volume_step=sym_data.stepVolume / factor,
            digits=sym_data.digits,
            pip_value=10 ** (-sym_data.digits),
        )
        self._symbol_spec = spec
        return spec

    async def get_h1_bars(self, count: int = 200) -> list:
        """Fetch last N H1 bars for XAUUSD."""
        return await self._get_trendbars("H1", count)

    async def get_trendbar(self, period: str = "WEEKLY", count: int = 2) -> list:
        """Fetch W1 or MN1 bars for XAUUSD."""
        return await self._get_trendbars(period, count)

    async def _get_trendbars(self, period: str, count: int) -> list:
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAGetTrendbarsReq
        if self._symbol_id is None:
            raise RuntimeError("Call get_symbol_spec() before requesting bars")

        period_int = _PERIOD_MAP.get(period)
        if period_int is None:
            raise ValueError(f"Unknown period: {period}")

        _PERIOD_MINUTES = {
            "M1": 1, "M2": 2, "M3": 3, "M4": 4, "M5": 5,
            "M10": 10, "M15": 15, "M30": 30,
            "H1": 60, "H4": 240, "H12": 720,
            "D1": 1440, "WEEKLY": 10080, "MONTHLY": 43200,
        }
        period_minutes = _PERIOD_MINUTES.get(period, 60)
        now_ms = int(time.time() * 1000)
        # Use a wider window than the raw period count because cTrader omits
        # weekends, daily maintenance gaps, and other non-trading intervals.
        # Without this buffer, lower intraday periods such as M15 can return far
        # fewer than the requested number of closed bars, which breaks long-lookback
        # indicators such as the 200 EMA.
        lookback_multiplier = 4
        from_ms = now_ms - (count + 1) * period_minutes * 60 * 1000 * lookback_multiplier

        req = ProtoOAGetTrendbarsReq()
        req.ctidTraderAccountId = int(self.config.ctrader_account_id)
        req.symbolId      = self._symbol_id
        req.period        = period_int
        req.fromTimestamp = from_ms
        req.toTimestamp   = now_ms
        req.count         = count

        res = await self._send_and_wait(req, timeout=20)

        bars = []
        for bar in res.trendbar:
            # Prices are stored as deltas from the low price in 1/100000 units
            low_price   = bar.low / 100_000.0
            open_price  = low_price + bar.deltaOpen  / 100_000.0
            high_price  = low_price + bar.deltaHigh  / 100_000.0
            close_price = low_price + bar.deltaClose / 100_000.0
            open_time   = _ts_to_utc(bar.utcTimestampInMinutes * 60 * 1000)
            bars.append({
                "open_time": open_time,
                "open":  open_price,
                "high":  high_price,
                "low":   low_price,
                "close": close_price,
            })
        return bars

    # ──────────────────────────────────────────────────────────────────────────
    # Reconciliation (critical for restart recovery)
    # ──────────────────────────────────────────────────────────────────────────

    async def reconcile(self) -> tuple[list[Position], list[dict]]:
        """
        Fetch all currently open positions and pending orders from the broker.

        Returns (open_positions, pending_orders).
        Call this on startup and after reconnection to sync local state.
        """
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAReconcileReq
        if self._symbol_spec is None:
            raise RuntimeError("Call get_symbol_spec() before reconcile()")

        req = ProtoOAReconcileReq()
        req.ctidTraderAccountId = int(self.config.ctrader_account_id)
        res = await self._send_and_wait(req, timeout=15)

        contract_size = self._symbol_spec.lot_size

        positions: list[Position] = []
        for p in res.position:
            direction = "LONG" if p.tradeData.tradeSide == _SIDE_BUY else "SHORT"
            vol_lots  = _lots_from_proto(p.tradeData.volume, contract_size)
            positions.append(Position(
                position_id=str(p.positionId),
                symbol=self._symbol_spec.symbol,
                direction=direction,
                volume=vol_lots,
                entry_price=p.price,
                current_price=p.price,
                unrealised_pnl=0.0,
                open_time=_ts_to_utc(p.tradeData.openTimestamp),
                stop_loss=p.stopLoss  if p.HasField('stopLoss')  else 0.0,
                take_profit=p.takeProfit if p.HasField('takeProfit') else 0.0,
            ))

        pending_orders: list[dict] = []
        for o in res.order:
            pending_orders.append({
                "order_id":   str(o.orderId),
                "direction":  "BUY" if o.tradeData.tradeSide == _SIDE_BUY else "SELL",
                "volume":     _lots_from_proto(o.tradeData.volume, contract_size),
                "stop_price": getattr(o, 'stopPrice', 0.0),
                "stop_loss":  o.stopLoss  if hasattr(o, 'stopLoss')  else 0.0,
                "take_profit":o.takeProfit if hasattr(o, 'takeProfit') else 0.0,
            })

        logger.info("[API] Reconcile: %d positions, %d pending orders",
                    len(positions), len(pending_orders))
        return positions, pending_orders

    async def get_open_positions(self) -> list[Position]:
        """Convenience wrapper — returns only the open positions from reconcile."""
        positions, _ = await self.reconcile()
        return positions

    # ──────────────────────────────────────────────────────────────────────────
    # Tick subscription
    # ──────────────────────────────────────────────────────────────────────────

    async def subscribe_to_ticks(self, callback: Callable) -> None:
        """Subscribe to XAUUSD live spot price ticks. callback(mid, ts) is async."""
        from ctrader_open_api.messages.OpenApiMessages_pb2 import (
            ProtoOASubscribeSpotsReq,
        )
        self._tick_callback = callback
        req = ProtoOASubscribeSpotsReq()
        req.ctidTraderAccountId = int(self.config.ctrader_account_id)
        req.symbolId.append(self._symbol_id)
        await self._send_and_wait(req)

        asyncio.create_task(self._dispatch_ticks())
        asyncio.create_task(self._dispatch_executions())
        logger.info("[API] Subscribed to XAUUSD tick stream.")

    async def subscribe_ticks(self, symbol: str, callback: Callable) -> None:
        """Alias matching orchestrator call signature: subscribe_ticks(symbol, callback)."""
        await self.subscribe_to_ticks(callback)

    def get_current_spread(self) -> float:
        """Return current bid/ask spread in price units (not async — uses cached values)."""
        if self._last_bid is not None and self._last_ask is not None:
            return abs(self._last_ask - self._last_bid)
        return 0.0

    def get_current_quote(self) -> tuple[Optional[float], Optional[float]]:
        """Return the latest cached bid/ask quote, if available."""
        return self._last_bid, self._last_ask

    async def _dispatch_ticks(self) -> None:
        """Drain tick queue and invoke callback with mid price."""
        from ctrader_open_api import Protobuf
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOASpotEvent

        while True:
            raw_msg = await self._tick_event_queue.get()
            try:
                event = Protobuf.extract(raw_msg)
                # Spot events may carry partial updates (only bid OR ask)
                if event.HasField('bid'):
                    self._last_bid = event.bid / 100_000.0
                if event.HasField('ask'):
                    self._last_ask = event.ask / 100_000.0

                if self._last_bid is None or self._last_ask is None:
                    continue  # wait for both sides

                mid = (self._last_bid + self._last_ask) / 2.0
                ts  = _ts_to_utc(event.timestamp) if event.timestamp else datetime.now(timezone.utc)
                if self._tick_callback:
                    await self._tick_callback(mid, ts)
            except Exception as exc:
                logger.error("[API] Tick dispatch error: %s", exc)

    async def _dispatch_executions(self) -> None:
        """Drain execution event queue and invoke callback for unsolicited fills/closes."""
        from ctrader_open_api import Protobuf

        while True:
            raw_msg = await self._execution_event_queue.get()
            try:
                event = Protobuf.extract(raw_msg)
                if self._execution_callback:
                    await self._execution_callback(event)
            except Exception as exc:
                logger.error("[API] Execution dispatch error: %s", exc)

    def set_execution_callback(self, callback: Callable) -> None:
        """Register callback for unsolicited execution events (e.g. SL hit by server)."""
        self._execution_callback = callback

    # ──────────────────────────────────────────────────────────────────────────
    # Order management
    # ──────────────────────────────────────────────────────────────────────────

    def _require_symbol_spec(self) -> SymbolSpec:
        if self._symbol_spec is None:
            raise RuntimeError("Call get_symbol_spec() before placing orders")
        return self._symbol_spec

    @staticmethod
    def _order_error_details(event) -> tuple[Optional[str], str]:
        """Extract broker error details from execution or order-error responses."""
        try:
            has_error = event.HasField("errorCode")
        except Exception:
            has_error = False

        error_code = getattr(event, "errorCode", None) if has_error else None
        description = getattr(event, "description", "") or ""
        return (str(error_code), description) if error_code else (None, description)

    async def place_market_order(
        self,
        direction: str,
        lot_size: float,
        stop_loss_price: float,
        take_profit_price: float,
    ) -> Optional[str]:
        """
        Place a market order. Returns position ID string or None on failure.

        lot_size: in standard lots (e.g. 0.05)
        stop_loss_price / take_profit_price: absolute prices
        """
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOANewOrderReq

        spec = self._require_symbol_spec()
        proto_vol = _proto_volume(lot_size, spec.lot_size)
        if proto_vol <= 0:
            logger.error("[API] place_market_order: invalid volume %s lots → %s proto",
                         lot_size, proto_vol)
            return None

        try:
            await self.refresh_token_if_needed()

            req = ProtoOANewOrderReq()
            req.ctidTraderAccountId = int(self.config.ctrader_account_id)
            req.symbolId   = self._symbol_id
            req.orderType  = _ORDER_MARKET
            req.tradeSide  = _SIDE_BUY if direction == "BUY" else _SIDE_SELL
            req.volume     = proto_vol
            current_price = self._last_ask if direction == "BUY" else self._last_bid
            if current_price is None:
                current_price = (float(stop_loss_price) + float(take_profit_price)) / 2.0

            req.relativeStopLoss = int(round(abs(current_price - float(stop_loss_price)) * 100_000))
            req.relativeTakeProfit = int(round(abs(float(take_profit_price) - current_price) * 100_000))
            req.label      = "XAUEX"

            event = await self._send_and_wait(req, timeout=10)

            if hasattr(event, "executionType") and event.executionType == _EXEC_ORDER_FILLED and event.HasField('position'):
                pos_id = str(event.position.positionId)
                logger.info("[API] Market order filled. Position ID: %s", pos_id)
                return {"status": "filled", "position_id": pos_id}

            if hasattr(event, "executionType") and event.executionType == _EXEC_ORDER_ACCEPTED and event.HasField('order'):
                order_id = str(event.order.orderId)
                logger.debug("[API] Market order accepted. Order ID: %s", order_id)
                return {"status": "accepted", "order_id": order_id}

            error_code, description = self._order_error_details(event)
            if error_code:
                logger.error("[API] Order rejected: %s %s", error_code, description)
            elif hasattr(event, "executionType"):
                logger.error("[API] Unexpected market order response. executionType=%s", event.executionType)
            else:
                logger.error("[API] Unexpected market order response: %s", type(event).__name__)
            return None

        except Exception as exc:
            logger.error("[API] Market order error: %s", exc)
            return None

    async def place_stop_order(
        self,
        order_type: str,
        trigger_price: float,
        lot_size: float,
        stop_loss_price: float,
        take_profit_price: float,
    ) -> Optional[str]:
        """
        Place a BUY_STOP or SELL_STOP pending order.
        Returns order ID string or None on failure.
        """
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOANewOrderReq

        spec = self._require_symbol_spec()
        proto_vol = _proto_volume(lot_size, spec.lot_size)

        try:
            await self.refresh_token_if_needed()

            req = ProtoOANewOrderReq()
            req.ctidTraderAccountId = int(self.config.ctrader_account_id)
            req.symbolId   = self._symbol_id
            req.orderType  = _ORDER_STOP
            req.tradeSide  = _SIDE_BUY if order_type == "BUY_STOP" else _SIDE_SELL
            req.volume     = proto_vol
            req.stopPrice  = float(trigger_price)
            req.stopLoss   = float(stop_loss_price)
            req.takeProfit = float(take_profit_price)
            req.label      = "XAUEX"

            event = await self._send_and_wait(req, timeout=10)

            if hasattr(event, "executionType") and event.executionType in (_EXEC_ORDER_ACCEPTED,) and event.HasField('order'):
                order_id = str(event.order.orderId)
                logger.debug("[API] Stop order accepted. Order ID: %s", order_id)
                return order_id

            error_code, description = self._order_error_details(event)
            if error_code:
                logger.error("[API] Stop order rejected: %s %s", error_code, description)
            elif not hasattr(event, "executionType"):
                logger.error("[API] Unexpected stop order response: %s", type(event).__name__)
            return None

        except Exception as exc:
            logger.error("[API] Stop order error: %s", exc)
            return None

    async def amend_position_sltp(
        self,
        position_id: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> bool:
        """
        Amend the SL and/or TP of an open position.

        Uses ProtoOAAmendPositionSLTPReq.
        stop_loss / take_profit: absolute prices (not relative).
        Returns True on success.
        """
        from ctrader_open_api.messages.OpenApiMessages_pb2 import (
            ProtoOAAmendPositionSLTPReq,
        )
        try:
            req = ProtoOAAmendPositionSLTPReq()
            req.ctidTraderAccountId = int(self.config.ctrader_account_id)
            req.positionId = int(position_id)
            if stop_loss is not None:
                req.stopLoss = float(stop_loss)
            if take_profit is not None:
                req.takeProfit = float(take_profit)

            event = await self._send_and_wait(req, timeout=10)
            error_code, description = self._order_error_details(event)
            if error_code:
                logger.error("[API] Amend SL/TP rejected: %s %s", error_code, description)
                return False

            logger.info("[API] Amended position %s SL=%.2f TP=%.2f",
                        position_id,
                        stop_loss  or 0.0,
                        take_profit or 0.0)
            return True
        except Exception as exc:
            logger.error("[API] amend_position_sltp error: %s", exc)
            return False

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending stop order."""
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOACancelOrderReq
        try:
            req = ProtoOACancelOrderReq()
            req.ctidTraderAccountId = int(self.config.ctrader_account_id)
            req.orderId = int(order_id)
            await self._send_and_wait(req, timeout=10)
            return True
        except Exception as exc:
            logger.error("[API] Cancel order error: %s", exc)
            return False

    async def close_position(self, position_id: str, volume_lots: float) -> bool:
        """
        Close an open position at market.

        volume_lots: the volume to close in standard lots (use position.volume for full close).
        Must NOT be 0 — the protocol requires a valid volume.
        """
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAClosePositionReq

        spec = self._require_symbol_spec()
        proto_vol = _proto_volume(volume_lots, spec.lot_size)
        if proto_vol <= 0:
            logger.error("[API] close_position: invalid volume %s lots", volume_lots)
            return False

        try:
            req = ProtoOAClosePositionReq()
            req.ctidTraderAccountId = int(self.config.ctrader_account_id)
            req.positionId = int(position_id)
            req.volume     = proto_vol

            event = await self._send_and_wait(req, timeout=10)
            error_code, description = self._order_error_details(event)
            if error_code:
                logger.error("[API] Close position rejected: %s %s", error_code, description)
                return False

            logger.info("[API] Position %s closed.", position_id)
            return True
        except Exception as exc:
            logger.error("[API] Close position error: %s", exc)
            return False
