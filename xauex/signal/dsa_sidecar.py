"""Read-only Daily Stock Analysis sidecar integration.

The sidecar is treated as research evidence for stock-transition work. This
module deliberately does not write XAUEX command files or bypass the live
signal parser, confirmation, kill switch, or risk gates.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

import httpx


_DEFAULT_BASE_URL = "http://127.0.0.1:8090/api/v1"
_DIRECTIONAL_ACTIONS = {"buy": "BUY", "sell": "SELL"}


@dataclass(frozen=True)
class DsaSidecarConfig:
    enabled: bool = False
    base_url: str = _DEFAULT_BASE_URL
    symbol: str = "AAPL"
    admin_cookie: str = ""
    timeout_seconds: float = 5.0
    min_confidence: float = 0.65
    shadow_only: bool = True

    @classmethod
    def from_env(cls) -> "DsaSidecarConfig":
        return cls(
            enabled=_env_bool("XAUEX_DSA_ENABLED", default=False),
            base_url=(os.getenv("XAUEX_DSA_BASE_URL") or _DEFAULT_BASE_URL).strip().rstrip("/"),
            symbol=(os.getenv("XAUEX_DSA_SYMBOL") or "AAPL").strip().upper(),
            admin_cookie=(os.getenv("XAUEX_DSA_ADMIN_COOKIE") or "").strip(),
            timeout_seconds=_env_float("XAUEX_DSA_TIMEOUT_SECONDS", default=5.0, lower=0.5, upper=60.0),
            min_confidence=_env_float("XAUEX_DSA_MIN_CONFIDENCE", default=0.65, lower=0.0, upper=1.0),
            shadow_only=_env_bool("XAUEX_DSA_SHADOW_ONLY", default=True),
        )


@dataclass(frozen=True)
class DsaDecision:
    stock_code: str
    action: str
    confidence: float
    entry_low: float | None = None
    entry_high: float | None = None
    stop_loss: float | None = None
    target_price: float | None = None
    reason: str = ""
    status: str = ""
    created_at: str = ""
    expires_at: str = ""
    raw: dict[str, Any] | None = None

    @classmethod
    def from_api_item(cls, item: dict[str, Any]) -> "DsaDecision":
        return cls(
            stock_code=str(item.get("stock_code") or item.get("symbol") or "").strip().upper(),
            action=str(item.get("action") or "").strip().lower(),
            confidence=_safe_float(item.get("confidence"), default=0.0),
            entry_low=_optional_float(item.get("entry_low")),
            entry_high=_optional_float(item.get("entry_high")),
            stop_loss=_optional_float(item.get("stop_loss")),
            target_price=_optional_float(item.get("target_price", item.get("take_profit"))),
            reason=str(item.get("reason") or "").strip(),
            status=str(item.get("status") or "").strip().lower(),
            created_at=str(item.get("created_at") or "").strip(),
            expires_at=str(item.get("expires_at") or "").strip(),
            raw=dict(item),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {key: value for key, value in payload.items() if value not in (None, "", {})}


class DsaSidecarClient:
    def __init__(self, config: DsaSidecarConfig, *, client: httpx.Client | None = None):
        self.config = config
        self._client = client

    def fetch_latest_decision(self) -> DsaDecision | None:
        url = f"{self.config.base_url}/decision-signals/latest/{self.config.symbol}"
        headers = _auth_headers(self.config.admin_cookie)
        if self._client is not None:
            response = self._client.get(url, headers=headers)
        else:
            with httpx.Client(timeout=self.config.timeout_seconds, follow_redirects=True) as client:
                response = client.get(url, headers=headers)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        item = _select_decision_item(response.json())
        return DsaDecision.from_api_item(item) if item else None


def build_dsa_research_snapshot(
    config: DsaSidecarConfig | None = None,
    *,
    client: httpx.Client | None = None,
    current_price: float | None = None,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    cfg = config or DsaSidecarConfig.from_env()
    if not cfg.enabled:
        return {
            "enabled": False,
            "status": "disabled",
            "symbol": cfg.symbol,
            "shadow_signal": _hold_shadow(cfg.symbol, "DSA sidecar is disabled."),
        }

    try:
        decision = DsaSidecarClient(cfg, client=client).fetch_latest_decision()
    except Exception as exc:
        return {
            "enabled": True,
            "status": "unavailable",
            "symbol": cfg.symbol,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "shadow_signal": _hold_shadow(cfg.symbol, "DSA sidecar is unavailable."),
        }

    if decision is None:
        return {
            "enabled": True,
            "status": "no_decision",
            "symbol": cfg.symbol,
            "shadow_signal": _hold_shadow(cfg.symbol, "DSA returned no latest decision."),
        }

    shadow = map_dsa_decision_to_shadow_signal(
        decision,
        symbol=cfg.symbol,
        min_confidence=cfg.min_confidence,
        current_price=current_price,
        reference_time=reference_time,
    )
    shadow["shadow_only"] = bool(cfg.shadow_only)
    return {
        "enabled": True,
        "status": "ok",
        "symbol": cfg.symbol,
        "decision": decision.to_dict(),
        "shadow_signal": shadow,
    }


def map_dsa_decision_to_shadow_signal(
    decision: DsaDecision | None,
    *,
    symbol: str,
    min_confidence: float,
    current_price: float | None = None,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    if decision is None:
        return _hold_shadow(symbol, "DSA decision is missing.")

    symbol_text = str(symbol or "").strip().upper()
    if decision.stock_code and decision.stock_code.upper() != symbol_text:
        return _hold_shadow(symbol_text, f"DSA symbol {decision.stock_code} does not match {symbol_text}.")

    action_key = str(decision.action or "").strip().lower()
    if action_key not in _DIRECTIONAL_ACTIONS:
        return _hold_shadow(symbol_text, f"DSA action {action_key or 'unknown'} is advisory-only.")

    confidence = max(0.0, min(1.0, float(decision.confidence or 0.0)))
    if confidence < min_confidence:
        return _hold_shadow(symbol_text, f"DSA confidence {confidence:.2f} is below threshold {min_confidence:.2f}.")

    if _is_expired(decision.expires_at, reference_time):
        return _hold_shadow(symbol_text, "DSA decision is expired.")

    price = _optional_float(current_price)
    if price is None or price <= 0:
        return _hold_shadow(symbol_text, "DSA current price is unavailable for geometry validation.")

    stop = decision.stop_loss
    target = decision.target_price
    if stop is None or target is None or stop <= 0 or target <= 0:
        return _hold_shadow(symbol_text, "DSA stop loss and target price are required for geometry validation.")

    action = _DIRECTIONAL_ACTIONS[action_key]
    if action == "BUY":
        if stop >= price or target <= price:
            return _hold_shadow(symbol_text, "DSA BUY geometry is invalid for current price.")
        stop_distance = price - stop
        take_profit_distance = target - price
    else:
        if stop <= price or target >= price:
            return _hold_shadow(symbol_text, "DSA SELL geometry is invalid for current price.")
        stop_distance = stop - price
        take_profit_distance = price - target

    return {
        "symbol": symbol_text,
        "action": action,
        "confidence": round(confidence, 4),
        "reason": decision.reason or "DSA directional decision passed shadow validation.",
        "shadow_only": True,
        "stop_loss_distance": round(stop_distance, 4),
        "take_profit_distance": round(take_profit_distance, 4),
        "source": "daily_stock_analysis",
    }


def _select_decision_item(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        item = payload.get("item")
        if isinstance(item, dict):
            return item
        items = payload.get("items")
        if isinstance(items, list):
            return _first_mapping(items)
        data = payload.get("data")
        if isinstance(data, dict):
            nested_item = data.get("item")
            if isinstance(nested_item, dict):
                return nested_item
            nested_items = data.get("items")
            if isinstance(nested_items, list):
                return _first_mapping(nested_items)
    if isinstance(payload, list):
        return _first_mapping(payload)
    return None


def _first_mapping(items: list[Any]) -> dict[str, Any] | None:
    for item in items:
        if isinstance(item, dict):
            return item
    return None


def _auth_headers(admin_cookie: str) -> dict[str, str]:
    if not admin_cookie:
        return {}
    cookie = admin_cookie if "=" in admin_cookie else f"admin_session={admin_cookie}"
    return {"Cookie": cookie}


def _hold_shadow(symbol: str, reason: str) -> dict[str, Any]:
    return {
        "symbol": str(symbol or "").strip().upper(),
        "action": "HOLD",
        "confidence": 0.0,
        "reason": reason,
        "shadow_only": True,
        "source": "daily_stock_analysis",
    }


def _is_expired(value: str, reference_time: datetime | None = None) -> bool:
    if not value:
        return False
    try:
        expires_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    now = reference_time or datetime.now(timezone.utc)
    return expires_at.astimezone(timezone.utc) <= now.astimezone(timezone.utc)


def _optional_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any, *, default: float) -> float:
    parsed = _optional_float(value)
    return default if parsed is None else parsed


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, *, default: float, lower: float, upper: float) -> float:
    raw = os.getenv(name)
    try:
        value = float(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        value = default
    return max(lower, min(upper, value))
