"""Shared FRED fetch helpers: bounded lookback, retry, optional API key.

Full-history fredgraph.csv downloads (decades of rows to read one value) were
the main FRED timeout/throttle trigger, so every request is bounded to a
recent observation window. An API key switches to the official endpoint,
which is rate-limited per key instead of per IP.
"""

from __future__ import annotations

from csv import DictReader
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import logging
import os
import random
import time

import httpx

logger = logging.getLogger(__name__)

FRED_LOOKBACK_DAYS = 45


def fred_api_key() -> str | None:
    value = str(os.getenv('FRED_API_KEY', '') or '').strip()
    return value or None


def fred_fetch_retries() -> int:
    try:
        return max(0, int(os.getenv('FRED_FETCH_RETRIES', '1') or '1'))
    except ValueError:
        return 1


def fred_request_url(
    series_id: str,
    *,
    api_key: str | None = None,
    lookback_days: int = FRED_LOOKBACK_DAYS,
    now_utc: datetime | None = None,
) -> str:
    now_value = now_utc or datetime.now(timezone.utc)
    start_date = (now_value - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
    if api_key:
        return (
            'https://api.stlouisfed.org/fred/series/observations'
            f'?series_id={series_id}&api_key={api_key}&file_type=json'
            f'&observation_start={start_date}'
        )
    return f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start_date}'


def parse_fred_rows(text: str, series_id: str, *, api_payload: bool) -> list[tuple[str, float]]:
    rows: list[tuple[str, float]] = []
    if api_payload:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f'Invalid FRED API payload for {series_id}') from exc
        observations = payload.get('observations') if isinstance(payload, dict) else None
        for observation in observations or []:
            if not isinstance(observation, dict):
                continue
            raw_value = str(observation.get('value', '') or '').strip()
            date_value = str(observation.get('date', '') or '').strip()
            if not raw_value or raw_value == '.':
                continue
            try:
                rows.append((date_value, float(raw_value)))
            except ValueError:
                continue
        return rows

    reader = DictReader(StringIO(text))
    for row in reader:
        raw_value = str(row.get(series_id, '') or '').strip()
        date_value = str(row.get('DATE', row.get('observation_date', '')) or '').strip()
        if not raw_value or raw_value == '.':
            continue
        try:
            rows.append((date_value, float(raw_value)))
        except ValueError:
            continue
    return rows


def fetch_fred_rows(client: httpx.Client, series_id: str) -> list[tuple[str, float]]:
    """Fetch recent observations for a series, retrying with jittered backoff."""
    api_key = fred_api_key()
    url = fred_request_url(series_id, api_key=api_key)
    attempts = 1 + fred_fetch_retries()
    last_error: Exception | None = None
    for attempt in range(attempts):
        if attempt:
            time.sleep(random.uniform(2.0, 5.0))
        try:
            response = client.get(url)
            response.raise_for_status()
            return parse_fred_rows(response.text, series_id, api_payload=bool(api_key))
        except Exception as exc:
            last_error = exc
            logger.warning(
                '[FRED] Fetch attempt %d/%d failed for %s: %s',
                attempt + 1,
                attempts,
                series_id,
                exc,
            )
    raise RuntimeError(f'FRED fetch failed for {series_id}: {last_error}') from last_error
