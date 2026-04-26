"""Typed broker boundary for order submission."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class OrderIntent:
    direction: str
    lot_size: float
    stop_loss_price: float
    take_profit_price: float
    entry_price: float
    owner: str
    pattern: str
    correlation_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
    order_type: str = "MARKET"
    symbol: str = "XAUUSD"
    bid: float | None = None
    ask: float | None = None


@dataclass(frozen=True)
class OrderAck:
    status: str
    order_id: str | None = None
    position_id: str | None = None
    reason: str | None = None
    raw_result: Any = None


class BrokerAdapter(Protocol):
    async def place_market_order(self, intent: OrderIntent) -> OrderAck:
        ...


class CTraderBrokerAdapter:
    """Adapter that preserves the existing cTrader client behavior."""

    def __init__(self, api_client):
        self.api_client = api_client

    async def place_market_order(self, intent: OrderIntent) -> OrderAck:
        result = await self.api_client.place_market_order(
            direction=intent.direction,
            lot_size=intent.lot_size,
            stop_loss_price=intent.stop_loss_price,
            take_profit_price=intent.take_profit_price,
        )
        return normalise_market_order_result(result)


def normalise_market_order_result(result: Any) -> OrderAck:
    if result is None:
        return OrderAck(status="rejected", reason="NO_RESULT", raw_result=result)
    if isinstance(result, str):
        return OrderAck(status="filled", position_id=str(result), raw_result=result)
    if isinstance(result, dict):
        status = str(result.get("status") or "").lower()
        order_id = str(result.get("order_id")) if result.get("order_id") is not None else None
        position_id = str(result.get("position_id")) if result.get("position_id") is not None else None
        reason = result.get("reason") or result.get("error") or result.get("error_code")
        if status in {"accepted", "filled", "rejected"}:
            return OrderAck(
                status=status,
                order_id=order_id,
                position_id=position_id,
                reason=str(reason) if reason else None,
                raw_result=result,
            )
        return OrderAck(status="rejected", order_id=order_id, position_id=position_id, reason="UNKNOWN_STATUS", raw_result=result)
    return OrderAck(status="rejected", reason="UNSUPPORTED_RESULT", raw_result=result)
