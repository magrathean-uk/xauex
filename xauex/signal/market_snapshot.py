"""Structured public market-series snapshot for XAUEX signal decisions."""

from __future__ import annotations

from csv import DictReader
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from datetime import datetime, timezone
from io import StringIO
import logging
from typing import Any

import httpx

from xauex.signal.assets import AssetProfile
from xauex.signal.config import SignalConfig
from xauex.signal.fedwatch import fetch_fedwatch_snapshot
from xauex.signal.policy_context import fetch_policy_context

logger = logging.getLogger(__name__)

_ACTIVE_WINDOWS = {'morning', 'midday', 'us_open'}
_MARKET_SNAPSHOT_WARNING_AGE_SECONDS = 3 * 24 * 3600
_MARKET_SNAPSHOT_BLOCK_AGE_SECONDS = 7 * 24 * 3600
_MARKET_SNAPSHOT_BLOCK_STALE_SERIES_COUNT = 2
_MARKET_SNAPSHOT_WARNING_MISSING_COUNT = 1
_MARKET_SNAPSHOT_BLOCK_MISSING_COUNT = 2

_FRED_SERIES: dict[str, dict[str, str]] = {
    'usd_broad_index': {
        'series_id': 'DTWEXBGS',
        'label': 'Trade-weighted USD broad index',
        'category': 'usd',
    },
    'us2y_yield': {
        'series_id': 'DGS2',
        'label': 'US 2Y Treasury yield',
        'category': 'yield',
    },
    'us10y_yield': {
        'series_id': 'DGS10',
        'label': 'US 10Y Treasury yield',
        'category': 'yield',
    },
    'us10y_real_yield': {
        'series_id': 'DFII10',
        'label': 'US 10Y real yield',
        'category': 'real_yield',
    },
    'vix': {
        'series_id': 'VIXCLS',
        'label': 'CBOE VIX',
        'category': 'risk',
    },
}

_EVENT_FLAG_SOURCE_IDS = {
    'fed_event_recent': {'fed_press_all', 'fed_speeches_testimony'},
    'cpi_release_recent': {'bls_cpi_rss'},
    'nfp_release_recent': {'bls_employment_situation'},
    'pce_release_recent': {'bea_personal_income'},
}


