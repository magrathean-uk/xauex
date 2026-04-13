"""Optional CME FedWatch adapter using the official API when configured."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

import httpx

from xauex.signal.config import SignalConfig

logger = logging.getLogger(__name__)


def fetch_fedwatch_snapshot(*, config: SignalConfig) -> dict[str, Any]:
    if not config.cme_fedwatch_api_url:
        return {
            'status': 'unconfigured',
            'available': False,
            'source': 'cme_fedwatch_api',
            'summary': 'CME FedWatch API is not configured.',
        }

    headers = {'User-Agent': config.source_user_agent, 'Accept': 'application/json'}
    key = config.cme_fedwatch_api_key
    header_name = config.cme_fedwatch_api_key_header.strip() or 'Authorization'
    if key:
        if header_name.lower() == 'authorization' and not key.lower().startswith('bearer '):
            headers[header_name] = f'Bearer {key}'
        else:
            headers[header_name] = key

    try:
        response = httpx.get(
            config.cme_fedwatch_api_url,
            headers=headers,
            follow_redirects=True,
            timeout=config.source_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # pragma: no cover - network/provider dependent
        logger.warning('[FEDWATCH] API fetch failed: %s', exc)
        return {
            'status': 'unavailable',
            'available': False,
            'source': 'cme_fedwatch_api',
            'summary': f'CME FedWatch API request failed: {exc}',
        }

    try:
        snapshot = _normalize_fedwatch_payload(payload, fetched_at=datetime.now(timezone.utc))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning('[FEDWATCH] API payload parse failed: %s', exc)
        return {
            'status': 'unavailable',
            'available': False,
            'source': 'cme_fedwatch_api',
            'summary': f'CME FedWatch payload parse failed: {exc}',
        }
    snapshot['source'] = 'cme_fedwatch_api'
    return snapshot


def _normalize_fedwatch_payload(payload: dict[str, Any], *, fetched_at: datetime) -> dict[str, Any]:
    meetings = payload.get('meetings') or payload.get('data') or payload.get('nextMeetings') or []
    if isinstance(meetings, dict):
        meetings = meetings.get('meetings') or meetings.get('items') or [meetings]
    if not isinstance(meetings, list) or not meetings:
        raise ValueError('No meetings found in FedWatch payload')

    meeting = None
    for candidate in meetings:
        distribution = _extract_distribution(candidate)
        if distribution:
            meeting = dict(candidate)
            meeting['_distribution'] = distribution
            break
    if meeting is None:
        raise ValueError('No meeting probabilities found in FedWatch payload')

    distribution = meeting['_distribution']
    best_bucket = max(distribution, key=lambda row: float(row.get('probability', 0.0) or 0.0))
    cut_probability = round(sum(float(row.get('probability', 0.0) or 0.0) for row in distribution if float(row.get('change_bps', 0.0) or 0.0) < 0), 4)
    hold_probability = round(sum(float(row.get('probability', 0.0) or 0.0) for row in distribution if float(row.get('change_bps', 0.0) or 0.0) == 0), 4)
    hike_probability = round(sum(float(row.get('probability', 0.0) or 0.0) for row in distribution if float(row.get('change_bps', 0.0) or 0.0) > 0), 4)
    expected_change_bps = round(
        sum(float(row.get('change_bps', 0.0) or 0.0) * float(row.get('probability', 0.0) or 0.0) for row in distribution),
        2,
    )
    if expected_change_bps < 0:
        bias = 'BUY'
    elif expected_change_bps > 0:
        bias = 'SELL'
    else:
        bias = 'NEUTRAL'

    meeting_date = (
        meeting.get('meetingDate')
        or meeting.get('meeting_date')
        or meeting.get('date')
        or meeting.get('name')
        or ''
    )
    current_target = meeting.get('currentTargetRate') or meeting.get('current_target_rate') or {}
    current_target_range = None
    if isinstance(current_target, dict):
        lower = current_target.get('lower')
        upper = current_target.get('upper')
        if lower is not None and upper is not None:
            current_target_range = f'{lower}-{upper}'

    summary = (
        f'FedWatch next meeting {meeting_date} implies cut {cut_probability:.0%}, '
        f'hold {hold_probability:.0%}, hike {hike_probability:.0%}; expected change {expected_change_bps:+.0f}bp.'
    )
    return {
        'status': 'available',
        'available': True,
        'meeting_date': str(meeting_date),
        'current_target_range': current_target_range,
        'cut_probability': cut_probability,
        'hold_probability': hold_probability,
        'hike_probability': hike_probability,
        'expected_change_bps': expected_change_bps,
        'bias': bias,
        'summary': summary,
        'top_bucket': {
            'label': str(best_bucket.get('label', '') or ''),
            'change_bps': float(best_bucket.get('change_bps', 0.0) or 0.0),
            'probability': float(best_bucket.get('probability', 0.0) or 0.0),
        },
        'probabilities': distribution,
        'fetched_at_utc': fetched_at.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    }


def _extract_distribution(meeting: dict[str, Any]) -> list[dict[str, Any]]:
    raw = (
        meeting.get('distribution')
        or meeting.get('probabilities')
        or meeting.get('targetRateProbabilities')
        or meeting.get('target_rate_probabilities')
        or []
    )
    if isinstance(raw, dict):
        raw = raw.get('items') or raw.get('probabilities') or []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = (
            item.get('label')
            or item.get('name')
            or item.get('range')
            or item.get('targetRateRange')
            or item.get('target_rate_range')
            or ''
        )
        probability = item.get('probability')
        if probability is None:
            probability = item.get('prob')
        if probability is None:
            probability = item.get('percent')
        if probability is None:
            continue
        probability = float(probability)
        if probability > 1:
            probability = probability / 100.0
        change_bps = item.get('change_bps')
        if change_bps is None:
            change_bps = item.get('changeBps')
        if change_bps is None:
            change_bps = item.get('move')
        if change_bps is None:
            change_bps = 0.0
        out.append({
            'label': str(label),
            'change_bps': float(change_bps),
            'probability': round(probability, 4),
        })
    return out
