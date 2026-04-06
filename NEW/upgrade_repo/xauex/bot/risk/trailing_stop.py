"""
Trailing stop manager.

Two-phase SL management for open XAUUSD positions:

Phase 1 — Breakeven:
    When unrealised profit >= 1× risk_amount, move SL to entry price.
    This eliminates all downside risk on the position.

Phase 2 — Trail:
    Once SL is at or beyond entry (breakeven triggered), begin trailing.
    The SL trails at (current_profit - 1× risk_amount), maintaining a
    minimum 1R cushion from current price.
    Equivalently: SL = current_price - sl_distance (LONG) or
                  SL = current_price + sl_distance (SHORT),
    where sl_distance = risk_amount / (lot_size_oz * volume_lots).

Hard rules:
    - Never move SL further from entry (would violate hard rule)
    - Return None if no change is needed
"""

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TrailingResult:
    """Result from evaluate_trailing_stop."""
    new_sl: float
    reason: str  # "breakeven" | "trail"


def evaluate_trailing_stop(
    direction: str,          # "LONG" or "SHORT"
    entry_price: float,
    current_price: float,
    current_sl: float,
    volume_lots: float,      # in standard lots (e.g. 0.05)
    contract_size_oz: float, # e.g. 100 for XAUUSD
    risk_amount: float,      # dollar amount risked on this trade
) -> Optional[TrailingResult]:
    """
    Compute a new SL if one should be applied, else return None.

    Callers MUST verify that the returned new_sl improves the position
    (does not move SL away from entry) before sending to broker.
    This function never violates the hard rule internally — it always
    returns None or a tighter SL — but callers should double-check.

    Args:
        direction:       "LONG" or "SHORT"
        entry_price:     Fill price of the position
        current_price:   Latest mid price
        current_sl:      Current stop loss level (absolute price)
        volume_lots:     Position volume in standard lots
        contract_size_oz: Lots-to-oz multiplier (100 for XAUUSD)
        risk_amount:     Total dollars risked = balance × risk_percent / 100

    Returns:
        TrailingResult(new_sl, reason) or None (no action required).
    """
    if volume_lots <= 0 or contract_size_oz <= 0 or risk_amount <= 0:
        return None

    # Unrealised P&L in dollars
    if direction == "LONG":
        unrealised = (current_price - entry_price) * volume_lots * contract_size_oz
    else:
        unrealised = (entry_price - current_price) * volume_lots * contract_size_oz

    if unrealised <= 0:
        return None  # in drawdown — never touch SL

    # SL distance per-unit in price
    sl_distance = risk_amount / (volume_lots * contract_size_oz)

    if direction == "LONG":
        if unrealised >= risk_amount:
            # Phase 2: trailing SL at (current_price - sl_distance)
            trail_sl = current_price - sl_distance
            if trail_sl > current_sl:
                return TrailingResult(new_sl=round(trail_sl, 2), reason="trail")
            # Phase 1: fallback — move SL to breakeven if that's still an improvement
            if entry_price > current_sl:
                return TrailingResult(new_sl=round(entry_price, 2), reason="breakeven")
        return None

    else:  # SHORT — SL moves down
        if unrealised >= risk_amount:
            trail_sl = current_price + sl_distance
            if trail_sl < current_sl:
                return TrailingResult(new_sl=round(trail_sl, 2), reason="trail")
            if entry_price < current_sl:
                return TrailingResult(new_sl=round(entry_price, 2), reason="breakeven")
        return None
