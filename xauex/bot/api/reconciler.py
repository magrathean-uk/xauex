"""
Reconciler — startup position sync between broker state and local PositionManager.

Called once on connect (and again on reconnect) to ensure our in-memory state
matches what the broker reports. Discrepancies are logged; the broker is always
authoritative.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xauex.bot.api.client import ApiClient
    from xauex.bot.state.position_manager import PositionManager

logger = logging.getLogger(__name__)


async def reconcile_positions(
    api: "ApiClient",
    position_manager: "PositionManager",
) -> None:
    """
    Fetch broker positions via ProtoOAReconcileReq and sync PositionManager.

    - Positions in broker but not in local state → add them
    - Positions in local state but not in broker → remove them (closed externally)
    - Positions in both → verify direction/volume (log warning on mismatch)

    After reconciliation, PositionManager reflects the live broker state.
    """
    broker_positions, pending_orders = await api.reconcile()
    local_ids = set(position_manager.get_position_ids())
    broker_ids = {p.position_id for p in broker_positions}

    added = removed = synced = 0

    # Add/update from broker
    for pos in broker_positions:
        if pos.position_id not in local_ids:
            position_manager.add_position(pos)
            logger.warning(
                "[RECONCILE] Added orphan position %s from broker (direction=%s, lots=%.4f)",
                pos.position_id, pos.direction, pos.volume,
            )
            added += 1
        else:
            local = position_manager.get_position(pos.position_id)
            local_vol = getattr(local, 'lot_size', getattr(local, 'volume', 0.0))
            if local and (local.direction != pos.direction or abs(local_vol - pos.volume) > 1e-6):
                logger.warning(
                    "[RECONCILE] Mismatch on %s: local=%s %.4f lots vs broker=%s %.4f lots",
                    pos.position_id,
                    local.direction, local_vol,
                    pos.direction, pos.volume,
                )
            synced += 1

    # Remove from local state if broker doesn't know about them
    for pid in list(local_ids):
        if pid not in broker_ids:
            position_manager.remove_position(pid)
            logger.warning(
                "[RECONCILE] Removed ghost position %s — not found on broker", pid
            )
            removed += 1

    logger.info(
        "[RECONCILE] Done: %d synced, %d added, %d removed. "
        "Pending orders at broker: %d",
        synced, added, removed, len(pending_orders),
    )
