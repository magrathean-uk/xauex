"""Lot size calculation with strict rounding rules."""

import logging
import math
from typing import Optional

from config import Config
from bot.api.models import SymbolSpec

logger = logging.getLogger(__name__)


def calculate_lot_size(
    account_balance: float,
    entry_price: float,
    stop_loss_price: float,
    symbol_spec: SymbolSpec,
    current_spread_usd: float,
    config: Config,
    min_sl_distance: Optional[float] = None,
    max_sl_distance: Optional[float] = None,
) -> Optional[float]:
    """
    Calculate lot size for a trade based on 1% account risk.

    Returns lot size (float) or None if the trade must be skipped.
    Caller must log the reason for skip.

    Rules (hard):
    - SL distance must be in [SL_MIN_DOLLARS, SL_MAX_DOLLARS]
    - SL distance must exceed 3× current spread
    - Floor to volume_step — never round up
    - If lot < volume_min → return None (NEVER round up to minimum)
    - Cap at volume_max
    """
    sl_distance = abs(entry_price - stop_loss_price)

    # Pre-calculation validation
    min_sl = config.sl_min_dollars if min_sl_distance is None else min_sl_distance
    max_sl = config.sl_max_dollars if max_sl_distance is None else max_sl_distance

    if sl_distance < min_sl:
        logger.info(f"[RISK] Lot skipped: SL distance {sl_distance:.2f} < min {min_sl}")
        return None

    if sl_distance > max_sl:
        logger.info(f"[RISK] Lot skipped: SL distance {sl_distance:.2f} > max {max_sl}")
        return None

    if sl_distance < current_spread_usd * 3.0:
        logger.info(f"[RISK] Lot skipped: SL distance {sl_distance:.2f} < 3× spread {current_spread_usd:.2f}")
        return None

    risk_amount = account_balance * (config.risk_percent / 100.0)

    # XAUUSD: 1 lot = lot_size oz (typically 100). P&L per lot per $1 = lot_size.
    # Use symbol_spec.lot_size to derive the multiplier.
    lot_size = risk_amount / (sl_distance * symbol_spec.lot_size)

    # Floor to volume step — never round up
    step = symbol_spec.volume_step
    lot_size = math.floor(lot_size / step) * step

    # Skip if below minimum — do NOT round up
    if lot_size < symbol_spec.volume_min:
        logger.info(
            f"[RISK] Lot skipped: calculated {lot_size:.5f} < volume_min {symbol_spec.volume_min}"
        )
        return None

    # Cap at maximum
    lot_size = min(lot_size, symbol_spec.volume_max)
    lot_size = min(lot_size, getattr(config, "max_lot_size", symbol_spec.volume_max))

    if lot_size < symbol_spec.volume_min:
        logger.info(
            f"[RISK] Lot skipped after cap: calculated {lot_size:.5f} < volume_min {symbol_spec.volume_min}"
        )
        return None

    return round(lot_size, 5)
