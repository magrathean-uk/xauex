"""Write an XAUEX trading signal to cmd.json."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def write_signal(signal: dict, output_path: str) -> None:
    """Write an XAUEX signal to cmd.json using an atomic replace."""
    kill_switch = False
    existing: dict = {}
    try:
        with open(output_path, 'r', encoding='utf-8') as handle:
            existing = json.load(handle)
            kill_switch = bool(existing.get('kill_switch', False))
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {}

    cmd = {
        'schema_version': 2,
        'generated_at_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'kill_switch': kill_switch,
        'xauex_signal': signal,
    }
    if 'notes' in existing:
        cmd['notes'] = existing['notes']

    tmp_path = output_path + '.tmp'
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    with open(tmp_path, 'w', encoding='utf-8') as handle:
        json.dump(cmd, handle, indent=2)
    os.replace(tmp_path, output_path)

    logger.info(
        '[WRITER] Signal written to %s: %s %s confidence=%.2f',
        output_path,
        signal.get('symbol', '?'),
        signal.get('action', '?'),
        float(signal.get('confidence', 0.0) or 0.0),
    )
