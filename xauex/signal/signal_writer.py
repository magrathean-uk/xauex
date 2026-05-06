"""Write an XAUEX trading signal to cmd.json."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from xauex.shared.safe_io import JsonLoadError, atomic_write_json, safe_load_json

logger = logging.getLogger(__name__)


def write_signal(signal: dict, output_path: str) -> None:
    """Write an XAUEX signal to cmd.json using an atomic replace."""
    kill_switch = False
    existing: dict = {}
    try:
        loaded = safe_load_json(output_path, default={}, max_bytes=512 * 1024)
        existing = loaded if isinstance(loaded, dict) else {}
        kill_switch = bool(existing.get('kill_switch', False))
    except JsonLoadError:
        existing = {}

    cmd = {
        'schema_version': 2,
        'generated_at_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'kill_switch': kill_switch,
        'xauex_signal': signal,
    }
    if 'notes' in existing:
        cmd['notes'] = existing['notes']

    atomic_write_json(output_path, cmd, mode=0o600)

    logger.info(
        '[WRITER] Signal written to %s: %s %s confidence=%.2f',
        output_path,
        signal.get('symbol', '?'),
        signal.get('action', '?'),
        float(signal.get('confidence', 0.0) or 0.0),
    )