def build_market_snapshot(
    *,
    asset: AssetProfile,
    config: SignalConfig,
    context_items: list[dict[str, Any]] | list[Any] | None = None,
    window_label: str = 'current',
) -> dict[str, Any]:
    if asset.symbol != 'XAUUSD':
        policy_context = {
            'status': 'unsupported',
            'available': False,
            'summary': 'Official Fed policy context is only evaluated for XAUUSD.',
            'source': 'fed_fomc_calendar+fred',
        }
        freshness = _assess_market_snapshot_freshness(
            market_snapshot_age_seconds=None,
            missing_series_count=0,
            window_label=window_label,
        )
        freshness['market_snapshot_state'] = 'warning'
        freshness['hard_blocker'] = False
        freshness['summary'] = 'Structured market snapshot is unavailable. Unsupported asset; market snapshot is not used.'
        freshness['policy_context_state'] = str(policy_context.get('status', 'unknown') or 'unknown')
        freshness['policy_context_summary'] = str(policy_context.get('summary', '') or '')
        return {
            'series': {},
            'fedwatch': {
                'status': 'unsupported',
                'available': False,
                'summary': 'FedWatch is only evaluated for XAUUSD.',
            },
            'event_flags': _event_flags(context_items or []),
            'input_freshness': freshness,
            'overall_bias': 'NEUTRAL',
            'missing_series': [],
            'policy_context': policy_context,
        }

    with ThreadPoolExecutor(max_workers=len(_FRED_SERIES) + 2) as executor:
        policy_context_future = executor.submit(fetch_policy_context, config=config)
        fedwatch_future = executor.submit(fetch_fedwatch_snapshot, config=config)
        series_futures = {
            executor.submit(_fetch_fred_series_payload, config, key, meta): key
            for key, meta in _FRED_SERIES.items()
        }

        event_flags = _event_flags(context_items or [])
        series_results: dict[str, tuple[dict[str, str], dict[str, Any] | None, Exception | None]] = {}
        for future in as_completed(series_futures):
            key, meta, latest, exc = future.result()
            series_results[key] = (meta, latest, exc)

        series_payload: dict[str, Any] = {}
        missing_series: list[str] = []
        ages: list[float] = []
        block_stale_series_count = 0
        for key, meta in _FRED_SERIES.items():
            result = series_results.get(key)
            if result is None:
                missing_series.append(key)
                continue
            result_meta, latest, exc = result
            if exc is not None or latest is None:
                logger.warning('[MARKET] Failed to fetch %s (%s): %s', key, result_meta['series_id'], exc)
                missing_series.append(key)
                continue
            ages.append(latest['age_seconds'])
            if latest['age_seconds'] >= _MARKET_SNAPSHOT_BLOCK_AGE_SECONDS:
                block_stale_series_count += 1
            series_payload[key] = {
                'label': result_meta['label'],
                'series_id': result_meta['series_id'],
                'value': latest['value'],
                'previous_value': latest['previous_value'],
                'change_1d': latest['change_1d'],
                'date_utc': latest['date_utc'],
                'age_seconds': latest['age_seconds'],
                'bias': _series_bias(asset.symbol, key, latest['change_1d']),
            }

        fedwatch = fedwatch_future.result()
        policy_context = policy_context_future.result()
    freshness = _assess_market_snapshot_freshness(
        market_snapshot_age_seconds=int(max(ages)) if ages else None,
        missing_series_count=len(missing_series),
        stale_block_series_count=block_stale_series_count,
        window_label=window_label,
    )
    freshness['fedwatch_state'] = str(fedwatch.get('status', 'unknown') or 'unknown')
    freshness['fedwatch_summary'] = str(fedwatch.get('summary', '') or '')
    freshness['policy_context_state'] = str(policy_context.get('status', 'unknown') or 'unknown')
    freshness['policy_context_summary'] = str(policy_context.get('summary', '') or '')

    # FOMC decision-day blackout. Gold reacts violently to Fed statements, dots,
    # and press conferences. Even when the London morning is hours before the
    # 18:00-19:00 UTC release, positioning ahead of the event produces large
    # whipsaws that our signal cannot meaningfully forecast. Likewise the
    # morning after a decision is still digesting the statement. Treat both as
    # hard blockers so the parser returns HOLD and the bot declines the slot.
    fomc_window = str(policy_context.get('fomc_window_state', '') or '').lower()
    freshness['fomc_window_state'] = fomc_window
    if fomc_window in ('today', 'recent'):
        freshness['state'] = 'blocked'
        freshness['market_snapshot_state'] = 'blocked'
        freshness['hard_blocker'] = True
        fomc_note = (
            'FOMC decision window (today) - blocking entries.'
            if fomc_window == 'today'
            else 'Day after FOMC decision - blocking entries while market digests.'
        )
        existing_summary = str(freshness.get('summary', '') or '').strip()
        freshness['summary'] = f'{fomc_note} {existing_summary}'.strip()

    return {
        'series': series_payload,
        'fedwatch': fedwatch,
        'event_flags': event_flags,
        'input_freshness': freshness,
        'overall_bias': _overall_bias(series_payload),
        'missing_series': missing_series,
        'policy_context': policy_context,
    }


def _fetch_fred_series_payload(
    config: SignalConfig,
    key: str,
    meta: dict[str, str],
) -> tuple[str, dict[str, str], dict[str, Any] | None, Exception | None]:
    client = httpx.Client(
        timeout=config.source_timeout_seconds,
        headers={'User-Agent': config.source_user_agent},
        follow_redirects=True,
    )
    try:
        latest = _fetch_fred_series(client, meta['series_id'])
        return key, meta, latest, None
    except Exception as exc:  # pragma: no cover - network dependent
        return key, meta, None, exc
    finally:
        client.close()


