"""Typed runtime contracts for execution-critical XAUEX payloads."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping


class ContractValidationError(ValueError):
    """Raised when runtime payloads fail strict shape validation."""


def _parse_utc(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ContractValidationError(f"{field_name} is required")
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError as exc:
        raise ContractValidationError(f"{field_name} must be an ISO UTC timestamp") from exc
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _float(value: Any, field_name: str, *, default: float | None = None) -> float:
    if value in (None, "") and default is not None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ContractValidationError(f"{field_name} must be a number") from exc


@dataclass(frozen=True)
class XauexSignal:
    symbol: str
    action: str
    confidence: float
    timestamp_utc: str
    stop_loss_distance: float = 0.0
    take_profit_distance: float = 0.0
    confirm_status: str = "PENDING"
    confirm_reason: str = "WAITING_FOR_CONFIRM"
    window_label: str = "current"
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "XauexSignal":
        if not isinstance(payload, Mapping):
            raise ContractValidationError("xauex_signal must be an object")
        symbol = str(payload.get("symbol") or "XAUUSD").strip().upper()
        action = str(payload.get("action") or "HOLD").strip().upper()
        if action not in {"BUY", "SELL", "HOLD"}:
            raise ContractValidationError("signal.action must be BUY, SELL, or HOLD")
        confidence = _float(payload.get("confidence", 0.0), "signal.confidence", default=0.0)
        if not 0.0 <= confidence <= 1.0:
            raise ContractValidationError("signal.confidence must be between 0 and 1")
        timestamp = _parse_utc(payload.get("timestamp_utc"), "signal.timestamp_utc")
        sl = _float(payload.get("stop_loss_distance", payload.get("stop_loss_usd", 0.0)), "signal.stop_loss_distance", default=0.0)
        tp = _float(payload.get("take_profit_distance", payload.get("take_profit_usd", 0.0)), "signal.take_profit_distance", default=0.0)
        if action in {"BUY", "SELL"} and (sl <= 0 or tp <= 0):
            raise ContractValidationError("directional signals require positive stop and take-profit distances")
        confirm_status = str(payload.get("confirm_status") or "PENDING").strip().upper()
        if confirm_status not in {"PENDING", "CONFIRMED", "SKIP", "REJECTED", "WAITING"}:
            confirm_status = "PENDING"
        return cls(
            symbol=symbol,
            action=action,
            confidence=round(confidence, 4),
            timestamp_utc=timestamp,
            stop_loss_distance=round(max(0.0, sl), 4),
            take_profit_distance=round(max(0.0, tp), 4),
            confirm_status=confirm_status,
            confirm_reason=str(payload.get("confirm_reason") or "WAITING_FOR_CONFIRM"),
            window_label=str(payload.get("window_label") or "current"),
            raw=dict(payload),
        )

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.raw)
        out.update(
            {
                "symbol": self.symbol,
                "action": self.action,
                "confidence": self.confidence,
                "timestamp_utc": self.timestamp_utc,
                "stop_loss_distance": self.stop_loss_distance,
                "take_profit_distance": self.take_profit_distance,
                "confirm_status": self.confirm_status,
                "confirm_reason": self.confirm_reason,
                "window_label": self.window_label,
            }
        )
        return out


@dataclass(frozen=True)
class CommandBundle:
    generated_at_utc: str
    kill_switch: bool
    xauex_signal: XauexSignal
    schema_version: int = 2
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "CommandBundle":
        if not isinstance(payload, Mapping):
            raise ContractValidationError("command bundle must be an object")
        schema_version = int(payload.get("schema_version", 2) or 2)
        if schema_version != 2:
            raise ContractValidationError("unsupported command bundle schema_version")
        signal_payload = payload.get("xauex_signal")
        signal = XauexSignal.from_mapping(signal_payload if isinstance(signal_payload, Mapping) else {})
        return cls(
            schema_version=schema_version,
            generated_at_utc=_parse_utc(payload.get("generated_at_utc"), "generated_at_utc"),
            kill_switch=bool(payload.get("kill_switch", False)),
            xauex_signal=signal,
            raw=dict(payload),
        )

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.raw)
        out.update(
            {
                "schema_version": self.schema_version,
                "generated_at_utc": self.generated_at_utc,
                "kill_switch": self.kill_switch,
                "xauex_signal": self.xauex_signal.to_dict(),
            }
        )
        return out


@dataclass(frozen=True)
class ManualOpenCommand:
    action: str
    lot_size: float
    stop_loss: float
    take_profit: float
    command: str = "open"

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ManualOpenCommand":
        action = str(payload.get("action") or payload.get("side") or "").upper()
        if action not in {"BUY", "SELL"}:
            raise ContractValidationError("manual open action must be BUY or SELL")
        lot_size = _float(payload.get("lot_size", payload.get("lot")), "lot_size")
        stop_loss = _float(payload.get("stop_loss"), "stop_loss")
        take_profit = _float(payload.get("take_profit"), "take_profit")
        if lot_size <= 0:
            raise ContractValidationError("lot_size must be positive")
        return cls(action=action, lot_size=lot_size, stop_loss=stop_loss, take_profit=take_profit)

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": "open",
            "action": self.action,
            "lot_size": self.lot_size,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
        }


@dataclass(frozen=True)
class ManualCloseCommand:
    position_id: str
    command: str = "close"

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ManualCloseCommand":
        position_id = str(payload.get("position_id") or payload.get("trade_id") or "").strip()
        if not position_id:
            raise ContractValidationError("position_id is required for manual close")
        return cls(position_id=position_id)

    def to_dict(self) -> dict[str, Any]:
        return {"command": "close", "position_id": self.position_id}


def parse_manual_command(payload: Mapping[str, Any]) -> ManualOpenCommand | ManualCloseCommand:
    command = str(payload.get("command") or "open").lower()
    if command == "close":
        return ManualCloseCommand.from_mapping(payload)
    if command == "open":
        return ManualOpenCommand.from_mapping(payload)
    raise ContractValidationError("manual command must be open or close")
