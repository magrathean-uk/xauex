"""Shared freshness policy for signal generation and execution."""

from __future__ import annotations

from typing import Any, Mapping


DAILY_PUBLISHING_MAX_AGE_SECONDS = 3 * 24 * 3600
DAILY_PUBLISHING_MAX_BUSINESS_AGE_DAYS = 2


def freshness_requires_risk_reduction(freshness: Mapping[str, Any]) -> bool:
    """Return whether structured freshness evidence should reduce trade risk."""
    if bool(freshness.get("hard_blocker")):
        return True

    if any(
        _coerce_int(freshness.get(field)) > 0
        for field in (
            "missing_series_count",
            "stale_block_series_count",
            "cache_fallback_series_count",
            "fed_h15_fallback_series_count",
        )
    ):
        return True

    business_age = _coerce_float(freshness.get("daily_publishing_max_business_age_days"))
    if business_age is not None and business_age > DAILY_PUBLISHING_MAX_BUSINESS_AGE_DAYS:
        return True

    if business_age is None:
        calendar_age = _coerce_float(freshness.get("daily_publishing_max_age_seconds"))
        if calendar_age is not None and calendar_age > DAILY_PUBLISHING_MAX_AGE_SECONDS:
            return True

    if str(freshness.get("fomc_window_state") or "").strip().lower() == "recent":
        return True

    return str(freshness.get("market_snapshot_state") or "").strip().lower() in {
        "warning",
        "stale",
        "blocked",
    }


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