def _assess_market_snapshot_freshness(
    *,
    market_snapshot_age_seconds: int | None,
    missing_series_count: int,
    stale_block_series_count: int = 1,
    window_label: str,
) -> dict[str, Any]:
    active_window = window_label in _ACTIVE_WINDOWS
    state = 'fresh'
    hard_blocker = False
    notes: list[str] = []

    if market_snapshot_age_seconds is None:
        state = 'blocked' if active_window else 'warning'
        hard_blocker = active_window
        notes.append('Structured market snapshot is unavailable.')
    elif market_snapshot_age_seconds >= _MARKET_SNAPSHOT_BLOCK_AGE_SECONDS:
        if active_window and stale_block_series_count >= _MARKET_SNAPSHOT_BLOCK_STALE_SERIES_COUNT:
            state = 'blocked'
            hard_blocker = True
            notes.append(
                f'Structured market snapshot is stale at {market_snapshot_age_seconds}s old during the {window_label} window.'
            )
        else:
            state = 'warning'
            notes.append(
                f'Structured market snapshot is stale at {market_snapshot_age_seconds}s old, but only {stale_block_series_count} series exceed the hard block threshold.'
            )
    elif market_snapshot_age_seconds >= _MARKET_SNAPSHOT_WARNING_AGE_SECONDS:
        state = 'warning'
        notes.append(f'Structured market snapshot is stale at {market_snapshot_age_seconds}s old.')

    if missing_series_count >= _MARKET_SNAPSHOT_BLOCK_MISSING_COUNT and active_window:
        state = 'blocked'
        hard_blocker = True
        notes.append(f'{missing_series_count} structured market series are missing during the {window_label} window.')
    elif missing_series_count >= _MARKET_SNAPSHOT_WARNING_MISSING_COUNT:
        if state == 'fresh':
            state = 'warning'
        notes.append(f'{missing_series_count} structured market series are missing.')

    if not notes:
        notes.append('Structured market snapshot is fresh enough for decision support.')

    return {
        'window_label': window_label,
        'market_snapshot_age_seconds': market_snapshot_age_seconds,
        'missing_series_count': missing_series_count,
        'stale_block_series_count': stale_block_series_count,
        'market_snapshot_state': state,
        'hard_blocker': hard_blocker,
        'summary': ' '.join(notes),
    }


def _fetch_fred_series(client: httpx.Client, series_id: str) -> dict[str, Any]:
    url = f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}'
    response = client.get(url)
    response.raise_for_status()
    reader = DictReader(StringIO(response.text))
    rows = []
    for row in reader:
        value = str(row.get(series_id, '') or '').strip()
        date_value = str(row.get('DATE', row.get('observation_date', '')) or '').strip()
        if not value or value == '.':
            continue
        try:
            rows.append((date_value, float(value)))
        except ValueError:
            continue
    if len(rows) < 2:
        raise RuntimeError(f'Insufficient FRED rows for {series_id}')
    date_value, latest = rows[-1]
    _, previous = rows[-2]
    observation = datetime.strptime(date_value, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    age_seconds = max(0.0, (datetime.now(timezone.utc) - observation).total_seconds())
    return {
        'value': round(latest, 4),
        'previous_value': round(previous, 4),
        'change_1d': round(latest - previous, 4),
        'date_utc': observation.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'age_seconds': age_seconds,
    }


def _event_flags(context_items: list[dict[str, Any]] | list[Any]) -> dict[str, bool]:
    source_ids: set[str] = set()
    for item in context_items:
        if isinstance(item, dict):
            source_id = str(item.get('source_id', '') or '').strip()
        else:
            source_id = str(getattr(item, 'source_id', '') or '').strip()
        if source_id:
            source_ids.add(source_id)
    return {
        flag: bool(source_ids.intersection(source_set))
        for flag, source_set in _EVENT_FLAG_SOURCE_IDS.items()
    }


def _series_bias(asset_symbol: str, key: str, change_1d: float) -> str:
    if asset_symbol != 'XAUUSD':
        return 'NEUTRAL'
    if key in {'usd_broad_index', 'us2y_yield', 'us10y_yield', 'us10y_real_yield'}:
        if change_1d > 0:
            return 'SELL'
        if change_1d < 0:
            return 'BUY'
        return 'NEUTRAL'
    if key == 'vix':
        if change_1d > 0:
            return 'BUY'
        if change_1d < 0:
            return 'SELL'
    return 'NEUTRAL'


def _overall_bias(series_payload: dict[str, dict[str, Any]]) -> str:
    score = 0
    for row in series_payload.values():
        bias = str(row.get('bias', 'NEUTRAL')).upper()
        if bias == 'BUY':
            score += 1
        elif bias == 'SELL':
            score -= 1
    if score > 0:
        return 'BUY'
    if score < 0:
        return 'SELL'
    return 'NEUTRAL'
