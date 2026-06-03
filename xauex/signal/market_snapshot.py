"""Structured public market-series snapshot for XAUEX signal decisions."""

from __future__ import annotations

from csv import DictReader
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import logging
from pathlib import Path
from typing import Any

import httpx

from xauex.signal.assets import AssetProfile
from xauex.signal.cftc_cot import fetch_cot_snapshot
from xauex.signal.config import SignalConfig
from xauex.signal.fedwatch import fetch_fedwatch_snapshot
from xauex.signal.policy_context import fetch_policy_context
from xauex.signal.polymarket import fetch_polymarket_snapshot

logger = logging.getLogger(__name__)

_ACTIVE_WINDOWS = {'morning', 'midday', 'us_open'}
_MARKET_SNAPSHOT_WARNING_AGE_SECONDS = 3 * 24 * 3600
_MARKET_SNAPSHOT_BLOCK_AGE_SECONDS = 7 * 24 * 3600
_MARKET_SNAPSHOT_BLOCK_STALE_SERIES_COUNT = 3
_MARKET_SNAPSHOT_WARNING_MISSING_COUNT = 1
_MARKET_SNAPSHOT_BLOCK_MISSING_COUNT = 2

_FRED_SERIES: dict[str, dict[str, str]] = {
    'usd_broad_index': {
        'series_id': 'DTWEXBGS',
        'label': 'Trade-weighted USD broad index',
        'category': 'usd',
        'stale_blocks_live_window': 'false',
    },
    'usd_major_index': {
        'series_id': 'DTWEXAFEGS',
        'label': 'Trade-weighted USD major-currencies index',
        'category': 'usd',
        'stale_blocks_live_window': 'false',
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
    'us5y_breakeven_inflation': {
        'series_id': 'T5YIE',
        'label': 'US 5Y breakeven inflation expectation',
        'category': 'inflation',
    },
    'us10y_breakeven_inflation': {
        'series_id': 'T10YIE',
        'label': 'US 10Y breakeven inflation expectation',
        'category': 'inflation',
    },
    'vix': {
        'series_id': 'VIXCLS',
        'label': 'CBOE VIX',
        'category': 'risk',
    },
    'wti_oil': {
        'series_id': 'DCOILWTICO',
        'label': 'WTI crude oil spot',
        'category': 'commodity',
        'stale_blocks_live_window': 'false',
    },
    'btc_usd': {
        'series_id': 'CBBTCUSD',
        'label': 'Bitcoin USD (Coinbase)',
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
            'cot': {
                'status': 'unsupported',
                'available': False,
                'summary': 'CFTC COT positioning is only evaluated for XAUUSD.',
            },
            'polymarket': {
                'status': 'unsupported',
                'available': False,
                'source': 'polymarket_gamma+clob',
                'summary': 'Polymarket prediction-market context is only evaluated for XAUUSD.',
            },
            'event_flags': _event_flags(context_items or []),
            'input_freshness': freshness,
            'overall_bias': 'NEUTRAL',
            'missing_series': [],
            'policy_context': policy_context,
        }

    with ThreadPoolExecutor(max_workers=len(_FRED_SERIES) + 4) as executor:
        policy_context_future = executor.submit(fetch_policy_context, config=config)
        fedwatch_future = executor.submit(fetch_fedwatch_snapshot, config=config)
        cot_future = executor.submit(fetch_cot_snapshot, config=config)
        polymarket_future = executor.submit(fetch_polymarket_snapshot, config=config, asset=asset)
        series_futures = {
            executor.submit(_fetch_fred_series_payload, config, key, meta): key
            for key, meta in _FRED_SERIES.items()
        }

        event_flags = _event_flags(context_items or [])
        series_results: dict[str, tuple[dict[str, str], dict[str, Any] | None, Exception | None]] = {}
        for future in as_completed(series_futures):
            key, meta, latest, exc = future.result()
            series_results[key] = (meta, latest, exc)

        archived_series_cache: dict[str, dict[str, Any]] | None = None
        series_payload: dict[str, Any] = {}
        missing_series: list[str] = []
        cache_fallback_series_count = 0
        cache_fallback_archives: set[str] = set()
        ages: list[float] = []
        daily_ages: list[float] = []
        daily_business_ages: list[int] = []
        block_stale_series_count = 0
        for key, meta in _FRED_SERIES.items():
            result = series_results.get(key)
            if result is None:
                missing_series.append(key)
                continue
            result_meta, latest, exc = result
            if exc is not None or latest is None:
                if archived_series_cache is None:
                    archived_series_cache = _load_archived_fred_series_cache(config)
                cached_latest = archived_series_cache.get(key) if archived_series_cache else None
                if cached_latest is None:
                    logger.warning('[MARKET] Failed to fetch %s (%s): %s', key, result_meta['series_id'], exc)
                    missing_series.append(key)
                    continue
                latest = cached_latest
                cache_fallback_series_count += 1
                cache_source_archive = str(latest.get('cache_source_archive') or '')
                if cache_source_archive:
                    cache_fallback_archives.add(cache_source_archive)
                logger.warning(
                    '[MARKET] Failed to fetch %s (%s): %s; using archived fallback from %s',
                    key,
                    result_meta['series_id'],
                    exc,
                    cache_source_archive or 'unknown archive',
                )
            ages.append(latest['age_seconds'])
            stale_blocks_live_window = result_meta.get('stale_blocks_live_window', 'true') != 'false'
            business_age_days = _latest_business_age_days(latest)
            if stale_blocks_live_window:
                # Series we expect to publish daily (yields, breakevens, VIX,
                # BTC). Track their max age separately so the hard-stale
                # guard ignores normal weekly-publish lag on slow series.
                daily_ages.append(latest['age_seconds'])
                if business_age_days is not None:
                    daily_business_ages.append(business_age_days)
            if latest['age_seconds'] >= _MARKET_SNAPSHOT_BLOCK_AGE_SECONDS and stale_blocks_live_window:
                block_stale_series_count += 1
            row_payload = {
                'label': result_meta['label'],
                'series_id': result_meta['series_id'],
                'value': latest['value'],
                'previous_value': latest['previous_value'],
                'change_1d': latest['change_1d'],
                'date_utc': latest['date_utc'],
                'age_seconds': latest['age_seconds'],
                'bias': _series_bias(asset.symbol, key, latest['change_1d']),
            }
            if business_age_days is not None:
                row_payload['business_age_days'] = business_age_days
            if latest.get('cache_fallback'):
                row_payload['cache_fallback'] = True
                if latest.get('cache_source_archive'):
                    row_payload['cache_source_archive'] = latest['cache_source_archive']
            series_payload[key] = row_payload

        fedwatch = fedwatch_future.result()
        policy_context = policy_context_future.result()
        cot = cot_future.result()
        polymarket = polymarket_future.result()
    freshness = _assess_market_snapshot_freshness(
        market_snapshot_age_seconds=int(max(ages)) if ages else None,
        daily_publishing_max_age_seconds=int(max(daily_ages)) if daily_ages else None,
        daily_publishing_max_business_age_days=int(max(daily_business_ages)) if daily_business_ages else None,
        missing_series_count=len(missing_series),
        stale_block_series_count=block_stale_series_count,
        cache_fallback_series_count=cache_fallback_series_count,
        cache_fallback_archives=sorted(cache_fallback_archives),
        window_label=window_label,
    )
    freshness['fedwatch_state'] = str(fedwatch.get('status', 'unknown') or 'unknown')
    freshness['fedwatch_summary'] = str(fedwatch.get('summary', '') or '')
    freshness['policy_context_state'] = str(policy_context.get('status', 'unknown') or 'unknown')
    freshness['policy_context_summary'] = str(policy_context.get('summary', '') or '')
    freshness['polymarket_state'] = str(polymarket.get('status', 'unknown') or 'unknown')
    freshness['polymarket_summary'] = str(polymarket.get('summary', '') or '')

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
        'cot': cot,
        'polymarket': polymarket,
        'event_flags': event_flags,
        'input_freshness': freshness,
        'overall_bias': _overall_bias(series_payload, cot=cot, polymarket=polymarket),
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


def _load_archived_fred_series_cache(config: SignalConfig, *, max_archives: int = 50) -> dict[str, dict[str, Any]]:
    archive_root = Path(str(getattr(config, 'archive_dir', '') or ''))
    if not archive_root.exists():
        return {}
    try:
        archive_dirs = sorted(
            (path for path in archive_root.iterdir() if path.is_dir()),
            key=lambda path: path.name,
            reverse=True,
        )
    except OSError as exc:
        logger.warning('[MARKET] Failed to scan archived market snapshots in %s: %s', archive_root, exc)
        return {}

    now_utc = datetime.now(timezone.utc)
    cached: dict[str, dict[str, Any]] = {}
    for archive_dir in archive_dirs[:max_archives]:
        payload_path = archive_dir / 'prediction_payload.json'
        try:
            data = json.loads(payload_path.read_text(encoding='utf-8'))
        except Exception:
            continue
        market_snapshot = data.get('market_snapshot') if isinstance(data, dict) else None
        raw_series = market_snapshot.get('series') if isinstance(market_snapshot, dict) else None
        if not isinstance(raw_series, dict) or not raw_series:
            continue
        for key, meta in _FRED_SERIES.items():
            if key in cached:
                continue
            row = raw_series.get(key)
            normalized = _normalize_archived_series_row(
                key=key,
                meta=meta,
                row=row,
                archive_dir=archive_dir,
                now_utc=now_utc,
            )
            if normalized is not None:
                cached[key] = normalized
        if len(cached) == len(_FRED_SERIES):
            break
    if cached:
        logger.info('[MARKET] Loaded %d archived FRED fallback series from %s', len(cached), archive_root)
    return cached


def _normalize_archived_series_row(
    *,
    key: str,
    meta: dict[str, str],
    row: Any,
    archive_dir: Path,
    now_utc: datetime,
) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    series_id = str(row.get('series_id', '') or '').strip()
    if series_id and series_id != meta['series_id']:
        return None
    observed = _parse_observation_datetime(str(row.get('date_utc', '') or ''))
    if observed is None:
        return None
    value = _coerce_float(row.get('value'))
    previous_value = _coerce_float(row.get('previous_value'))
    change_1d = _coerce_float(row.get('change_1d'))
    if value is None:
        return None
    if previous_value is None:
        previous_value = value
    if change_1d is None:
        change_1d = value - previous_value
    age_seconds = max(0.0, (now_utc - observed).total_seconds())
    date_utc = observed.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    return {
        'value': round(value, 4),
        'previous_value': round(previous_value, 4),
        'change_1d': round(change_1d, 4),
        'date_utc': date_utc,
        'age_seconds': age_seconds,
        'business_age_days': _business_days_since_observation(date_utc, now_utc=now_utc),
        'cache_fallback': True,
        'cache_source_archive': str(archive_dir),
        'cache_series_key': key,
    }


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _assess_market_snapshot_freshness(
    *,
    market_snapshot_age_seconds: int | None,
    daily_publishing_max_age_seconds: int | None = None,
    daily_publishing_max_business_age_days: int | None = None,
    missing_series_count: int,
    stale_block_series_count: int = 1,
    cache_fallback_series_count: int = 0,
    cache_fallback_archives: list[str] | None = None,
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

    if cache_fallback_series_count > 0:
        if state == 'fresh':
            state = 'warning'
        notes.append(
            f'{cache_fallback_series_count} structured market series reused from archived fallback after source fetch failures.'
        )

    if not notes:
        notes.append('Structured market snapshot is fresh enough for decision support.')

    return {
        'window_label': window_label,
        'market_snapshot_age_seconds': market_snapshot_age_seconds,
        'daily_publishing_max_age_seconds': daily_publishing_max_age_seconds,
        'daily_publishing_max_business_age_days': daily_publishing_max_business_age_days,
        'missing_series_count': missing_series_count,
        'stale_block_series_count': stale_block_series_count,
        'cache_fallback_series_count': cache_fallback_series_count,
        'cache_fallback_archives': cache_fallback_archives or [],
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
    now_utc = datetime.now(timezone.utc)
    age_seconds = max(0.0, (now_utc - observation).total_seconds())
    return {
        'value': round(latest, 4),
        'previous_value': round(previous, 4),
        'change_1d': round(latest - previous, 4),
        'date_utc': observation.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'age_seconds': age_seconds,
        'business_age_days': _business_days_since_observation(
            observation.strftime('%Y-%m-%dT%H:%M:%SZ'),
            now_utc=now_utc,
        ),
    }


def _parse_observation_datetime(text: str) -> datetime | None:
    value = str(text or '').strip()
    if not value:
        return None
    try:
        if value.endswith('Z'):
            observed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        elif 'T' in value:
            observed = datetime.fromisoformat(value)
        else:
            observed = datetime.strptime(value[:10], '%Y-%m-%d').replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return observed.astimezone(timezone.utc)


def _latest_business_age_days(latest: dict[str, Any]) -> int | None:
    raw_age = latest.get('business_age_days')
    if raw_age is not None:
        try:
            return max(0, int(float(raw_age)))
        except (TypeError, ValueError):
            return None
    date_utc = str(latest.get('date_utc', '') or '').strip()
    if not date_utc:
        return None
    return _business_days_since_observation(date_utc)


def _business_days_since_observation(date_utc: str, now_utc: datetime | None = None) -> int:
    text = str(date_utc or '').strip()
    if not text:
        return 0
    try:
        if text.endswith('Z'):
            observed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        elif 'T' in text:
            observed = datetime.fromisoformat(text)
        else:
            observed = datetime.strptime(text[:10], '%Y-%m-%d').replace(tzinfo=timezone.utc)
    except ValueError:
        return 0
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    observed_date = observed.astimezone(timezone.utc).date()
    current_date = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc).date()
    if current_date <= observed_date:
        return 0

    elapsed_days = (current_date - observed_date).days
    full_weeks, remainder_days = divmod(elapsed_days, 7)
    business_days = full_weeks * 5
    for offset in range(1, remainder_days + 1):
        if (observed_date + timedelta(days=offset)).weekday() < 5:
            business_days += 1
    return business_days


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
    # USD strength and yields are inverse to gold.
    if key in {
        'usd_broad_index',
        'usd_major_index',
        'us2y_yield',
        'us10y_yield',
        'us10y_real_yield',
    }:
        if change_1d > 0:
            return 'SELL'
        if change_1d < 0:
            return 'BUY'
        return 'NEUTRAL'
    # Rising inflation expectations are gold-positive (gold as inflation hedge).
    if key in {'us5y_breakeven_inflation', 'us10y_breakeven_inflation'}:
        if change_1d > 0:
            return 'BUY'
        if change_1d < 0:
            return 'SELL'
        return 'NEUTRAL'
    # Rising risk (VIX) and oil (inflation proxy) are gold-positive.
    if key in {'vix', 'wti_oil'}:
        if change_1d > 0:
            return 'BUY'
        if change_1d < 0:
            return 'SELL'
        return 'NEUTRAL'
    # BTC: regime-dependent correlation with gold; let the LLM judge.
    if key == 'btc_usd':
        return 'NEUTRAL'
    return 'NEUTRAL'


def _overall_bias(
    series_payload: dict[str, dict[str, Any]],
    *,
    cot: dict[str, Any] | None = None,
    polymarket: dict[str, Any] | None = None,
) -> str:
    score = 0
    for row in series_payload.values():
        bias = str(row.get('bias', 'NEUTRAL')).upper()
        if bias == 'BUY':
            score += 1
        elif bias == 'SELL':
            score -= 1
    if cot and cot.get('available') and cot.get('extreme_positioning'):
        cot_bias = str(cot.get('bias', 'NEUTRAL')).upper()
        if cot_bias == 'BUY':
            score += 1
        elif cot_bias == 'SELL':
            score -= 1
    if polymarket and polymarket.get('available'):
        polymarket_bias = str(polymarket.get('overall_bias', 'NEUTRAL')).upper()
        if polymarket_bias == 'BUY':
            score += 2
        elif polymarket_bias == 'SELL':
            score -= 2
    if score > 0:
        return 'BUY'
    if score < 0:
        return 'SELL'
    return 'NEUTRAL'
