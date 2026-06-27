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
    market_snapshot: dict[str, Any] | None = None,
    input_freshness: dict[str, Any] | None = None,
    dsa_sidecar: dict[str, Any] | None = None,
    validator: dict[str, Any] | None = None,
    estimated_total_cost_usd: float | None = None,
    prediction_mode: str = 'direct',
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'prediction_mode': prediction_mode,
        'context_summary': context_summary,
        'recent_runs': recent_runs[-5:],
        'weights': weights or {},
        'price_features': price_features or {},
        'market_snapshot': market_snapshot or {},
        'input_freshness': input_freshness or {},
        'dsa_sidecar': dsa_sidecar or {},
        'validator': validator or {},
        'estimated_total_cost_usd': estimated_total_cost_usd,
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding='utf-8')
