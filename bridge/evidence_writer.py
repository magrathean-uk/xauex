"""Persist compact predictor evidence for the dashboard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_evidence_pack(
    *,
    output_path: Path,
    context_summary: str,
    recent_runs: list[dict[str, Any]],
    weights: dict[str, Any] | None = None,
    price_features: dict[str, Any] | None = None,
    prediction_mode: str = 'direct',
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'prediction_mode': prediction_mode,
        'context_summary': context_summary,
        'recent_runs': recent_runs[-5:],
        'weights': weights or {},
        'price_features': price_features or {},
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding='utf-8')
